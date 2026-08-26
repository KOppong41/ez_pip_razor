import logging
import os
from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone as dt_timezone
from decimal import Decimal
from typing import Callable

from celery import shared_task
from django.conf import settings
from django.db.models import Sum
from django.utils import timezone

from brokers.models import BrokerAccount
from bots.models import STRATEGY_CHOICES
from core.metrics import task_failures_total
from execution.connectors.base import ConnectorError
from execution.connectors.mt5 import MT5Connector, is_mt5_available
from execution.models import (
    AccountSnapshot,
    BrokerPosition,
    Decision,
    Execution,
    Order,
    PnLDaily,
    Position,
    RiskPolicy,
    ScalperRunLog,
    Signal,
)
from execution.services.brokers import dispatch_place_order, get_broker_symbol_constraints
from execution.services.order_guard import GuardInputs, apply_order_guards
from execution.services.decision import make_decision_from_signal
from execution.services.ai_strategy_selector import select_ai_strategies
from execution.services.engine import run_engine_on_candles
from execution.services.fanout import fanout_orders
from execution.services.marketdata import get_candles_for_account
from execution.services.monitor import (
    EarlyExitConfig,
    KillSwitchConfig,
    TrailingConfig,
    should_early_exit,
    apply_trailing,
    close_position_now,
    should_trigger_kill_switch,
    unrealized_pnl,
    manage_scalper_position,
)
from execution.services.prices import get_price
from execution.services.psychology import bot_is_available_for_trading
from execution.services.market_hours import (
    get_market_status_for_bot,
    maybe_pause_bot_for_market,
    maybe_unpause_crypto_for_open_market,
    is_crypto_symbol,
)
from execution.tasks_market_guard import apply_market_guard
from execution.services.scalper_config import build_scalper_config
from execution.services.runtime_config import get_runtime_config
from execution.services.journal import log_journal_event
from execution.services.orchestrator import create_close_order_for_position, update_order_status
from execution.services.portfolio import record_fill
from execution.services.strategies.breakout_retest import (
    BreakoutRetestConfig,
    run_breakout_retest,
)
from execution.services.strategies.doji_breakout import DojiBreakoutConfig, run_doji_breakout
from execution.services.strategies.harami import detect_harami
from execution.services.strategies.momentum_ignition import (
    MomentumIgnitionConfig,
    run_momentum_ignition,
)
from execution.services.strategies.price_action_pinbar import PinBarConfig, run_price_action_pinbar
from execution.services.strategies.range_reversion import RangeReversionConfig, run_range_reversion
from execution.services.strategies.trend_pullback import TrendPullbackConfig, run_trend_pullback
from execution.utils.symbols import canonical_symbol


HTF_MAP = {
    "5m": "30m",
    "15m": "1h",
    "30m": "4h",
    "1h": "4h",
}

def _get_htf(timeframe: str) -> str | None:
    return HTF_MAP.get(timeframe)


logger = logging.getLogger(__name__)


def _queue_or_dispatch_order(order: Order) -> str:
    """Keep every MT5 operation on the single serialized execution queue."""
    if getattr(order.broker_account, "connector", "mt5_local") == "mt5_local":
        from execution.mt5_tasks import execute_mt5_order_task

        execute_mt5_order_task.apply_async(args=[order.id], queue="mt5_execution")
        return "queued"
    dispatch_place_order(order)
    return "executed"

def _json_safe(val):
    """
    Recursively convert values into JSON-serializable forms (e.g., datetime -> iso string,
    Decimal -> string).
    """
    if isinstance(val, (list, tuple)):
        return [_json_safe(v) for v in val]
    if isinstance(val, dict):
        return {k: _json_safe(v) for k, v in val.items()}
    if isinstance(val, datetime):
        return val.isoformat()
    if isinstance(val, Decimal):
        return str(val)
    return val

SESSION_WINDOWS = (
    ("asia", 0, 6),
    ("london", 6, 12),
    ("new_york", 12, 20),
)


def _session_label(moment=None) -> str:
    moment = moment or timezone.now()
    hour = moment.hour
    for label, start, end in SESSION_WINDOWS:
        if start <= hour < end:
            return label
    return "overnight"


def _tick_timestamp_is_stale(tick_dt: datetime, *, now: datetime | None = None) -> bool:
    """Validate freshness while tolerating bounded MT5 broker-clock skew."""
    now = now or timezone.now()
    age_seconds = (now - tick_dt).total_seconds()
    max_age = int(getattr(settings, "MT5_TICK_MAX_AGE_SECONDS", 120))
    future_tolerance = int(getattr(settings, "MT5_TICK_FUTURE_TOLERANCE_SECONDS", 7200))
    return age_seconds > max_age or age_seconds < -future_tolerance


def _analyze_htf_bias(candles) -> dict | None:
    if not candles or len(candles) < 30:
        return None

    closes = [c["close"] for c in candles]
    k = Decimal("2") / Decimal("21")
    ema = closes[0]
    ema_values = [ema]
    for c in closes[1:]:
        ema = c * k + ema * (Decimal("1") - k)
        ema_values.append(ema)

    ema_now = ema_values[-1]
    lookback = min(5, len(ema_values) - 1)
    ema_prev = ema_values[-lookback]
    last_close = closes[-1] or Decimal("0")
    slope = (ema_now - ema_prev)
    slope_pct = (slope / last_close) if last_close else Decimal("0")

    atr_points = _atr_like(candles, period=14)
    atr_prev = _atr_like(candles[:-5], period=14) if len(candles) > 35 else atr_points
    atr_ratio = (atr_points / atr_prev) if atr_prev else Decimal("1")

    range_window = candles[-30:]
    highs = [c["high"] for c in range_window]
    lows = [c["low"] for c in range_window]
    range_high = max(highs)
    range_low = min(lows)
    denom = range_high - range_low
    position = ((last_close - range_low) / denom) if denom else Decimal("0.5")

    structure = "range"
    if highs[-1] >= range_high and lows[-1] >= lows[-2]:
        structure = "higher_high"
    elif lows[-1] <= range_low and highs[-1] <= highs[-2]:
        structure = "lower_low"

    bias = None
    slope_threshold = Decimal("0.00008")
    if slope_pct > slope_threshold and position > Decimal("0.55"):
        bias = "buy"
    elif slope_pct < -slope_threshold and position < Decimal("0.45"):
        bias = "sell"

    return {
        "bias": bias,
        "ema_slope_pct": float(slope_pct),
        "atr_points": float(atr_points),
        "atr_ratio": float(atr_ratio),
        "range_high": str(range_high),
        "range_low": str(range_low),
        "position_in_range": float(position),
        "structure": structure,
    }


def _compute_bias_from_htf(candles) -> str | None:
    info = _analyze_htf_bias(candles)
    return info.get("bias") if info else None


@dataclass(frozen=True)
class ScalperStrategyEntry:
    runner: Callable
    config_factory: Callable[[], object]
    requires_symbol: bool = False


SCALPER_STRATEGY_REGISTRY: dict[str, ScalperStrategyEntry] = {
    "price_action_pinbar": ScalperStrategyEntry(
        runner=run_price_action_pinbar,
        config_factory=PinBarConfig,
        requires_symbol=True,
    ),
    "trend_pullback": ScalperStrategyEntry(
        runner=run_trend_pullback,
        config_factory=TrendPullbackConfig,
    ),
    "doji_breakout": ScalperStrategyEntry(
        runner=run_doji_breakout,
        config_factory=DojiBreakoutConfig,
        requires_symbol=True,
    ),
    "range_reversion": ScalperStrategyEntry(
        runner=run_range_reversion,
        config_factory=RangeReversionConfig,
    ),
    "breakout_retest": ScalperStrategyEntry(
        runner=run_breakout_retest,
        config_factory=BreakoutRetestConfig,
    ),
    "momentum_ignition": ScalperStrategyEntry(
        runner=run_momentum_ignition,
        config_factory=MomentumIgnitionConfig,
    ),
}


def _atr_like(candles, period: int = 14):
    """Simple ATR-like mean of high-low for sizing/filters."""
    from decimal import Decimal  # local to avoid circulars in tests

    if not candles or len(candles) < period:
        return Decimal("0")
    window = candles[-period:]
    total = sum((c["high"] - c["low"] for c in window), Decimal("0"))
    return total / Decimal(str(period))


def _reconcile_missing_owned_position(connector: MT5Connector, local: BrokerPosition) -> list[int]:
    """Import broker-side SL/TP exits for one EZ Trade-owned position.

    MT5 removes a position immediately after its stop or take-profit fills. The
    open-position snapshot alone therefore cannot distinguish a legitimate
    broker close from an unexplained disappearance. Position-scoped deal
    history is authoritative and is not affected by broker/local clock skew.
    """
    if local.ownership != "ez_trade":
        return []

    deals = connector.history_deals_for_position_account(
        local.broker_account,
        local.broker_position_ticket,
    )
    exit_entries = {1, 2, 3}  # MT5 DEAL_ENTRY_OUT, INOUT, OUT_BY
    exit_deals = [
        deal
        for deal in deals
        if int(getattr(deal, "position_id", 0) or 0) == local.broker_position_ticket
        and int(getattr(deal, "entry", -1)) in exit_entries
    ]
    if not exit_deals:
        return []

    close_order, _ = create_close_order_for_position(local, local.broker_account)
    imported = []
    for deal in sorted(
        exit_deals,
        key=lambda value: int(getattr(value, "time_msc", 0) or getattr(value, "time", 0) or 0),
    ):
        deal_ticket = int(getattr(deal, "ticket", 0) or 0)
        if not deal_ticket or Execution.objects.filter(
            order__broker_account=local.broker_account,
            broker_deal_ticket=deal_ticket,
        ).exists():
            continue
        qty = Decimal(str(getattr(deal, "volume", 0) or 0))
        price = Decimal(str(getattr(deal, "price", 0) or 0))
        if qty <= 0 or price <= 0:
            continue
        record_fill(
            close_order,
            qty,
            price,
            broker_order_ticket=int(getattr(deal, "order", 0) or 0) or None,
            broker_deal_ticket=deal_ticket,
            broker_position_ticket=local.broker_position_ticket,
            broker_profit=Decimal(str(getattr(deal, "profit", 0) or 0)),
            commission=Decimal(str(getattr(deal, "commission", 0) or 0)),
            swap=Decimal(str(getattr(deal, "swap", 0) or 0)),
            broker_metadata=(deal._asdict() if hasattr(deal, "_asdict") else {}),
        )
        imported.append(deal_ticket)

    if not imported:
        return []

    filled = close_order.executions.aggregate(total=Sum("qty"))["total"] or Decimal("0")
    requested_qty = Decimal(str(close_order.qty))
    close_order.filled_qty = min(requested_qty, filled)
    close_order.remaining_qty = max(Decimal("0"), requested_qty - close_order.filled_qty)
    last = exit_deals[-1]
    close_order.broker_order_ticket = int(getattr(last, "order", 0) or 0) or None
    close_order.broker_deal_ticket = int(getattr(last, "ticket", 0) or 0) or None
    close_order.broker_position_ticket = local.broker_position_ticket
    close_order.mt5_retcode_description = "Reconciled broker-side position close"
    close_order.save(
        update_fields=[
            "filled_qty",
            "remaining_qty",
            "broker_order_ticket",
            "broker_deal_ticket",
            "broker_position_ticket",
            "mt5_retcode_description",
        ]
    )
    target = "filled" if close_order.remaining_qty == 0 else "part_filled"
    if close_order.status != target:
        update_order_status(
            close_order,
            target,
            price=Decimal(str(getattr(last, "price", 0) or 0)),
        )
    local.status = "closed" if target == "filled" else "missing"
    local.volume = close_order.remaining_qty
    local.closed_at = timezone.now() if target == "filled" else None
    local.last_reconciled_at = timezone.now()
    local.save(update_fields=["status", "volume", "closed_at", "last_reconciled_at"])
    return imported


@shared_task(
    bind=True,
    autoretry_for=(Exception,),
    retry_backoff=True,      # exponential: 1s, 2s, 4s, ...
    retry_backoff_max=300,   # cap at 5 minutes
    retry_jitter=True,       # add randomness
    retry_kwargs={"max_retries": 5},
)
def simulate_fill_task(self, order_id: int):
    try:
        order = Order.objects.get(id=order_id)
        if order.status not in ("ack", "new"):
            return
        # Simple deterministic mock price: 1.1000 for buys, 1.1005 for sells
        price = Decimal("1.1000") if order.side == "buy" else Decimal("1.1005")
        # Transition to filled + create execution/position
        update_order_status(order, "filled", price=price)
        record_fill(order, order.qty, price, contract_size=Decimal("1"))
        return {"status": "filled", "order_id": order.id, "price": str(price)}
    except Exception as e:
        task_failures_total.labels(task="simulate_fill_task").inc()
        raise
    

@shared_task(
    bind=True,
    autoretry_for=(Exception,),
    retry_backoff=True,      # exponential: 1s, 2s, 4s, ...
    retry_backoff_max=300,   # cap at 5 minutes
    retry_jitter=True,       # add randomness
    retry_kwargs={"max_retries": 5},
)
def monitor_positions_task(self):
    """
    Refresh broker-authoritative position state. Broker-side SL/TP remains the
    protection mechanism; this task does not close from placeholder PnL math.
    """
    try:
        connector = MT5Connector()
        refreshed = 0
        for account in BrokerAccount.objects.filter(is_active=True, connector="mt5_local"):
            raw_positions = connector.positions_for_account(account)
            for raw in raw_positions:
                ticket = int(getattr(raw, "ticket", 0) or getattr(raw, "identifier", 0) or 0)
                if not ticket:
                    continue
                local = BrokerPosition.objects.filter(
                    broker_account=account,
                    broker_position_ticket=ticket,
                    ownership="ez_trade",
                ).select_related("originating_order").first()
                if local:
                    connector._sync_broker_position(
                        local.originating_order,
                        raw,
                        broker_account=account,
                        ownership="ez_trade",
                    )
                    refreshed += 1
        return {"status": "ok", "refreshed": refreshed}
    except Exception as e:
        task_failures_total.labels(task="monitor_positions_task").inc()
        raise


@shared_task(
    bind=True,
    autoretry_for=(Exception,),
    retry_backoff=True,      # exponential: 1s, 2s, 4s, ...
    retry_backoff_max=300,   # cap at 5 minutes
    retry_jitter=True,       # add randomness
    retry_kwargs={"max_retries": 5},
)
def trail_positions_task(self):
    """
    Applies a simple trailing stop to profitable positions.
    """
    try:
        runtime_cfg = get_runtime_config()
        tcfg = TrailingConfig(
            trigger=runtime_cfg.trailing_trigger,
            distance=runtime_cfg.trailing_distance,
        )
        moved_ids = []
        connector = MT5Connector()
        for pos in BrokerPosition.objects.filter(
            status="open",
            ownership="ez_trade",
        ).select_related("broker_account"):
            tick = connector.tick_for_account(pos.broker_account, pos.symbol)
            bid = Decimal(str(getattr(tick, "bid", 0) or 0))
            ask = Decimal(str(getattr(tick, "ask", 0) or 0))
            market = bid if pos.side == "buy" else ask
            if market <= 0:
                continue
            gain = market - pos.open_price if pos.side == "buy" else pos.open_price - market
            if gain < tcfg.trigger:
                continue
            requested_sl = market - tcfg.distance if pos.side == "buy" else market + tcfg.distance
            improves = (
                pos.sl is None
                or (pos.side == "buy" and requested_sl > pos.sl)
                or (pos.side == "sell" and requested_sl < pos.sl)
            )
            if not improves:
                continue
            connector.modify_broker_position(pos, sl=requested_sl)
            moved_ids.append(pos.id)
        return {"moved": moved_ids}
    except Exception as e:
        task_failures_total.labels(task="trail_positions_task").inc()
        raise


@shared_task(
    bind=True,
    autoretry_for=(Exception,),
    retry_backoff=True,      # exponential: 1s, 2s, 4s, ...
    retry_backoff_max=300,   # cap at 5 minutes
    retry_jitter=True,       # add randomness
    retry_kwargs={"max_retries": 5},
)
def reconcile_daily_task(self):
    """
    Roll broker-provided deal economics and live position profit into PnLDaily.
    """
    try:
        today = timezone.now().date()
        totals = defaultdict(lambda: {"realized": Decimal("0"), "unrealized": Decimal("0"), "fees": Decimal("0")})
        executions = Execution.objects.filter(
            exec_time__date=today,
            profit__isnull=False,
        ).select_related("order")
        for execution in executions:
            key = (execution.order.broker_account_id, execution.order.symbol)
            totals[key]["realized"] += (
                execution.profit + execution.commission + execution.swap
            )
            totals[key]["fees"] += execution.commission
        for pos in BrokerPosition.objects.filter(status="open"):
            key = (pos.broker_account_id, pos.symbol)
            totals[key]["unrealized"] += pos.profit + pos.swap

        for (acct_id, symbol), values in totals.items():
            pnl_daily, _ = PnLDaily.objects.get_or_create(
                broker_account_id=acct_id,
                symbol=symbol,
                date=today,
                defaults={"realized": Decimal("0"), "unrealized": Decimal("0")},
            )
            pnl_daily.realized = values["realized"]
            pnl_daily.unrealized = values["unrealized"]
            pnl_daily.fees = values["fees"]
            latest = AccountSnapshot.objects.filter(broker_account_id=acct_id).first()
            if latest:
                pnl_daily.balance = latest.balance
            pnl_daily.save(update_fields=["realized", "unrealized", "fees", "balance"])
        return {"status": "ok"}
    except Exception as e:
        task_failures_total.labels(task="reconcile_daily_task").inc()
        raise


@shared_task(
    bind=True, autoretry_for=(Exception,),
    retry_backoff=True, retry_backoff_max=300, retry_jitter=True, retry_kwargs={"max_retries": 3},
)
def ingest_tradingview_email(self):
    """
    Poll IMAP inbox, parse TradingView alert emails (JSON in body),
    then reuse AlertWebhookSerializer + auto-trade flow via the same code path used by the webhook.
    """
    return {"status": "disabled", "reason": "external_alert_ingestion_removed"}

    try:
        from execution.integrations.tradingview_email import fetch_emails_and_parse
        from execution.serializers import AlertWebhookSerializer
        from execution.services.decision import make_decision_from_signal
        from execution.services.fanout import fanout_orders
        from execution.services.brokers import dispatch_place_order
        from core.metrics import signals_ingested_total

        alerts = fetch_emails_and_parse()
        created_count = 0
        sent_count = 0

        for payload in alerts:
            # validate & create/update Signal
            ser = AlertWebhookSerializer(data=payload)
            if not ser.is_valid():
                log_journal_event(
                    "signal.ingest.error",
                    severity="warning",
                    message="TradingView email alert validation failed",
                    context={"errors": ser.errors, "payload": payload},
                )
                continue
            signal, created = ser.save()
            signals_ingested_total.labels(signal.source, signal.symbol, signal.timeframe).inc()
            log_journal_event(
                "signal.ingest",
                signal=signal,
                bot=signal.bot,
                owner=getattr(signal, "owner", None),
                symbol=signal.symbol,
                message=f"{signal.symbol} {signal.direction} via email",
                context={"via": "email", "timeframe": signal.timeframe},
            )

            if created:
                created_count += 1

            # AUTO-TRADE (identical to webhook's guard)
            if signal.bot and bot_is_available_for_trading(signal.bot) and getattr(signal.bot, "auto_trade", False):
                decision = make_decision_from_signal(signal)
                if decision.action == "open":
                    orders = fanout_orders(decision, master_qty=None)  # default bot qty
                    for order, _c in orders:
                        try:
                            _queue_or_dispatch_order(order)
                            sent_count += 1
                        except Exception as e:
                            log_journal_event(
                                "order.dispatch_error",
                                severity="error",
                                order=order,
                                bot=order.bot,
                                broker_account=order.broker_account,
                                symbol=order.symbol,
                                message="Email auto-trade dispatch failed",
                                context={"error": str(e)},
                            )

        return {"alerts": len(alerts), "signals_new": created_count, "orders_sent": sent_count}
    except Exception as e:
        task_failures_total.labels(task="ingest_tradingview_email").inc()
        raise
    
    
@shared_task(
    bind=True,
    autoretry_for=(Exception,),
    retry_backoff=True,
    retry_backoff_max=300,
    retry_jitter=True,
    retry_kwargs={"max_retries": 3},
)
def scan_harami_for_bot(self, bot_id: int, timeframe: str = "15m", n_bars: int = 200):
    """
    Internal engine prototype: fetch candles for a bot's symbol/timeframe,
    run the Harami strategy, and log the decision.

    ⚠️ IMPORTANT: This task does NOT create Signals, Decisions, or Orders yet.
    It is read-only + logging so it cannot affect live trading.
    """
    from bots.models import Bot  # local import to avoid any circulars

    try:
        bot = Bot.objects.select_related("broker_account").get(id=bot_id)
    except Bot.DoesNotExist:
        logger.warning(f"[HaramiScan] bot_id={bot_id} not found")
        return {"status": "error", "reason": "bot_not_found"}

    if getattr(bot, "status", None) != "active":
        logger.info(f"[HaramiScan] bot={bot.id} inactive, skipping")
        return {"status": "skipped", "reason": "bot_inactive"}

    broker_account = getattr(bot, "broker_account", None)
    if not broker_account or not getattr(broker_account, "is_active", False):
        logger.info(f"[HaramiScan] bot={bot.id} has no active broker account, skipping")
        return {"status": "skipped", "reason": "no_active_broker"}

    symbol = getattr(bot, "symbol", None)
    if not symbol:
        logger.info(f"[HaramiScan] bot={bot.id} has no symbol configured, skipping")
        return {"status": "skipped", "reason": "no_symbol"}

    try:
        tf = bot.default_timeframe or "5m"
        candles = get_candles_for_account(
            broker_account=broker_account,
            symbol=symbol,
            timeframe=tf,
            n_bars=200,
        )
        if not candles:
            logger.info(f"[HaramiScan] bot={bot.id} symbol={symbol} tf={timeframe} -> no candles")
            return {"status": "skipped", "reason": "no_candles"}

        decision = detect_harami(candles)

        logger.info(
            "[HaramiScan] bot=%s symbol=%s tf=%s action=%s direction=%s sl=%s tp=%s reason=%s",
            bot.id,
            symbol,
            timeframe,
            decision.action,
            decision.direction,
            decision.sl,
            decision.tp,
            decision.reason,
        )

        return {
            "status": "ok",
            "action": decision.action,
            "direction": decision.direction,
            "sl": str(decision.sl) if decision.sl is not None else None,
            "tp": str(decision.tp) if decision.tp is not None else None,
            "reason": decision.reason,
        }
    except Exception as e:
        task_failures_total.labels(task="scan_harami_for_bot").inc()
        logger.exception(f"[HaramiScan] bot={bot.id} symbol={symbol} tf={timeframe} failed: %s", e)
        raise


@shared_task(
    bind=True,
    autoretry_for=(ConnectorError,),  # only retry on broker connectivity issues
    retry_backoff=True,
    retry_backoff_max=300,
    retry_jitter=True,
    retry_kwargs={"max_retries": 3},
)
def trade_harami_for_bot(self, bot_id: int, timeframe: str = "15m", n_bars: int = 200):
    
    from execution.services.engine import run_engine, EngineContext
    """
    Internal engine task (v1):

    1) Fetch candles for the bot's symbol/timeframe (+ optional HTF).
    2) Run the internal engine (harami + engulfing + trend).
    3) If action='open', create a synthetic Signal (source='engine_v1').
    4) Run it through make_decision_from_signal -> Decision.
    5) Fanout to Orders and send them via dispatch_place_order.
    """
    from bots.models import Bot
    from django.utils import timezone

    try:
        bot = Bot.objects.select_related("broker_account").get(id=bot_id)
    except Bot.DoesNotExist:
        logger.warning("[EngineTrade] bot_id=%s not found", bot_id)
        return {"status": "error", "reason": "bot_not_found"}

    if not getattr(bot, "auto_trade", False):
        logger.info("[EngineTrade] bot=%s auto_trade=False, skipping", bot.id)
        return {"status": "skipped", "reason": "bot_auto_trade_disabled"}

    market_status = get_market_status_for_bot(bot, use_mt5_probe=True)
    if market_status and not market_status.is_open:
        maybe_pause_bot_for_market(bot, market_status)
        logger.info("[EngineTrade] bot=%s symbol=%s skipped: market_closed (%s)", bot.id, getattr(bot.asset, "symbol", None), market_status.reason)
        return {"status": "skipped", "reason": f"market_closed:{market_status.reason}"}
    if market_status and market_status.is_open:
        maybe_unpause_crypto_for_open_market(bot, market_status)

    if not bot_is_available_for_trading(bot):
        logger.info("[EngineTrade] bot=%s unavailable (status/paused), skipping", bot.id)
        return {"status": "skipped", "reason": "bot_unavailable"}

    broker_account = getattr(bot, "broker_account", None)
    if not broker_account or not getattr(broker_account, "is_active", False):
        logger.info("[EngineTrade] bot=%s has no active broker account, skipping", bot.id)
        return {"status": "skipped", "reason": "no_active_broker"}

    symbol = getattr(bot.asset, "symbol", None)
    if not symbol:
        logger.info("[EngineTrade] bot=%s has no asset configured, skipping", bot.id)
        return {"status": "skipped", "reason": "no_symbol"}
    canonical_sym = canonical_symbol(symbol)
    if not bot.accepts(symbol, timeframe):
        logger.info(
            "[EngineTrade] bot=%s does not accept symbol=%s tf=%s, skipping",
            bot.id,
            symbol,
            timeframe,
        )
        return {"status": "skipped", "reason": "bot_not_accept_symbol_timeframe"}

    # 1) Fetch candles (entry TF)
    try:
        entry_candles = get_candles_for_account(
            broker_account=broker_account,
            symbol=symbol,
            timeframe=timeframe,
            n_bars=n_bars,
        )
    except Exception as e:
        task_failures_total.labels(task="trade_harami_for_bot").inc()
        logger.exception(
            "[EngineTrade] bot=%s symbol=%s tf=%s marketdata failed: %s",
            bot.id,
            symbol,
            timeframe,
            e,
        )
        raise

    if not entry_candles:
        logger.info(
            "[EngineTrade] bot=%s symbol=%s tf=%s -> no candles",
            bot.id,
            symbol,
            timeframe,
        )
        return {"status": "skipped", "reason": "no_candles"}

    # Optional HTF candles
    htf = _get_htf(timeframe)
    htf_candles = None
    if htf:
        try:
            htf_candles = get_candles_for_account(
                broker_account=broker_account,
                symbol=symbol,
                timeframe=htf,
                n_bars=200,
            )
        except Exception as e:
            logger.exception(
                "[EngineTrade] bot=%s symbol=%s htf=%s marketdata failed (ignored): %s",
                bot.id,
                symbol,
                htf,
                e,
            )
            htf_candles = None

    htf_bias = _compute_bias_from_htf(htf_candles) if htf_candles else None

    # 2) Build engine context + run engine (auto-trade mode uses asset/profile presets)
    if getattr(bot, "auto_trade", False):
        last_entry = entry_candles[-1]
        atr_points = _atr_like(entry_candles, period=14)
        allowed = select_ai_strategies(
            engine_mode="harami",
            available=STRATEGY_CHOICES,
            symbol=canonical_sym,
            context={
                "atr_points": atr_points,
                "bar_range": last_entry["high"] - last_entry["low"],
                "last_close": last_entry.get("close"),
                "htf_bias": htf_bias,
            },
        )
    else:
        allowed = bot.enabled_strategies or []
    if not allowed:
        log_journal_event(
            "engine.decision",
            bot=bot,
            owner=getattr(bot, "owner", None),
            symbol=symbol,
            message=f"{symbol} {timeframe} skipped (no enabled strategies)",
            context={"action": "skipped", "reason": "no_enabled_strategies"},
        )
        return {"status": "skipped", "reason": "no_enabled_strategies"}

    ctx = EngineContext(
        symbol=symbol,
        timeframe=timeframe,
        entry_candles=entry_candles,
        htf_candles=htf_candles,
        allowed_strategies=allowed,
    )
    engine_decision = run_engine(ctx)
    # Trace every engine decision so we can see why trades are skipped.
    log_journal_event(
        "engine.decision",
        bot=bot,
        owner=getattr(bot, "owner", None),
        symbol=symbol,
        message=f"{symbol} {timeframe} action={engine_decision.action}",
        context={
            "timeframe": timeframe,
            "action": engine_decision.action,
            "reason": engine_decision.reason,
            "score": float(engine_decision.score or 0.0),
            "strategy": engine_decision.strategy,
        },
    )

    logger.info(
        "[EngineTrade] bot=%s symbol=%s tf=%s action=%s dir=%s sl=%s tp=%s reason=%s strategy=%s",
        bot.id,
        symbol,
        timeframe,
        engine_decision.action,
        engine_decision.direction,
        engine_decision.sl,
        engine_decision.tp,
        engine_decision.reason,
        engine_decision.strategy,
    )

    if engine_decision.action != "open" or not engine_decision.direction:
        return {
            "status": "ok",
            "action": engine_decision.action,
            "reason": engine_decision.reason,
        }

    # 3) Create a synthetic Signal for this engine decision
    # deterministic dedupe per bot/symbol/timeframe/last bar to avoid duplicate signals
    dedupe_key = f"engine:{bot.id}:{symbol}:{timeframe}:{entry_candles[-1]['time'].isoformat()}"
    atr_val = _atr_like(entry_candles)

    # Use get_or_create for idempotency - prevents UNIQUE constraint violations on dedupe_key
    signal, signal_created = Signal.objects.get_or_create(
        dedupe_key=dedupe_key,
        defaults={
            "bot": bot,
            "source": "engine_v1",
            "symbol": symbol,
            "timeframe": timeframe,
            "direction": engine_decision.direction,
            "payload": {
                "engine": engine_decision.strategy,
                "sl": str(engine_decision.sl) if engine_decision.sl is not None else None,
                "tp": str(engine_decision.tp) if engine_decision.tp is not None else None,
                "reason": engine_decision.reason,
                "score": engine_decision.score,
                "generated_at": timezone.now().isoformat(),
                "atr": str(atr_val),
            },
        }
    )

    # 4) Run through existing decision + risk pipeline
    try:
        # Idempotency: if this signal already produced a decision, reuse it to avoid duplicate orders on retries.
        existing_decision = Decision.objects.filter(signal=signal, action="open").first()
        if existing_decision:
            decision = existing_decision
            logger.info("[EngineTrade] bot=%s signal=%s reusing existing decision=%s", bot.id, signal.id, decision.id)
        else:
            decision = make_decision_from_signal(signal)
    except Exception as e:
        task_failures_total.labels(task="trade_harami_for_bot").inc()
        logger.exception(
            "[EngineTrade] bot=%s signal=%s decision failed: %s",
            bot.id,
            signal.id,
            e,
        )
        raise

    if decision.action != "open":
        logger.info(
            "[EngineTrade] decision ignored: bot=%s signal=%s action=%s reason=%s",
            bot.id,
            signal.id,
            decision.action,
            decision.reason,
        )
        return {
            "status": "ok",
            "decision_action": decision.action,
            "decision_reason": decision.reason,
        }

    # 5) Fanout to orders and send
    orders_info = []
    dispatch_errors = []
    for order, created in fanout_orders(decision, master_qty=None):
        should_dispatch = created or order.status in ("new", "ack")
        if should_dispatch:
            # Apply unit-aware, broker-aware guards before dispatch
            try:
                constraints = get_broker_symbol_constraints(order.broker_account, order.symbol)
            except Exception:
                constraints = None
            point = getattr(constraints, "point", None) if constraints else None
            spread = (decision.signal.payload or {}).get("spread_points") if decision.signal and decision.signal.payload else None
            min_stop_pts = getattr(constraints, "stops_level_points", None) if constraints else None
            entry_hint = None
            try:
                params = decision.params or {}
                for key in ("entry", "price", "close", "last_price"):
                    if params.get(key):
                        entry_hint = Decimal(str(params.get(key)))
                        break
                if entry_hint is None and decision.signal and decision.signal.payload:
                    payload = decision.signal.payload or {}
                    for key in ("entry", "price", "close", "last_price"):
                        if payload.get(key):
                            entry_hint = Decimal(str(payload.get(key)))
                            break
            except Exception:
                entry_hint = None
            if entry_hint is None:
                try:
                    px = get_price(order.broker_account, order.symbol)
                    if px is not None:
                        entry_hint = Decimal(str(px))
                except Exception:
                    entry_hint = None
            sl_distance_price = Decimal("0")
            try:
                if entry_hint is not None and order.sl is not None:
                    sl_distance_price = abs(Decimal(str(order.sl)) - entry_hint)
            except Exception:
                sl_distance_price = Decimal("0")
            guard_inputs = GuardInputs(
                sl_distance=sl_distance_price,
                sl_unit="price",
                spread=Decimal(str(spread)) if spread is not None else None,
                spread_unit="price",
                point=point,
                min_stop_points=min_stop_pts,
            )
            guard = apply_order_guards(guard_inputs)
            if not guard.ok:
                log_journal_event(
                    "order.dispatch_error",
                    severity="warning",
                    order=order,
                    bot=order.bot,
                    broker_account=order.broker_account,
                    symbol=order.symbol,
                    message="Order skipped by guard",
                    context={
                        "reason": guard.reason,
                        "sl_distance_price": str(sl_distance_price),
                        "spread_price": str(spread) if spread is not None else None,
                        "min_stop_points": str(min_stop_pts) if min_stop_pts is not None else None,
                        "point": str(point) if point is not None else None,
                    },
                )
                continue
            try:
                _queue_or_dispatch_order(order)
            except Exception as e:
                dispatch_errors.append(e)
                log_journal_event(
                    "order.dispatch_error",
                    severity="error",
                    order=order,
                    bot=order.bot,
                    broker_account=order.broker_account,
                    symbol=order.symbol,
                    message="Engine dispatch failed",
                    context={
                        "bot_id": bot.id if bot else None,
                        "status": order.status,
                        "error": str(e),
                    },
                )
                logger.exception(f"[EngineTrade] Failed to dispatch order {order.id}: {e}")
                # Continue processing other orders
        orders_info.append(
            {
                "order_id": order.id,
                "created": created,
                "status": order.status,
                "symbol": order.symbol,
                "side": order.side,
            }
        )

    # If any broker dispatch failed, surface a ConnectorError to trigger retry/backoff.
    if dispatch_errors:
        raise ConnectorError(f"{len(dispatch_errors)} order dispatch failure(s); see logs for details")

    log_journal_event(
        "harami_trade_executed",
        bot=bot,
        owner=getattr(bot, "owner", None),
        symbol=symbol,
        message=f"Placed {len(orders_info)} order(s) for {symbol} {timeframe}",
        context={
            "bot_id": bot.id if bot else None,
            "signal_id": signal.id,
            "decision_id": decision.id,
            "orders": orders_info,
        },
    )

    return {
        "status": "ok",
        "decision_id": decision.id,
        "orders": orders_info,
    }


@shared_task
def check_broker_health_task(symbol_hint: str = "EURUSDm"):
    """
    Connectivity check for active broker accounts (MT5).
    Attempts a login + symbol select + ready check and logs audit events.
    """
    connector = MT5Connector()
    checked = []
    for acct in BrokerAccount.objects.filter(is_active=True, broker__in=["mt5", "exness_mt5", "icmarket_mt5"]):
        creds = acct.get_creds()
        symbol = symbol_hint
        try:
            connector.check_health(creds, symbol)
            log_journal_event(
                "broker.health",
                broker_account=acct,
                owner=acct.owner,
                message=f"Broker {acct.id} healthy",
                symbol=symbol,
                context={"status": "ok"},
            )
            checked.append(acct.id)
        except Exception as e:
            log_journal_event(
                "broker.health.error",
                severity="warning",
                broker_account=acct,
                owner=acct.owner,
                symbol=symbol,
                message="Broker health check failed",
                context={"error": str(e)},
            )
            checked.append(f"{acct.id}:error")
    return {"checked": checked}


@shared_task
def validate_broker_configs_task():
    """
    Basic validation of broker credentials to catch misconfig early.
    """
    issues = []
    for acct in BrokerAccount.objects.filter(is_active=True):
        errs = []
        creds = acct.get_creds()
        login = creds.get("login")
        path = creds.get("path")
        if acct.broker in ["mt5", "exness_mt5", "icmarket_mt5"]:
            if login is None or str(login).strip() == "" or not str(login).isdigit():
                errs.append("login_missing_or_invalid")
            if not path or not os.path.exists(path):
                errs.append("terminal_path_missing")
            missing = [k for k in ("password", "server", "path") if not creds.get(k)]
            if missing:
                errs.append(f"missing_fields:{','.join(missing)}")
        if errs:
            issues.append((acct.id, errs))
            log_journal_event(
                "broker.config.error",
                severity="warning",
                broker_account=acct,
                owner=acct.owner,
                message="Broker configuration invalid",
                context={"errors": errs},
            )
    return {"issues": issues}

    
    

@shared_task(
    bind=True,
    autoretry_for=(Exception,),
    retry_backoff=True,
    retry_backoff_max=300,
    retry_jitter=True,
    retry_kwargs={"max_retries": 3},
)
def run_scalper_engine_for_all_bots(self, timeframe: str = "1m", n_bars: int = 100):
    """
    High-frequency scalper signal generator.
    
    - Picks bots with engine_mode="scalper", auto_trade=True, status="active"
    - For each bot, scans M1 candles and runs bot.enabled_strategies (price_action_pinbar, trend_pullback, etc.)
    - Emits Signal objects for each strategy match
    - Routes through existing decision + execution pipeline
    
    This is the real "scalper brain" that powers high-frequency trading on XAUUSDm and other liquid assets.
    """
    from bots.models import Bot
    
    bots_qs = (
        Bot.objects.select_related("broker_account", "asset")
        .filter(
            auto_trade=True,
            status="active",
            engine_mode="scalper",
        )
    )
    
    dispatched = 0
    skipped_no_broker = 0
    skipped_no_symbols = 0
    skipped_no_strategies = 0
    skipped_not_accepted = 0
    skipped_market_closed = 0
    skipped_unavailable = 0

    for bot in bots_qs:
        broker_account = getattr(bot, "broker_account", None)
        if not broker_account or not getattr(broker_account, "is_active", False):
            skipped_no_broker += 1
            continue

        symbol = getattr(bot.asset, "symbol", None)
        if not symbol:
            skipped_no_symbols += 1
            continue

        market_status = get_market_status_for_bot(bot, use_mt5_probe=False)
        if market_status and not market_status.is_open:
            maybe_pause_bot_for_market(bot, market_status)
            skipped_market_closed += 1
            continue
        if market_status and market_status.is_open:
            maybe_unpause_crypto_for_open_market(bot, market_status)

        if not bot_is_available_for_trading(bot):
            skipped_unavailable += 1
            continue

        # Must have manual strategies when auto-trade is disabled
        enabled_strats = bot.enabled_strategies or []
        if not getattr(bot, "auto_trade", True) and not enabled_strats:
            skipped_no_strategies += 1
            continue
        
        # Prefer bot-specific default timeframe when provided, otherwise fall back to the global default.
        tf = (bot.default_timeframe or timeframe or "1m").lower()
        fallback_tf = (timeframe or "1m").lower()

        if not bot.accepts(symbol, tf):
            # If the bot default is not accepted, attempt the fallback; otherwise skip.
            if tf != fallback_tf and bot.accepts(symbol, fallback_tf):
                tf = fallback_tf
            else:
                skipped_not_accepted += 1
                continue
        
        # Run inline to guarantee scalper cycles execute even if a nested Celery worker is unavailable.
        trade_scalper_strategies_for_bot.apply(
            args=(bot.id,),
            kwargs={"timeframe": tf, "n_bars": n_bars},
        )
        dispatched += 1
    
    logger.info(
        "[ScalperRunner] tf=%s dispatched=%s skipped_no_broker=%s skipped_no_symbols=%s skipped_no_strategies=%s skipped_not_accepted=%s skipped_market_closed=%s skipped_unavailable=%s",
        timeframe,
        dispatched,
        skipped_no_broker,
        skipped_no_symbols,
        skipped_no_strategies,
        skipped_not_accepted,
        skipped_market_closed,
        skipped_unavailable,
    )

    return {
        "status": "ok",
        "timeframe": timeframe,
        "dispatched": dispatched,
        "skipped_no_broker": skipped_no_broker,
        "skipped_no_symbols": skipped_no_symbols,
        "skipped_no_strategies": skipped_no_strategies,
        "skipped_not_accepted": skipped_not_accepted,
        "skipped_market_closed": skipped_market_closed,
        "skipped_unavailable": skipped_unavailable,
    }


@shared_task(
    bind=True,
    autoretry_for=(ConnectorError,),
    retry_backoff=True,
    retry_backoff_max=300,
    retry_jitter=True,
    retry_kwargs={"max_retries": 5},
)
def trade_scalper_strategies_for_bot(self, bot_id: int, timeframe: str = "1m", n_bars: int = 100):
    """
    Runs all enabled scalper strategies for a single bot.

    - Fetches M1 candles
    - Runs price_action_pinbar, trend_pullback, etc. (whatever is enabled for the bot)
    - Creates Signal + Decision + Order for each match
    - Emits to broker if decision passes risk checks
    """
    from bots.models import Bot
    bot = Bot.objects.select_related("broker_account", "asset").get(id=bot_id)
    session_label = _session_label()
    symbol = getattr(getattr(bot, "asset", None), "symbol", None)

    def _log_skip(reason: str, extra_context: dict | None = None):
        log_journal_event(
            "scalper_engine_run",
            bot=bot,
            owner=bot.owner if bot else None,
            symbol=symbol or "",
            message=f"Scalper skipped tf={timeframe} reason={reason} session={session_label}",
            context={
                "timeframe": timeframe,
                "session": session_label,
                "auto_trade_active": bool(getattr(bot, "auto_trade", False)),
                "outcome": "skipped",
                "reason": reason,
                **(extra_context or {}),
            },
        )

    if not getattr(bot, "auto_trade", False):
        logger.info("[ScalperTrade] bot=%s auto_trade=False, skipping", bot.id)
        _log_skip("bot_auto_trade_disabled")
        return {"status": "skipped", "reason": "bot_auto_trade_disabled"}

    market_status = get_market_status_for_bot(bot, use_mt5_probe=True)
    if market_status and not market_status.is_open:
        maybe_pause_bot_for_market(bot, market_status)
        logger.info(
            "[ScalperTrade] bot=%s symbol=%s skipped: market_closed (%s)",
            bot.id,
            symbol,
            market_status.reason,
        )
        _log_skip("market_closed", {"market_reason": market_status.reason})
        return {"status": "skipped", "reason": f"market_closed:{market_status.reason}"}
    if market_status and market_status.is_open:
        maybe_unpause_crypto_for_open_market(bot, market_status)

    if not bot_is_available_for_trading(bot):
        logger.info("[ScalperTrade] bot=%s unavailable (status/paused/allocation), skipping", bot.id)
        _log_skip("bot_unavailable")
        return {"status": "skipped", "reason": "bot_unavailable"}

    broker_account = getattr(bot, "broker_account", None)
    if not broker_account or not getattr(broker_account, "is_active", False):
        logger.info("[ScalperTrade] bot=%s has no active broker account, skipping", bot.id)
        _log_skip("no_active_broker")
        return {"status": "skipped", "reason": "no_active_broker"}

    if not getattr(bot, "asset", None) or not getattr(bot.asset, "symbol", None):
        logger.info("[ScalperTrade] bot=%s has no asset configured, skipping", bot.id)
        _log_skip("no_symbol")
        return {"status": "skipped", "reason": "no_symbol"}

    broker_constraints = get_broker_symbol_constraints(broker_account, getattr(bot.asset, "symbol", None))
    broker_min_stop_points = broker_constraints.stops_level_points or Decimal("0")
    broker_point = broker_constraints.point
    broker_lot_step = broker_constraints.lot_step

    scalper_cfg = build_scalper_config(bot)
    scalper_params = bot.scalper_params or {}
    strategy_profile_key = (
        scalper_params.get("strategy_profile") or scalper_cfg.default_strategy_profile
    )
    profile_aliases = {
        "xauusd_standard": "core_standard",
        "xauusd_aggressive": "core_aggressive",
    }
    original_profile_key = strategy_profile_key
    strategy_profile_key = profile_aliases.get(strategy_profile_key, strategy_profile_key)
    if strategy_profile_key != original_profile_key:
        scalper_params["strategy_profile"] = strategy_profile_key
        bot.scalper_params = scalper_params
        bot.save(update_fields=["scalper_params"])
    asset_profile_key = None
    if getattr(bot, "asset", None) and getattr(bot.asset, "symbol", None):
        asset_canon = canonical_symbol(bot.asset.symbol)
        for key, profile in (scalper_cfg.strategy_profiles or {}).items():
            if profile.symbol and canonical_symbol(profile.symbol) == asset_canon:
                asset_profile_key = key
                break
    if getattr(bot, "auto_trade", False) and asset_profile_key:
        strategy_profile_key = asset_profile_key
    strategy_profile = scalper_cfg.strategy_profiles.get(strategy_profile_key)

    symbol = bot.asset.symbol
    canonical_sym = canonical_symbol(symbol)
    auto_mode = bool(getattr(bot, "auto_trade", False))
    manual_strats = list(bot.enabled_strategies or [])
    profile_strats = (
        list(strategy_profile.enabled_strategies)
        if strategy_profile and strategy_profile.enabled_strategies
        else []
    )
    enabled_strats: list[str] = []
    strategy_context: dict[str, object] = {
        "symbol": canonical_sym,
        "mode": "auto_profile" if auto_mode else "manual",
        "profile_key": strategy_profile_key,
        "profile_symbol": getattr(strategy_profile, "symbol", None) if strategy_profile else None,
        "manual_configured": bool(manual_strats),
    }
    if not auto_mode:
        enabled_strats = manual_strats
        if not enabled_strats:
            _log_skip(
                "no_enabled_strategies",
                {"strategy_context": strategy_context, "strategy_profile": strategy_profile_key},
            )
            return {"status": "ok", "reason": "no_enabled_strategies"}
    
    tick_snapshot = None
    spread_points = None
    # Get M1 candles
    try:
        # Validate broker-authoritative symbol/tick state before evaluating a new entry.
        tick = None
        try:
            connector = MT5Connector()
            info = connector.symbol_info_for_account(broker_account, symbol)
            if not getattr(info, "visible", False) or getattr(info, "trade_mode", 0) in {0, 1}:
                _log_skip(
                    "market_closed_or_symbol_not_tradable",
                    {"trade_mode": getattr(info, "trade_mode", None), "strategy_profile": strategy_profile_key},
                )
                return {"status": "skipped", "reason": "market_closed"}

            tick = connector.tick_for_account(broker_account, symbol)
            tick_time = getattr(tick, "time", None)
            tick_seconds = tick_time
            bid = getattr(tick, "bid", None)
            ask = getattr(tick, "ask", None)
            if bid is None or ask is None or Decimal(str(bid)) <= 0 or Decimal(str(ask)) <= 0:
                _log_skip("market_data_unavailable", {"strategy_profile": strategy_profile_key})
                return {"status": "skipped", "reason": "market_data_unavailable"}
            spread_points = Decimal(str(ask)) - Decimal(str(bid))
            tick_snapshot = {
                "bid": float(bid),
                "ask": float(ask),
                "last": float(getattr(tick, "last", 0.0)),
                "time": tick_time.isoformat() if isinstance(tick_time, datetime) else tick_time,
            }
            if not tick_seconds:
                tick_millis = getattr(tick, "time_msc", None)
                if tick_millis:
                    tick_seconds = tick_millis / 1000
            if isinstance(tick_seconds, datetime):
                tick_seconds = tick_seconds.timestamp()
            if not tick_seconds:
                _log_skip("market_data_timestamp_missing", {"strategy_profile": strategy_profile_key})
                return {"status": "skipped", "reason": "market_data_unavailable"}
            tick_dt = datetime.fromtimestamp(float(tick_seconds), tz=dt_timezone.utc)
            seconds_since_tick = (timezone.now() - tick_dt).total_seconds()
            if _tick_timestamp_is_stale(tick_dt):
                _log_skip(
                    "market_data_stale",
                    {"age_seconds": int(seconds_since_tick), "strategy_profile": strategy_profile_key},
                )
                return {"status": "skipped", "reason": "market_data_stale"}
        except Exception as exc:
            logger.warning(
                "[ScalperTrade] broker market data unavailable bot=%s symbol=%s: %s",
                bot.id,
                symbol,
                exc,
            )
            _log_skip("market_data_unavailable", {"strategy_profile": strategy_profile_key})
            return {"status": "skipped", "reason": "market_data_unavailable"}

        entry_candles = get_candles_for_account(
            broker_account=broker_account,
            symbol=symbol,
            timeframe=timeframe,
            n_bars=n_bars,
        )
    except Exception as e:
        task_failures_total.labels(task="trade_scalper_strategies_for_bot").inc()
        logger.exception(
            "[ScalperTrade] bot=%s symbol=%s tf=%s failed to fetch candles: %s",
            bot.id,
            symbol,
            timeframe,
            e,
        )
        raise
    
    if not entry_candles or len(entry_candles) < 20:
        logger.debug(
            "[ScalperTrade] bot=%s symbol=%s insufficient candles: %s",
            bot.id,
            symbol,
            len(entry_candles) if entry_candles else 0,
        )
        _log_skip(
            "insufficient_candles",
            {"candles": len(entry_candles) if entry_candles else 0, "strategy_profile": strategy_profile_key},
        )
        return {"status": "ok", "reason": "insufficient_candles"}
    
    last_entry = entry_candles[-1]
    entry_atr_points = _atr_like(entry_candles, period=14)
    bar_range = last_entry["high"] - last_entry["low"]

    volatility_snapshot = {
        "atr_points": str(entry_atr_points),
        "bar_range": str(bar_range),
        "tick_volume": last_entry.get("tick_volume"),
        "spread_points": str(spread_points) if spread_points is not None else None,
    }
    broker_snapshot = {
        "min_lot": str(broker_constraints.min_lot) if broker_constraints.min_lot is not None else None,
        "max_lot": str(broker_constraints.max_lot) if broker_constraints.max_lot is not None else None,
        "lot_step": str(broker_lot_step) if broker_lot_step is not None else None,
        "point": str(broker_point) if broker_point is not None else None,
        "stops_level_points": str(broker_min_stop_points) if broker_min_stop_points else None,
        "freeze_level_points": str(broker_constraints.freeze_level_points) if broker_constraints.freeze_level_points else None,
        "max_deviation": str(broker_constraints.max_deviation) if broker_constraints.max_deviation is not None else None,
    }
    market_snapshot = {
        "session": session_label,
        "last_close": str(last_entry["close"]),
        "tick": tick_snapshot,
        "volatility": volatility_snapshot,
        "broker_constraints": broker_snapshot,
    }
    
    signals_created = []
    decisions_made = []
    orders_placed = []
    strategy_events = []

    # Optional HTF bias (15m) to filter countertrend M1 entries
    htf_bias = None
    htf_bias_detail = None
    try:
        htf_candles = get_candles_for_account(
            broker_account=broker_account,
            symbol=symbol,
            timeframe="15m",
            n_bars=120,
        )
        analysis = _analyze_htf_bias(htf_candles)
        if analysis:
            htf_bias = analysis.get("bias")
            htf_bias_detail = analysis
    except Exception:
        htf_bias = None
        htf_bias_detail = None

    # Fallback: reuse last known bias if it is recent
    if htf_bias is None:
        try:
            last = (bot.scalper_params or {}).get("last_htf_bias", {})
            if last:
                ts = last.get("at")
                val = last.get("value")
                detail = last.get("info")
                if ts and val:
                    parsed = datetime.fromisoformat(ts)
                    age_min = (
                        (timezone.now() - timezone.make_aware(parsed, timezone=dt_timezone.utc))
                        if timezone.is_naive(parsed)
                        else (timezone.now() - parsed)
                    ).total_seconds() / 60
                    if age_min <= 60:
                        htf_bias = val
                        htf_bias_detail = detail
        except Exception:
            htf_bias = None
            htf_bias_detail = None

    # If we cannot establish HTF bias, skip this cycle to avoid trading blind.
    if htf_bias is None:
        logger.warning("[ScalperTrade] bot=%s symbol=%s proceeding without HTF bias", bot.id, symbol)

    # Cache latest bias for reuse
    try:
        params = bot.scalper_params or {}
        params["last_htf_bias"] = {
            "value": htf_bias,
            "at": timezone.now().isoformat(),
            "info": htf_bias_detail,
        }
        bot.scalper_params = params
        bot.save(update_fields=["scalper_params"])
    except Exception:
        pass
    
    available_pool: list[str] = []
    if auto_mode:
        available_pool = profile_strats or list(SCALPER_STRATEGY_REGISTRY.keys())
        auto_selected = select_ai_strategies(
            engine_mode="scalper",
            available=available_pool,
            symbol=canonical_sym,
            context={
                "atr_points": entry_atr_points,
                "bar_range": bar_range,
                "last_close": last_entry["close"],
                "spread_points": spread_points,
                "session": session_label,
                "htf_bias": htf_bias,
            },
        )
        enabled_strats = list(auto_selected)
        strategy_context["auto_selected"] = list(auto_selected)

    disabled_profile_strats = set(strategy_profile.disabled_strategies if strategy_profile else [])
    enabled_strats = [
        s
        for s in enabled_strats
        if s in SCALPER_STRATEGY_REGISTRY and s not in disabled_profile_strats
    ]
    if auto_mode:
        if not enabled_strats:
            fallback_pool = [
                s for s in (available_pool or SCALPER_STRATEGY_REGISTRY.keys())
                if s not in disabled_profile_strats
            ]
            enabled_strats = list(fallback_pool)[:3]
            strategy_context["auto_fallback_used"] = True
        strategy_context["active"] = enabled_strats.copy()
    else:
        strategy_context["active"] = enabled_strats.copy()
    if not enabled_strats:
        _log_skip(
            "no_active_strategies",
            {"strategy_context": strategy_context, "strategy_profile": strategy_profile_key},
        )
        return {"status": "ok", "reason": "no_active_strategies"}
    
    # Run each enabled strategy
    for strategy_name in enabled_strats:
        strategy_entry = SCALPER_STRATEGY_REGISTRY.get(strategy_name)
        if not strategy_entry:
            logger.debug(
                "[ScalperTrade] bot=%s strategy %s not implemented yet, skipping",
                bot.id,
                strategy_name,
            )
            continue

        engine_decision = None
        try:
            cfg = strategy_entry.config_factory()
            if strategy_entry.requires_symbol:
                engine_decision = strategy_entry.runner(symbol, entry_candles, cfg)
            else:
                engine_decision = strategy_entry.runner(entry_candles, cfg)
        except Exception as e:
            logger.exception(
                "[ScalperTrade] bot=%s strategy=%s candle processing failed: %s",
                bot.id,
                strategy_name,
                e,
            )
            continue
        strategy_events.append(
            {
                "strategy": strategy_name,
                "action": engine_decision.action,
                "reason": engine_decision.reason,
                "score": float(engine_decision.score or 0.0),
                "metadata": engine_decision.metadata or {},
            }
        )
        
        # Safety check - should never happen, but guard against it
        if engine_decision is None:
            logger.warning(
                "[ScalperTrade] bot=%s strategy=%s returned None decision, skipping",
                bot.id,
                strategy_name,
            )
            continue
        
        # Skip if strategy doesn't emit "open"
        if engine_decision.action != "open" or not engine_decision.direction:
            logger.debug(
                "[ScalperTrade] bot=%s strategy=%s action=%s reason=%s",
                bot.id,
                strategy_name,
                engine_decision.action,
                engine_decision.reason,
            )
            continue
        
        # Create deterministic dedupe key per strategy/bar/bot to avoid duplicate signals
        last_bar_time = entry_candles[-1]["time"]
        if hasattr(last_bar_time, "isoformat"):
            time_str = last_bar_time.isoformat()
        else:
            time_str = str(last_bar_time)
        
        dedupe_key = f"scalper:{bot.id}:{symbol}:{timeframe}:{strategy_name}:{time_str}"
        
        # Create or reuse signal
        try:
            strategy_payload = {
                "strategy": strategy_name,
                "sl": str(engine_decision.sl) if engine_decision.sl is not None else None,
                "tp": str(engine_decision.tp) if engine_decision.tp is not None else None,
                "reason": engine_decision.reason,
                "score": float(engine_decision.score or 0.0),
                "generated_at": timezone.now().isoformat(),
                "session": session_label,
                "close": str(last_entry.get("close")),
                "atr_points": str(entry_atr_points),
                "tick_volume": last_entry.get("tick_volume"),
                "spread_points": str(spread_points) if spread_points is not None else None,
                "point": str(broker_point) if broker_point is not None else None,
                "min_stop_points": str(broker_min_stop_points) if broker_min_stop_points else None,
                "lot_step": str(broker_lot_step) if broker_lot_step is not None else None,
                "broker_constraints": broker_snapshot,
                "market_snapshot": market_snapshot,
                "volatility": volatility_snapshot,
                "strategy_metrics": engine_decision.metadata or {},
                **({"bias_m15": htf_bias} if htf_bias else {}),
            }
            if htf_bias_detail:
                strategy_payload["htf_bias_detail"] = htf_bias_detail
            signal, signal_created = Signal.objects.get_or_create(
                dedupe_key=dedupe_key,
                defaults={
                    "bot": bot,
                    "source": "scalper_engine",
                    "symbol": symbol,
                    "timeframe": timeframe,
                    "direction": engine_decision.direction,
                    "payload": strategy_payload,
                }
            )
            signals_created.append((signal.id, signal_created))
        except Exception as e:
            logger.exception(
                "[ScalperTrade] bot=%s strategy=%s failed to create signal: %s",
                bot.id,
                strategy_name,
                e,
            )
            continue
        
        # Make decision from signal
        try:
            existing_decision = Decision.objects.filter(signal=signal, action="open").first()
            if existing_decision:
                decision = existing_decision
                logger.debug(
                    "[ScalperTrade] bot=%s signal=%s reusing existing decision=%s",
                    bot.id,
                    signal.id,
                    decision.id,
                )
            else:
                decision = make_decision_from_signal(signal)
            decisions_made.append((decision.id, decision.action))
        except Exception as e:
            task_failures_total.labels(task="trade_scalper_strategies_for_bot").inc()
            logger.exception(
                "[ScalperTrade] bot=%s signal=%s decision failed: %s",
                bot.id,
                signal.id,
                e,
            )
            raise
        
        if decision.action != "open":
            logger.debug(
                "[ScalperTrade] decision ignored: bot=%s signal=%s action=%s reason=%s",
                bot.id,
                signal.id,
                decision.action,
                decision.reason,
            )
            continue
        
        # Fanout to orders and dispatch
        try:
            for order, created in fanout_orders(decision, master_qty=None):
                should_dispatch = created or order.status in ("new", "ack")
                if should_dispatch:
                    # Apply unit-aware, broker-aware guards before dispatch
                    try:
                        constraints = get_broker_symbol_constraints(order.broker_account, order.symbol)
                    except Exception:
                        constraints = None
                    point = getattr(constraints, "point", None) if constraints else None
                    min_stop_pts = getattr(constraints, "stops_level_points", None) if constraints else None
                    spread = None
                    try:
                        spread = (decision.signal.payload or {}).get("spread_points") if decision.signal and decision.signal.payload else None
                    except Exception:
                        spread = None
                    entry_hint = None
                    try:
                        params = decision.params or {}
                        for key in ("entry", "price", "close", "last_price"):
                            if params.get(key):
                                entry_hint = Decimal(str(params.get(key)))
                                break
                        if entry_hint is None and decision.signal and decision.signal.payload:
                            payload = decision.signal.payload or {}
                            for key in ("entry", "price", "close", "last_price"):
                                if payload.get(key):
                                    entry_hint = Decimal(str(payload.get(key)))
                                    break
                    except Exception:
                        entry_hint = None
                    if entry_hint is None:
                        try:
                            px = get_price(order.broker_account, order.symbol)
                            if px is not None:
                                entry_hint = Decimal(str(px))
                        except Exception:
                            entry_hint = None
                    sl_distance_price = Decimal("0")
                    try:
                        if entry_hint is not None and order.sl is not None:
                            sl_distance_price = abs(Decimal(str(order.sl)) - entry_hint)
                    except Exception:
                        sl_distance_price = Decimal("0")
                    guard_inputs = GuardInputs(
                        sl_distance=sl_distance_price,
                        sl_unit="price",
                        spread=Decimal(str(spread)) if spread is not None else None,
                        spread_unit="price",
                        point=point,
                        min_stop_points=min_stop_pts,
                    )
                    guard = apply_order_guards(guard_inputs)
                    if not guard.ok:
                        log_journal_event(
                            "order.dispatch_error",
                            severity="warning",
                            order=order,
                            bot=order.bot,
                            broker_account=order.broker_account,
                            symbol=order.symbol,
                            message="Order skipped by guard",
                            context={
                                "reason": guard.reason,
                                "sl_distance_price": str(sl_distance_price),
                                "spread_price": str(spread) if spread is not None else None,
                                "min_stop_points": str(min_stop_pts) if min_stop_pts is not None else None,
                                "point": str(point) if point is not None else None,
                            },
                        )
                        continue
                    try:
                        _queue_or_dispatch_order(order)
                        orders_placed.append((order.id, order.symbol, order.side))
                    except Exception as e:
                        log_journal_event(
                            "order.dispatch_error",
                            severity="error",
                            order=order,
                            bot=order.bot,
                            broker_account=order.broker_account,
                            symbol=order.symbol,
                            message="Scalper dispatch failed",
                            context={
                                "bot_id": bot.id,
                                "strategy": strategy_name,
                                "error": str(e),
                            },
                        )
                        logger.exception(
                            "[ScalperTrade] bot=%s strategy=%s failed to dispatch order %s: %s",
                            bot.id,
                            strategy_name,
                            order.id,
                            e,
                        )
        except Exception as e:
            logger.exception(
                "[ScalperTrade] bot=%s strategy=%s fanout failed: %s",
                bot.id,
                strategy_name,
                e,
            )
            continue
    
    # Log summary with clearer outcome/context for UI
    if orders_placed:
        outcome = "orders_sent"
    elif decisions_made:
        outcome = "decisions_made_no_orders"
    elif signals_created:
        outcome = "signals_generated_no_decisions"
    else:
        outcome = "no_signals"

    log_journal_event(
        "scalper_engine_run",
        bot=bot,
        owner=bot.owner if bot else None,
        symbol=symbol,
        message=(
            f"Scalper run tf={timeframe} signals={len(signals_created)} "
            f"decisions={len(decisions_made)} orders={len(orders_placed)} "
            f"profile={strategy_profile_key} session={session_label}"
        ),
        context={
            "timeframe": timeframe,
            "session": session_label,
            "auto_trade_active": auto_mode,
            "strategies_enabled": enabled_strats,
            "strategy_profile": strategy_profile_key,
            "outcome": outcome,
            "signals": len(signals_created),
            "decisions": len(decisions_made),
            "orders": len(orders_placed),
            "strategy_context": strategy_context,
        },
    )
    
    logger.info(
        "[ScalperTrade] bot=%s symbol=%s strategies=%s signals=%s decisions=%s orders=%s",
        bot.id,
        symbol,
        len(enabled_strats),
        len(signals_created),
        len(decisions_made),
        len(orders_placed),
    )
    
    if not signals_created:
        summary = {
            "strategies": strategy_events,
            "market": market_snapshot,
            "htf_bias": htf_bias,
            "htf_bias_detail": htf_bias_detail,
            "generated_at": timezone.now().isoformat(),
        }
        try:
            ScalperRunLog.objects.create(
                bot=bot,
                timeframe=timeframe,
                session=session_label,
                summary=_json_safe(summary),
            )
        except Exception:
            logger.exception("[ScalperTrade] failed to persist run log bot=%s", bot.id)

    return {
        "status": "ok",
        "bot_id": bot.id,
        "symbol": symbol,
        "timeframe": timeframe,
        "strategies_enabled": enabled_strats,
        "signals": len(signals_created),
        "decisions": len(decisions_made),
        "orders": len(orders_placed),
    }


@shared_task(
    bind=True,
    autoretry_for=(Exception,),
    retry_backoff=True,
    retry_backoff_max=300,
    retry_jitter=True,
    retry_kwargs={"max_retries": 3},
)
def run_harami_engine_for_all_bots(self, timeframe: str = "5m", n_bars: int = 200):
    """
    Periodic runner for the internal engine.

    - Picks bots with engine_mode="harami", auto_trade=True, status="active"
    - Uses bot.default_timeframe unless overridden by `timeframe`
    - Uses bot.asset (required) and respects Bot.accepts(...)
    """
    from bots.models import Bot  # local import to avoid circulars

    bots_qs = (
        Bot.objects.select_related("broker_account")
        .filter(
            auto_trade=True,
            status="active",
            engine_mode="harami",
        )
    )

    dispatched = 0
    skipped_no_broker = 0
    skipped_no_symbols = 0
    skipped_not_accepted = 0

    for bot in bots_qs:
        broker_account = getattr(bot, "broker_account", None)
        if not broker_account or not getattr(broker_account, "is_active", False):
            skipped_no_broker += 1
            continue

        symbol = getattr(bot.asset, "symbol", None)
        if not symbol:
            skipped_no_symbols += 1
            continue

        tf = bot.default_timeframe or timeframe

        if not bot.accepts(symbol, tf):
            skipped_not_accepted += 1
            continue

        trade_harami_for_bot.delay(bot.id, timeframe=tf, n_bars=n_bars)
        dispatched += 1

    logger.info(
        "[HaramiRunner] default_tf=%s dispatched=%s skipped_no_broker=%s skipped_no_symbols=%s skipped_not_accepted=%s",
        timeframe,
        dispatched,
        skipped_no_broker,
        skipped_no_symbols,
        skipped_not_accepted,
    )

    return {
        "status": "ok",
        "default_timeframe": timeframe,
        "dispatched": dispatched,
        "skipped_no_broker": skipped_no_broker,
        "skipped_no_symbols": skipped_no_symbols,
        "skipped_not_accepted": skipped_not_accepted,
    }




@shared_task(
    bind=True,
    autoretry_for=(Exception,),
    retry_backoff=True,
    retry_backoff_max=300,
    retry_jitter=True,
    retry_kwargs={"max_retries": 5},
)
def kill_switch_monitor_task(self):
    """
    One account-level kill-switch policy using broker equity snapshots.
    """
    from execution.services.orchestrator import create_close_order_for_position

    connector = MT5Connector()
    triggered = []
    closed = []
    for policy in RiskPolicy.objects.select_related("broker_account").all():
        account = policy.broker_account
        if not account.is_active:
            continue
        info = connector.account_info_for_account(account)
        snapshot = AccountSnapshot.objects.create(
            broker_account=account,
            balance=Decimal(str(getattr(info, "balance", 0) or 0)),
            equity=Decimal(str(getattr(info, "equity", 0) or 0)),
            margin=Decimal(str(getattr(info, "margin", 0) or 0)),
            free_margin=Decimal(str(getattr(info, "margin_free", 0) or 0)),
            margin_level=Decimal(str(getattr(info, "margin_level", 0) or 0)),
            currency=str(getattr(info, "currency", "") or ""),
        )
        today_values = list(
            AccountSnapshot.objects.filter(
                broker_account=account,
                captured_at__date=timezone.localdate(),
            ).order_by("captured_at").values_list("equity", flat=True)
        )
        start = today_values[0] if today_values else snapshot.equity
        peak = max(today_values) if today_values else snapshot.equity
        daily_loss = ((start - snapshot.equity) / start * 100) if start > 0 else Decimal("0")
        drawdown = ((peak - snapshot.equity) / peak * 100) if peak > 0 else Decimal("0")
        reason = None
        if policy.emergency_stop:
            reason = "explicit_emergency_stop"
        elif daily_loss >= policy.max_daily_loss_pct:
            reason = "maximum_daily_loss"
        elif drawdown >= policy.max_account_drawdown_pct:
            reason = "maximum_account_drawdown"
        if reason is None:
            continue
        policy.entries_enabled = False
        policy.emergency_stop = True
        policy.save(update_fields=["entries_enabled", "emergency_stop", "updated_at"])
        triggered.append({"broker_account_id": account.id, "reason": reason})
        log_journal_event(
            "kill_switch.triggered",
            severity="error",
            broker_account=account,
            owner=account.owner,
            message=f"Kill switch triggered: {reason}",
            context={"daily_loss_pct": str(daily_loss), "drawdown_pct": str(drawdown)},
        )
        if policy.emergency_close_owned_positions:
            for position in BrokerPosition.objects.filter(
                broker_account=account,
                ownership="ez_trade",
                status="open",
            ):
                try:
                    order, _ = create_close_order_for_position(position, account)
                    _queue_or_dispatch_order(order)
                    closed.append(position.broker_position_ticket)
                except Exception:
                    task_failures_total.labels(task="kill_switch_monitor_task").inc()
                    logger.exception("Kill switch could not close owned ticket=%s", position.broker_position_ticket)
    return {"triggered": triggered, "closed_owned_tickets": closed}


@shared_task(
    bind=True,
    autoretry_for=(Exception,),
    retry_backoff=True,
    retry_backoff_max=300,
    retry_jitter=True,
    retry_kwargs={"max_retries": 3},
)
def cancel_stale_orders_task(self, max_age_seconds: int | None = None):
    """
    Reconcile stale submitted orders with MT5 before changing local state.
    """
    runtime_cfg = get_runtime_config()
    timeout = max_age_seconds or int(runtime_cfg.order_ack_timeout_seconds)
    cutoff = timezone.now() - timedelta(seconds=timeout)
    # Use updated_at to catch orders that were recently touched by broker transitions.
    stale_qs = Order.objects.filter(status__in=["new", "ack", "part_filled"], updated_at__lt=cutoff)

    reconciled = []
    unresolved = []
    rejected = []
    canceled_local = []
    connector = MT5Connector()
    for order in stale_qs:
        try:
            if order.status == "new" and order.submitted_at is None:
                update_order_status(order, "canceled", error_msg="Local order expired before broker submission")
                canceled_local.append(order.id)
                continue
            if connector.reconcile_order(order):
                reconciled.append(order.id)
                continue
            ambiguous = order.attempts.filter(status__in=["submitting", "ambiguous"]).exists()
            if ambiguous and order.updated_at >= timezone.now() - timedelta(minutes=5):
                unresolved.append(order.id)
                continue
            update_order_status(
                order,
                "rejected",
                error_msg="No matching MT5 order, deal, or position found after reconciliation",
            )
            rejected.append(order.id)
        except Exception as e:
            logger.exception("[StaleCancel] failed for order %s: %s", order.id, e)
            task_failures_total.labels(task="cancel_stale_orders_task").inc()

    if reconciled or rejected or canceled_local or unresolved:
        log_journal_event(
            "order.stale_reconciliation",
            severity="warning",
            message="Completed stale-order broker reconciliation",
            context={
                "reconciled": reconciled,
                "rejected": rejected,
                "canceled_local": canceled_local,
                "unresolved": unresolved,
                "timeout_sec": timeout,
            },
        )
    return {
        "reconciled": reconciled,
        "rejected": rejected,
        "canceled_local": canceled_local,
        "unresolved": unresolved,
        "timeout_sec": timeout,
    }


@shared_task(
    bind=True,
    autoretry_for=(Exception,),
    retry_backoff=True,
    retry_backoff_max=300,
    retry_jitter=True,
    retry_kwargs={"max_retries": 3},
)
def reconcile_broker_positions_task(self):
    """
    Import broker-authoritative positions by ticket. Unknown/manual positions are
    visible but are never closed or modified automatically.
    """
    connector = MT5Connector()
    imported_owned = []
    imported_external = []
    imported_closes = []
    missing_local = []
    errors = []

    accounts = BrokerAccount.objects.filter(is_active=True, connector="mt5_local")
    for acct in accounts:
        if not is_mt5_available():
            logger.warning("[Recon] MetaTrader5 library unavailable; skipping acct=%s", acct.id)
            continue
        try:
            mt5_positions = connector.positions_for_account(acct)
        except Exception as e:
            errors.append((acct.id, str(e)))
            logger.exception("[Recon] login failed for acct=%s: %s", acct.id, e)
            continue

        broker_tickets = set()
        for pos in mt5_positions or []:
            ticket = getattr(pos, "ticket", None) or getattr(pos, "identifier", None)
            if not ticket:
                continue
            ticket = int(ticket)
            broker_tickets.add(ticket)
            order = Order.objects.filter(
                broker_account=acct,
                broker_position_ticket=ticket,
            ).order_by("-created_at").first()
            magic = int(getattr(pos, "magic", 0) or 0)
            comment = str(getattr(pos, "comment", "") or "")
            is_owned = bool(
                order
                or (
                    magic == int(getattr(settings, "MT5_MAGIC_NUMBER", 20250813))
                    and comment.startswith(("ez:", "ezc:"))
                )
            )
            try:
                connector._sync_broker_position(
                    order,
                    pos,
                    broker_account=acct,
                    ownership="ez_trade" if is_owned else ("manual" if not comment else "external"),
                )
                target = imported_owned if is_owned else imported_external
                target.append(ticket)
            except Exception as e:
                errors.append((acct.id, ticket, str(e)))
                logger.exception("[Recon] failed to import broker position acct=%s ticket=%s", acct.id, ticket)

        for local in acct.broker_positions.filter(status="open"):
            if local.broker_position_ticket not in broker_tickets:
                try:
                    closed_deals = _reconcile_missing_owned_position(connector, local)
                except Exception as e:
                    closed_deals = []
                    errors.append((acct.id, local.broker_position_ticket, str(e)))
                    logger.exception(
                        "[Recon] failed to reconcile closed position acct=%s ticket=%s",
                        acct.id,
                        local.broker_position_ticket,
                    )
                if closed_deals:
                    imported_closes.extend(closed_deals)
                else:
                    local.status = "missing"
                    local.last_reconciled_at = timezone.now()
                    local.save(update_fields=["status", "last_reconciled_at"])
                    missing_local.append(local.broker_position_ticket)

    if imported_owned or imported_external or imported_closes or missing_local or errors:
        log_journal_event(
            "broker.reconcile",
            severity="info",
            message="Broker reconciliation summary",
            context={
                "imported_owned": imported_owned,
                "imported_external": imported_external,
                "imported_closes": imported_closes,
                "missing_local": missing_local,
                "errors": errors,
            },
        )
    return {
        "imported_owned": imported_owned,
        "imported_external": imported_external,
        "imported_closes": imported_closes,
        "missing_local": missing_local,
        "errors": errors,
    }


@shared_task(
    bind=True,
    autoretry_for=(Exception,),
    retry_backoff=True,
    retry_backoff_max=300,
    retry_jitter=True,
    retry_kwargs={"max_retries": 3},
)
def market_hours_guard_task(self):
    """
    Background guard to auto-stop bots whose market is closed and resume them when open.
    Uses a reversible flag in scalper_params to avoid touching manually stopped bots.
    """
    result = apply_market_guard()
    logger.info(
        "[MarketGuard] stopped=%s resumed=%s skipped_crypto=%s skipped_no_asset=%s errors=%s",
        result["stopped"],
        result["resumed"],
        result["skipped_crypto"],
        result["skipped_no_asset"],
        len(result["errors"]),
    )
    return result


# Celery autodiscovery imports this module. Re-export the dedicated queue tasks
# so execution.mt5_tasks is registered without exposing MT5 to another worker.
from execution.mt5_tasks import (  # noqa: E402,F401
    check_mt5_account_task,
    execute_mt5_order_task,
    modify_mt5_position_task,
    reconcile_mt5_order_task,
    refresh_mt5_markets_task,
)
