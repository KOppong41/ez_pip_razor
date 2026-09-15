"""Disposable broker adapter for replaying the actual Django scalper pipeline.

Only this child process replaces the clock and external I/O providers. The
strategy, allocation, decision, fanout, pretrade risk and exit services run as
they do live, against their own in-memory database.
"""
import json
import io
import os
import sys
from collections import Counter
from contextlib import ExitStack, redirect_stdout
from datetime import datetime, timedelta
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

ZERO = Decimal("0")


def model_values(model, values):
    return {key: model._meta.get_field(key).to_python(value) for key, value in values.items()}


def completed_context(bars, timeframe, now, source_minutes):
    """Aggregate only complete, contiguous HTF buckets; never invent candles."""
    from execution.services.higher_timeframe_context import FRAME_MINUTES
    minutes = FRAME_MINUTES[timeframe]
    if minutes < source_minutes or minutes % source_minutes:
        return []
    groups = {}
    for bar in bars:
        stamp = bar["time"]
        bucket = datetime.fromtimestamp((int(stamp.timestamp()) // (minutes * 60)) * minutes * 60, tz=stamp.tzinfo)
        if bucket + timedelta(minutes=minutes) <= now:
            groups.setdefault(bucket, []).append(bar)
    result = []
    for bucket, group in groups.items():
        expected = [bucket + timedelta(minutes=index * source_minutes) for index in range(minutes // source_minutes)]
        if [bar["time"] for bar in group] != expected:
            continue
        result.append({"time": bucket, "open": group[0]["open"], "close": group[-1]["close"],
                       "high": max(bar["high"] for bar in group), "low": min(bar["low"] for bar in group),
                       "tick_volume": sum(bar["tick_volume"] for bar in group)})
    return result


class ReplayBroker:
    def __init__(self, bars, config, snapshot):
        from execution.services.historical_backtest import TIMEFRAMES
        self.bars, self.config = bars, config
        self.step_minutes = TIMEFRAMES[config["timeframe"]]
        self.index = 0
        self.now = bars[0]["time"]
        self.bid = bars[0]["open"]
        self.spread = config["spread_points"] * config["point_size"]
        self.slippage = config["slippage_points"] * config["point_size"]
        self.balance = config["initial_balance"]
        self.trades, self.entries, self.rejections = [], {}, Counter()
        self.moves = 0
        self.symbol_info = SimpleNamespace(
            visible=True, trade_mode=4, point=config["point_size"], digits=config["digits"],
            volume_min=config["volume_min"], volume_max=config["volume_max"], volume_step=config["volume_step"],
            trade_stops_level=config["stops_level_points"], trade_freeze_level=ZERO,
            trade_tick_size=config["point_size"], trade_tick_value=config["point_size"] * config["contract_size"],
            trade_tick_value_loss=config["point_size"] * config["contract_size"], trade_contract_size=config["contract_size"],
        )

    def tick_for_account(self, *args):
        return SimpleNamespace(bid=self.bid, ask=self.bid + self.spread, last=self.bid, time=self.now.timestamp())

    def symbol_info_for_account(self, *args):
        return self.symbol_info

    def positions_for_account(self, *args):
        from execution.models import BrokerPosition
        return tuple(SimpleNamespace(ticket=p.broker_position_ticket, time=p.opened_at.timestamp(), volume=p.volume, sl=p.sl)
                     for p in BrokerPosition.objects.filter(status="open"))

    def history_deals_for_account(self, *args):
        # Risk days are initialized explicitly at each observed day boundary.
        return ()

    def account_info_for_account(self, *args):
        from execution.models import BrokerPosition
        positions = list(BrokerPosition.objects.filter(status="open"))
        pnl = sum((self.calc_profit_for_account(None, p.side, p.symbol, p.volume, p.open_price,
                    self.bid if p.side == "buy" else self.bid + self.spread) for p in positions), ZERO)
        margin = sum((p.volume * self.config["margin_per_lot"] for p in positions), ZERO)
        equity = self.balance + pnl
        return SimpleNamespace(trade_mode=0, margin_mode=self.config["account_margin_mode"], balance=self.balance, equity=equity, margin=margin,
                               margin_free=equity - margin, margin_level=equity / margin * 100 if margin else ZERO,
                               currency=self.config["currency"])

    def calc_profit_for_account(self, account, side, symbol, volume, entry, stop):
        return (Decimal(str(stop)) - Decimal(str(entry))) * (1 if side == "buy" else -1) * Decimal(str(volume)) * self.config["contract_size"]

    def calc_margin_for_account(self, account, side, symbol, volume, entry):
        return Decimal(str(volume)) * self.config["margin_per_lot"]

    def candles(self, broker_account, symbol, timeframe, n_bars):
        if timeframe == self.config["timeframe"]:
            return self.bars[max(0, self.index - n_bars):self.index]
        from execution.services.higher_timeframe_context import FRAME_MINUTES
        history = self.bars[max(0, self.index - (n_bars + 1) * FRAME_MINUTES[timeframe] // self.step_minutes):self.index]
        return completed_context(history, timeframe, self.now, self.step_minutes)[-n_bars:]

    def constraints(self, *args):
        from execution.services.brokers import BrokerSymbolConstraints
        info = self.symbol_info
        return BrokerSymbolConstraints(min_lot=info.volume_min, max_lot=info.volume_max, lot_step=info.volume_step,
                                       point=info.point, tick_size=info.trade_tick_size, digits=info.digits,
                                       stops_level_points=info.trade_stops_level, freeze_level_points=ZERO)

    def modify_broker_position(self, position, *, sl=None, **kwargs):
        if sl is not None:
            position.sl = Decimal(str(sl)).quantize(Decimal("1").scaleb(-self.config["digits"]))
            position.save(update_fields=["sl"])
            self.moves += 1
        return True

    def submit(self, order, *args, **kwargs):
        from execution.models import BrokerPosition
        from execution.services.live_risk import RiskRejected, enforce_pretrade_risk
        if order.intent == "exit":
            pos = BrokerPosition.objects.get(broker_position_ticket=order.broker_position_ticket, status="open")
            quote = self.bid if pos.side == "buy" else self.bid + self.spread
            return self.close(pos, quote, "partial_tp1" if order.client_order_id.startswith("close:tp1|") else "managed_exit", order=order)
        try:
            result = enforce_pretrade_risk(order, self, self.tick_for_account(), self.symbol_info,
                                          self.account_info_for_account(), broker_positions=self.positions_for_account())
        except RiskRejected as exc:
            self.rejections[exc.code] += 1
            order.status, order.risk_reserved_at = "rejected", None
            order.broker_response = exc.as_dict()
            order.save(update_fields=["status", "risk_reserved_at", "broker_response"])
            return None
        sign = 1 if order.side == "buy" else -1
        fill = result.entry_price + sign * self.slippage
        position = BrokerPosition.objects.create(
            broker_account=order.broker_account, bot=order.bot, owner=order.owner,
            originating_order=order, broker_position_ticket=order.id, ownership="ez_trade", symbol=order.symbol,
            side=order.side, volume=result.volume, open_price=fill, sl=order.sl, tp=order.tp,
            opened_at=self.now, status="open",
        )
        self.entries[position.id] = {
            "direction": order.side, "signal_time": order.decision.signal.payload.get("generated_at"),
            "entry_time": self.now, "entry_price": fill, "sl": order.sl, "tp": order.tp,
            "score": order.decision.score, "strategy": order.decision.signal.payload.get("strategy"),
            "entry_reason": order.decision.reason, "original_quantity": result.volume,
            "is_opposite_scalp": bool(order.decision.params.get("is_opposite_scalp")),
            "primary_position_id": order.decision.params.get("primary_position_id"),
        }
        self.record(order, result.volume, fill, position.broker_position_ticket)
        return position

    def record(self, order, qty, fill, ticket, *, profit=None, commission=ZERO):
        from execution.services.portfolio import record_fill
        order.status, order.filled_qty, order.remaining_qty = "filled", qty, ZERO
        order.actual_fill_price, order.submitted_at, order.risk_reserved_at = fill, self.now, None
        order.broker_position_ticket = ticket
        order.save()
        record_fill(order, qty, fill, account_balance=self.balance, contract_size=self.config["contract_size"],
                    broker_position_ticket=ticket, broker_profit=profit, commission=-commission)

    def close(self, position, quote, reason, *, order=None):
        from execution.services.orchestrator import create_close_order_for_position
        if order is None:
            order, _ = create_close_order_for_position(position, position.broker_account)
        qty = min(Decimal(str(order.qty)), position.volume)
        sign = 1 if position.side == "buy" else -1
        fill = quote - sign * self.slippage
        profit = self.calc_profit_for_account(None, position.side, position.symbol, qty, position.open_price, fill)
        commission = self.config["commission_per_lot"] * qty
        pnl = profit - commission
        self.balance += pnl
        units = qty * self.config["contract_size"]
        costs = self.spread * units + 2 * self.slippage * units + commission
        self.trades.append({**self.entries[position.id], "position_id": position.id, "quantity": qty,
                            "exit_time": self.now, "exit_price": fill, "reason": reason, "pnl": pnl,
                            "gross_pnl": pnl + costs, "spread_cost": self.spread * units,
                            "slippage_cost": 2 * self.slippage * units, "commission": commission, "balance": self.balance})
        position.volume -= qty
        if position.volume <= 0:
            position.status, position.closed_at = "closed", self.now
        position.save()
        self.record(order, qty, fill, position.broker_position_ticket, profit=profit, commission=commission)
        return order

    def broker_stops(self, bar):
        from execution.models import BrokerPosition
        for pos in BrokerPosition.objects.filter(status="open"):
            buy = pos.side == "buy"
            offset = ZERO if buy else self.spread
            opening, high, low = (bar[key] + offset for key in ("open", "high", "low"))
            stop_gap = opening <= pos.sl if buy else opening >= pos.sl
            target_gap = pos.tp is not None and (opening >= pos.tp if buy else opening <= pos.tp)
            stop_hit = low <= pos.sl if buy else high >= pos.sl
            target_hit = pos.tp is not None and (high >= pos.tp if buy else low <= pos.tp)
            if stop_gap:
                self.close(pos, opening, "stop_gap")
            elif target_gap:
                self.close(pos, pos.tp, "take_profit")
            elif stop_hit and (not target_hit or self.config["same_bar_policy"] == "stop_first"):
                self.close(pos, pos.sl, "stop_loss")
            elif target_hit:
                self.close(pos, pos.tp, "take_profit")


def replay(payload):
    from django.conf import settings
    from django.contrib.auth import get_user_model
    from django.db import connection
    from bots.models import Asset, Bot
    from brokers.models import BrokerAccount
    from execution.models import AccountRiskDay, BrokerPosition, Decision, ExecutionSetting, RiskPolicy, ScalperProfile, ScalperRunLog
    from execution.services.daily_risk import risk_day_window
    from execution.services.historical_backtest import MAX_EQUITY_POINTS, json_safe
    from execution import tasks

    if connection.settings_dict["NAME"] != ":memory:" or not getattr(settings, "REPLAY_ISOLATED", False):
        raise RuntimeError("Replay requires its dedicated in-memory worker database")
    config, dataset = payload["config"], payload["dataset"]
    for key in ("point_size", "quantity", "contract_size", "initial_balance", "spread_points", "slippage_points",
                "commission_per_lot", "volume_min", "volume_max", "volume_step", "margin_per_lot", "stops_level_points"):
        config[key] = Decimal(str(config[key]))
    bars = [{**bar, "time": datetime.fromisoformat(bar["time"]),
             **{key: Decimal(str(bar[key])) for key in ("open", "high", "low", "close")}} for bar in payload["bars"]]
    snap = config["bot_snapshot"]
    user = get_user_model().objects.create(username="isolated-replay")
    asset, _ = Asset.objects.update_or_create(symbol=snap["asset"]["symbol"], defaults=model_values(Asset, snap["asset"]))
    account = BrokerAccount.objects.create(owner=user, name="Isolated replay", broker="mt5", connector="mt5_local",
                                           account_ref="replay", is_active=True, is_verified=True, timezone=snap["broker_timezone"])
    profile = ScalperProfile.objects.create(slug="replay-snapshot", name="Replay snapshot", config=snap["profile"])
    values = model_values(Bot, snap["bot"])
    values.update(status="active", auto_trade=True, paused_until=None, current_loss_streak=0,
                  allocation_start_pnl=ZERO, allocation_started_at=bars[dataset["first_index"]]["time"])
    for key in ("last_htf_bias", "flip_history", "_allocation_guard"):
        values["scalper_params"].pop(key, None)
    bot = Bot.objects.create(owner=user, asset=asset, broker_account=account, scalper_profile=profile, **values)
    policy_values = model_values(RiskPolicy, snap["policy"])
    policy_values.update(entries_enabled=True, emergency_stop=False, equity_high_water=config["initial_balance"], equity_high_water_at=None)
    RiskPolicy.objects.update_or_create(broker_account=account, defaults=policy_values)
    setting_values = model_values(ExecutionSetting, snap["execution_settings"])
    setting_values.update(model_values(ExecutionSetting, snap["runtime"]))
    ExecutionSetting.objects.update_or_create(key="default", defaults=setting_values)
    settings.TESTING = False
    settings.ECONOMIC_CALENDAR_ENABLED = False
    sim = ReplayBroker(bars, config, snap)
    first, last = dataset["first_index"], dataset["last_index"]
    peak, max_dd, max_dd_pct = sim.balance, ZERO, ZERO
    equity = []
    sample = max(1, (last - first + MAX_EQUITY_POINTS) // MAX_EQUITY_POINTS)
    from execution.services.market_hours import get_market_status_for_bot
    from execution.services.runtime_config import clear_runtime_config_cache
    clear_runtime_config_cache()
    with ExitStack() as stack:
        providers = {
            "django.utils.timezone.now": lambda: sim.now,
            "execution.tasks.MT5Connector": lambda: sim,
            "execution.connectors.mt5.MT5Connector": lambda: sim,
            "execution.tasks.get_candles_for_account": sim.candles,
            "execution.tasks.get_broker_symbol_constraints": sim.constraints,
            "execution.services.brokers.get_broker_symbol_constraints": sim.constraints,
            "execution.tasks._queue_or_dispatch_order": sim.submit,
            "execution.services.positions.dispatch_place_order": sim.submit,
            "execution.services.decision.get_price": lambda *args: sim.bid,
            "execution.tasks.get_price": lambda *args: sim.bid,
            "execution.services.psychology._get_broker_balance_decimal": lambda *args: sim.balance,
            "execution.tasks.get_market_status_for_bot": lambda bot, **kwargs: get_market_status_for_bot(bot, now=sim.now, use_mt5_probe=False),
        }
        for target, provider in providers.items():
            stack.enter_context(patch(target, new=provider))
        for index in range(first, last + 1):
            sim.index = index
            bar = bars[index]
            sim.now, sim.bid = bar["time"], bar["open"]
            window = risk_day_window(account, sim.now)
            info = sim.account_info_for_account()
            AccountRiskDay.objects.get_or_create(broker_account=account, risk_date=window.risk_date, defaults={
                "starting_balance": sim.balance, "starting_equity": info.equity, "high_equity": info.equity,
                "first_snapshot_at": sim.now, "baseline_locked": True, "baseline_source": "manual",
            })
            cycle = tasks.run_scalper_engine_for_all_bots.run(timeframe=config["timeframe"], n_bars=config["warmup"])
            # Runner-level gates do not produce a signal or a strategy run log.
            for key, count in cycle.items():
                if key.startswith("skipped_") and count:
                    sim.rejections[key.removeprefix("skipped_")] += count
            sim.broker_stops(bar)
            # Management observes the completed close. New protection only
            # affects subsequent bars, avoiding invented intrabar sequencing.
            sim.now = bar["time"] + timedelta(minutes=sim.step_minutes)
            sim.bid = bar["close"]
            tasks.trail_positions_task.run()
            if index == last:
                for pos in BrokerPosition.objects.filter(status="open"):
                    sim.close(pos, sim.bid if pos.side == "buy" else sim.bid + sim.spread, "end_of_data")
            mark = sim.account_info_for_account().equity
            peak = max(peak, mark)
            dd = peak - mark
            dd_pct = dd / peak * 100 if peak > 0 else ZERO
            max_dd, max_dd_pct = max(max_dd, dd), max(max_dd_pct, dd_pct)
            if (index - first) % sample == 0 or index == last:
                equity.append({"time": sim.now, "balance": sim.balance, "equity": mark, "drawdown_pct": dd_pct})
    for reason in Decision.objects.filter(action="ignore").values_list("reason", flat=True):
        sim.rejections[reason] += 1
    for cycle in ScalperRunLog.objects.values_list("summary", flat=True):
        # Decision rejections are counted above; include earlier scan gates and
        # later allocation/dispatch gates without counting the same decision twice.
        if not cycle.get("decisions") or cycle.get("outcome") in {
            "account_slot_lost", "account_slot_unavailable", "dispatch_rejected",
        }:
            reason = cycle.get("rejection_reason")
            if reason:
                sim.rejections[reason] += 1
    # Group partial fills when calculating trade-level performance.
    pnls = Counter()
    for trade in sim.trades:
        pnls[trade["position_id"]] += trade["pnl"]
    wins, losses = [p for p in pnls.values() if p > 0], [p for p in pnls.values() if p < 0]
    count, net = len(pnls), sim.balance - config["initial_balance"]
    gross_profit, gross_loss = sum(wins, ZERO), -sum(losses, ZERO)
    summary = {
        "trades": count, "exit_fills": len(sim.trades), "wins": len(wins), "losses": len(losses),
        "breakeven": count - len(wins) - len(losses), "win_rate_pct": Decimal(len(wins)) / count * 100 if count else ZERO,
        "net_pnl": net, "return_pct": net / config["initial_balance"] * 100,
        "initial_balance": config["initial_balance"], "ending_balance": sim.balance,
        "gross_profit": gross_profit, "gross_loss": gross_loss,
        "profit_factor": gross_profit / gross_loss if gross_loss else None,
        "max_drawdown": max_dd, "max_drawdown_pct": max_dd_pct, "avg_trade": net / count if count else ZERO,
        "best_trade": max(pnls.values(), default=ZERO), "worst_trade": min(pnls.values(), default=ZERO),
        "evaluated_bars": last - first + 1, "open_signals": Decision.objects.filter(action="open").count(),
        "first_at": bars[first]["time"], "last_at": sim.now, "protection_moves": sim.moves,
        **{key: sum((trade[key] for trade in sim.trades), ZERO) for key in ("spread_cost", "slippage_cost", "commission")},
    }
    from execution.services.overlay_analytics import overlay_report
    overlay = overlay_report([{**trade, "costs": trade["spread_cost"] + trade["slippage_cost"] + trade["commission"]}
                              for trade in sim.trades])
    return json_safe({"summary": summary, "trades": sim.trades, "equity": equity, "opposite_scalp": overlay,
        "skip_reasons": [{"reason": reason, "count": count} for reason, count in sim.rejections.most_common()],
        "assumptions": [
            "Frozen bot, scalper profile, runtime settings and account limits; shared live strategy selection, allocation, decision, fanout, risk and exit services.",
            "The selected bot starts active on an empty simulated account; operational stops, prior loss streaks and cached market context are reset. Other bots and manual positions are not part of this dataset.",
            "Completed bid candles only; entries at the next available open; configured context timeframes are aggregated from completed contiguous candles.",
            "Constant spread, adverse slippage and margin per lot; contract size calculates profit in the selected simulation currency. No historical conversion, swap or broker liquidation model.",
            "Broker SL/TP follow the chosen same-bar policy. Trailing and partial-exit management observes each candle close; updated stops become active on subsequent candles. Tick-by-tick fills cannot be reconstructed from OHLC.",
            "Account risk days start at the first observed quote of each broker day. Price gaps remain in the dataset.",
            "News not simulated: economic-calendar filtering is explicitly disabled for replay because archived events and freshness coverage are unavailable. Live news filtering is unchanged.",
        ]})


def main(*, input_path=None, output_path=None):
    if os.environ.get("EZSCALPER_REPLAY_WORKER") != "1" or os.environ.get("DJANGO_SETTINGS_MODULE") != "config.settings_replay":
        raise RuntimeError("Replay must be launched by the isolated worker")
    # Make broker IPC unavailable even if an unadapted path is accidentally used.
    sys.modules["MetaTrader5"] = None
    import django
    django.setup()
    from django.conf import settings
    from django.core.management import call_command
    settings.REPLAY_ISOLATED = True
    if sys.stderr is None:
        sys.stderr = io.StringIO()
    payload = json.loads(Path(input_path).read_text(encoding="utf-8")) if input_path else json.load(sys.stdin)
    with redirect_stdout(sys.stderr):
        call_command("migrate", verbosity=0, interactive=False)
        with patch("socket.socket.connect", side_effect=RuntimeError("Network I/O is disabled during replay")):
            result = replay(payload)
    if output_path:
        Path(output_path).write_text(json.dumps(result), encoding="utf-8")
    else:
        print(json.dumps(result))


if __name__ == "__main__":
    main()
