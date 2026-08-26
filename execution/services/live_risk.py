from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal, ROUND_FLOOR

from django.db import transaction
from django.utils import timezone

from execution.models import AccountSnapshot, BrokerPosition, Order, RiskPolicy
from execution.services.scalper_config import build_scalper_config
from execution.services.trade_constraints import distance_to_price


class RiskRejected(ValueError):
    pass


@dataclass(frozen=True)
class PreTradeRiskResult:
    volume: Decimal
    entry_price: Decimal
    margin_required: Decimal
    risk_amount: Decimal
    loss_per_lot: Decimal
    spread_points: Decimal
    spread_limit_points: Decimal
    deviation_points: int


def _decimal(value, default="0") -> Decimal:
    try:
        return Decimal(str(value))
    except Exception:
        return Decimal(default)


def _floor_to_step(value: Decimal, step: Decimal) -> Decimal:
    if step <= 0:
        return value
    return (value / step).to_integral_value(rounding=ROUND_FLOOR) * step


def _scalper_symbol_limit_points(order: Order, point: Decimal, value_field: str, unit_field: str):
    """Return a scalper profile limit converted to raw MT5 points."""
    bot = getattr(order, "bot", None)
    if not bot or getattr(bot, "engine_mode", "") != "scalper" or point <= 0:
        return None
    try:
        symbol_config = build_scalper_config(bot).resolve_symbol(order.symbol)
        if symbol_config is None:
            return None
        value = _decimal(getattr(symbol_config, value_field, 0))
        unit = getattr(symbol_config, unit_field, "points")
        price_limit = distance_to_price(value, unit, point)
        return price_limit / point if price_limit > 0 else None
    except Exception:
        return None


@transaction.atomic
def enforce_pretrade_risk(order: Order, connector, tick, symbol_info, account_info) -> PreTradeRiskResult:
    """Enforce broker-aware monetary risk immediately before ``order_send``."""
    locked_order = Order.objects.select_for_update().select_related("broker_account", "bot").get(pk=order.pk)
    account = locked_order.broker_account
    policy, _ = RiskPolicy.objects.select_for_update().get_or_create(broker_account=account)

    if locked_order.intent != "entry":
        raise RiskRejected("Pre-trade entry risk called for a non-entry order")
    if policy.emergency_stop:
        raise RiskRejected("Emergency stop is active")
    if not policy.entries_enabled:
        raise RiskRejected("New entries are disabled until the risk policy is explicitly enabled")
    if not account.is_active or not account.is_verified:
        raise RiskRejected("Broker account is not active and verified")
    if not locked_order.bot.auto_trade or locked_order.bot.status != "active":
        raise RiskRejected("Bot is not enabled for new automated entries")

    # MT5: 0=demo, 1=contest, 2=real. Unknown values remain visible but the
    # explicit entries_enabled switch is still required above.
    trade_mode = getattr(account_info, "trade_mode", None)
    if trade_mode == 2 and not policy.live_trading_confirmed:
        raise RiskRejected("Live MT5 account requires explicit live-trading confirmation")

    equity = _decimal(getattr(account_info, "equity", 0))
    balance = _decimal(getattr(account_info, "balance", 0))
    margin = _decimal(getattr(account_info, "margin", 0))
    free_margin = _decimal(getattr(account_info, "margin_free", 0))
    margin_level = _decimal(getattr(account_info, "margin_level", 0))
    if equity <= 0 or free_margin < 0:
        raise RiskRejected("MT5 account equity/free margin is unavailable")

    snapshot = AccountSnapshot.objects.create(
        broker_account=account,
        balance=balance,
        equity=equity,
        margin=margin,
        free_margin=free_margin,
        margin_level=margin_level,
        currency=str(getattr(account_info, "currency", "") or ""),
    )
    today = timezone.localdate(snapshot.captured_at)
    day_snapshots = AccountSnapshot.objects.filter(
        broker_account=account,
        captured_at__date=today,
    ).order_by("captured_at")
    start_equity = day_snapshots.first().equity
    peak_equity = max(day_snapshots.values_list("equity", flat=True), default=equity)
    daily_loss_pct = ((start_equity - equity) / start_equity * 100) if start_equity > 0 else Decimal("0")
    drawdown_pct = ((peak_equity - equity) / peak_equity * 100) if peak_equity > 0 else Decimal("0")
    daily_profit_pct = ((equity - start_equity) / start_equity * 100) if start_equity > 0 else Decimal("0")
    if daily_loss_pct >= policy.max_daily_loss_pct:
        raise RiskRejected("Maximum daily loss reached")
    if drawdown_pct >= policy.max_account_drawdown_pct:
        raise RiskRejected("Maximum account drawdown reached")
    if policy.stop_after_daily_profit_pct > 0 and daily_profit_pct >= policy.stop_after_daily_profit_pct:
        raise RiskRejected("Daily profit stop reached; new entries are disabled for today")

    open_positions = BrokerPosition.objects.filter(broker_account=account, status="open")
    if open_positions.count() >= policy.max_positions:
        raise RiskRejected("Maximum open positions reached")
    if open_positions.filter(symbol=locked_order.symbol).count() >= policy.max_positions_per_symbol:
        raise RiskRejected("Maximum positions for symbol reached")

    entries_today = Order.objects.filter(
        broker_account=account,
        intent="entry",
        status__in=["ack", "part_filled", "filled"],
        submitted_at__date=today,
    ).exclude(pk=locked_order.pk).count()
    if entries_today >= policy.max_entry_trades_per_day:
        raise RiskRejected("Maximum daily entry trades reached")

    bid = _decimal(getattr(tick, "bid", 0))
    ask = _decimal(getattr(tick, "ask", 0))
    point = _decimal(getattr(symbol_info, "point", 0))
    if bid <= 0 or ask <= 0 or ask < bid or point <= 0:
        raise RiskRejected("Fresh broker bid/ask and point size are required")
    spread_points = (ask - bid) / point
    symbol_spread_limit = _scalper_symbol_limit_points(
        locked_order,
        point,
        "max_spread_points",
        "max_spread_unit",
    )
    spread_limit_points = symbol_spread_limit or policy.max_spread_points
    if spread_limit_points > 0 and spread_points > spread_limit_points:
        raise RiskRejected("Spread exceeds configured limit")

    symbol_deviation_limit = _scalper_symbol_limit_points(
        locked_order,
        point,
        "max_slippage_points",
        "max_slippage_unit",
    )
    deviation_points = int(symbol_deviation_limit or policy.deviation_points)

    entry = ask if locked_order.side == "buy" else bid
    if locked_order.sl is None:
        raise RiskRejected("Stop loss is required")
    stop = _decimal(locked_order.sl)
    take_profit = _decimal(locked_order.tp) if locked_order.tp is not None else None
    if locked_order.side == "buy" and not (stop < entry and (take_profit is None or take_profit > entry)):
        raise RiskRejected("BUY protection is on the wrong side of the market")
    if locked_order.side == "sell" and not (stop > entry and (take_profit is None or take_profit < entry)):
        raise RiskRejected("SELL protection is on the wrong side of the market")

    stops_level = _decimal(getattr(symbol_info, "trade_stops_level", None) or getattr(symbol_info, "stops_level", 0))
    min_distance = stops_level * point
    if min_distance > 0 and abs(entry - stop) < min_distance:
        raise RiskRejected("Stop loss violates the broker stop-distance rule")

    loss_per_lot = abs(
        connector.calc_profit_for_account(account, locked_order.side, locked_order.symbol, 1, entry, stop)
    )
    if loss_per_lot <= 0:
        raise RiskRejected("Broker monetary loss calculation failed")
    risk_amount = equity * policy.risk_per_trade_pct / Decimal("100")
    if risk_amount <= 0:
        raise RiskRejected("Risk amount is not positive")
    volume = risk_amount / loss_per_lot

    volume_min = _decimal(getattr(symbol_info, "volume_min", 0))
    volume_max = _decimal(getattr(symbol_info, "volume_max", 0))
    volume_step = _decimal(getattr(symbol_info, "volume_step", 0))
    effective_max = policy.max_lot
    if volume_max > 0:
        effective_max = min(effective_max, volume_max) if effective_max > 0 else volume_max
    volume = min(volume, effective_max) if effective_max > 0 else volume
    volume = _floor_to_step(volume, volume_step)
    if volume <= 0 or (volume_min > 0 and volume < volume_min):
        raise RiskRejected("Risk-sized volume is below the broker minimum")

    margin_required = connector.calc_margin_for_account(
        account,
        locked_order.side,
        locked_order.symbol,
        volume,
        entry,
    )
    if margin_required <= 0 or margin_required > free_margin * Decimal("0.90"):
        raise RiskRejected("Insufficient free margin with required safety buffer")

    digits = int(getattr(symbol_info, "digits", 0) or 0)
    quantum = Decimal("1").scaleb(-digits) if digits >= 0 else point
    locked_order.qty = volume
    locked_order.remaining_qty = volume
    locked_order.requested_price = entry.quantize(quantum)
    locked_order.sl = stop.quantize(quantum)
    if take_profit is not None:
        locked_order.tp = take_profit.quantize(quantum)
    locked_order.save(update_fields=["qty", "remaining_qty", "requested_price", "sl", "tp"])

    order.qty = locked_order.qty
    order.remaining_qty = locked_order.remaining_qty
    order.requested_price = locked_order.requested_price
    order.sl = locked_order.sl
    order.tp = locked_order.tp
    return PreTradeRiskResult(
        volume=volume,
        entry_price=entry,
        margin_required=margin_required,
        risk_amount=risk_amount,
        loss_per_lot=loss_per_lot,
        spread_points=spread_points,
        spread_limit_points=spread_limit_points,
        deviation_points=deviation_points,
    )
