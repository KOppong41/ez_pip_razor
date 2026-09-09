from __future__ import annotations

from dataclasses import dataclass
from datetime import timedelta
from decimal import Decimal, ROUND_FLOOR

from django.db import transaction
from django.db.models import Sum
from django.utils import timezone

from execution.models import BrokerPosition, Order, RiskPolicy
from execution.services.daily_risk import (
    create_account_snapshot,
    daily_equity_change_pcts,
    risk_day_window,
    update_account_risk_day,
)
from execution.services.equity import update_equity_high_water
from execution.services.runtime_config import get_runtime_config
from execution.services.scalper_config import build_scalper_config
from execution.services.trade_constraints import distance_to_price


class RiskRejected(ValueError):
    """A safe, machine-readable final execution rejection."""

    def __init__(self, code: str, message: str, *, context: dict | None = None):
        self.code = code
        self.message = message
        self.context = context or {}
        super().__init__(message)

    def as_dict(self) -> dict:
        return {"code": self.code, "message": self.message, "context": self.context}


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
    effective_risk_pct: Decimal = Decimal("0")


def _decimal(value, default="0") -> Decimal:
    try:
        return Decimal(str(value))
    except Exception:
        return Decimal(default)


def _floor_to_step(value: Decimal, step: Decimal) -> Decimal:
    if step <= 0:
        return value
    return (value / step).to_integral_value(rounding=ROUND_FLOOR) * step


def _positive_min(*values: Decimal) -> Decimal:
    enabled = [value for value in values if value > 0]
    return min(enabled) if enabled else Decimal("0")


def _scalper_symbol_limit_points(
    order: Order,
    point: Decimal,
    value_field: str,
    unit_field: str,
    *,
    market_price: Decimal | None = None,
    digits: int | None = None,
):
    bot = getattr(order, "bot", None)
    if not bot or getattr(bot, "engine_mode", "") != "scalper" or point <= 0:
        return None
    try:
        symbol_config = build_scalper_config(bot).resolve_symbol(order.symbol)
        if symbol_config is None:
            return None
        value = _decimal(getattr(symbol_config, value_field, 0))
        unit = getattr(symbol_config, unit_field, "points")
        price_limit = distance_to_price(
            value,
            unit,
            point,
            market_price=market_price,
            digits=digits,
        )
        return price_limit / point if price_limit > 0 else None
    except Exception:
        return None


@transaction.atomic
def enforce_pretrade_risk(
    order: Order,
    connector,
    tick,
    symbol_info,
    account_info,
    *,
    broker_positions=None,
) -> PreTradeRiskResult:
    """Apply all final bot/account checks immediately before MT5 submission."""
    locked_order = (
        Order.objects.select_for_update()
        # Decision is nullable; joining it under FOR UPDATE is rejected by
        # PostgreSQL. The order/account rows are the serialization boundary.
        .select_related("broker_account", "bot")
        .get(pk=order.pk)
    )
    account_model = type(locked_order.broker_account)
    account = account_model.objects.select_for_update().get(pk=locked_order.broker_account_id)
    policy, _ = RiskPolicy.objects.select_for_update().get_or_create(broker_account=account)
    bot = locked_order.bot
    now = timezone.now()
    base_context = {
        "bot_id": bot.id,
        "broker_account_id": account.id,
        "symbol": locked_order.symbol,
        "requested_volume": str(locked_order.qty),
        "timestamp": now.isoformat(),
    }

    def reject(code: str, message: str, **context):
        raise RiskRejected(code, message, context={**base_context, **context})

    if locked_order.intent != "entry":
        reject("INVALID_ORDER_INTENT", "Pre-trade entry risk called for a non-entry order")
    if policy.emergency_stop:
        reject("EMERGENCY_STOP_ACTIVE", "Emergency stop is active")
    if not policy.entries_enabled:
        reject("ACCOUNT_ENTRIES_DISABLED", "New entries are disabled until the account risk policy is enabled")
    if not account.is_active or not account.is_verified:
        reject("ACCOUNT_UNAVAILABLE", "Broker account is not active and verified")
    if not bot.auto_trade or bot.status != "active":
        reject("BOT_DISABLED", "Bot is not enabled for new automated entries")
    if locked_order.decision_id and locked_order.decision.score < bot.decision_min_score:
        reject(
            "BOT_SIGNAL_SCORE",
            "Signal score is below this bot's minimum",
            signal_score=str(locked_order.decision.score),
            bot_limit=str(bot.decision_min_score),
        )

    trade_mode = getattr(account_info, "trade_mode", None)
    if trade_mode not in {0, 1, 2}:
        reject("ACCOUNT_MODE_UNKNOWN", "Unknown or unsupported MT5 account trade mode")
    if trade_mode == 2 and not bot.allow_live_account_execution:
        reject("BOT_LIVE_EXECUTION_DISABLED", "This bot is not allowed to execute on a live-money account")

    equity = _decimal(getattr(account_info, "equity", 0))
    free_margin = _decimal(getattr(account_info, "margin_free", 0))
    if equity <= 0 or free_margin < 0:
        reject("ACCOUNT_FINANCIALS_UNAVAILABLE", "MT5 account equity/free margin is unavailable")

    snapshot = create_account_snapshot(account, account_info)
    risk_day = update_account_risk_day(
        account,
        snapshot,
        connector=connector,
        trade_mode=trade_mode,
        broker_positions=broker_positions,
    )
    if not risk_day.baseline_locked:
        reject("ACCOUNT_DAILY_BASELINE_UNAVAILABLE", "Daily risk baseline is unavailable; entries remain blocked")
    daily_loss_pct, daily_profit_pct = daily_equity_change_pcts(risk_day, equity)
    drawdown_pct = update_equity_high_water(policy, equity, observed_at=snapshot.captured_at)
    capital_context = {
        "equity": str(equity),
        "daily_loss_pct": str(daily_loss_pct),
        "daily_profit_pct": str(daily_profit_pct),
        "drawdown_pct": str(drawdown_pct),
    }
    if policy.max_daily_loss_pct > 0 and daily_loss_pct >= policy.max_daily_loss_pct:
        reject("ACCOUNT_DAILY_LOSS", "Maximum daily loss reached", account_limit=str(policy.max_daily_loss_pct), **capital_context)
    if policy.max_account_drawdown_pct > 0 and drawdown_pct >= policy.max_account_drawdown_pct:
        reject("ACCOUNT_MAX_DRAWDOWN", "Maximum account drawdown reached", account_limit=str(policy.max_account_drawdown_pct), **capital_context)
    if policy.stop_after_daily_profit_pct > 0 and daily_profit_pct >= policy.stop_after_daily_profit_pct:
        reject("ACCOUNT_PROFIT_LOCK", "Daily profit lock reached; new entries are disabled for today", account_limit=str(policy.stop_after_daily_profit_pct), **capital_context)

    window = risk_day_window(account, snapshot.captured_at)
    entries_today = (
        Order.objects.filter(
            bot=bot,
            intent="entry",
            status__in=["ack", "part_filled", "filled"],
            submitted_at__gte=window.start,
            submitted_at__lt=window.end,
        )
        .exclude(pk=locked_order.pk)
        .count()
    )
    if bot.max_trades_per_day > 0 and entries_today >= bot.max_trades_per_day:
        reject("BOT_DAILY_TRADE_LIMIT", "Maximum bot trades per day reached", current_trades=entries_today, bot_limit=bot.max_trades_per_day)
    if bot.trade_interval_minutes > 0:
        last_entry = (
            Order.objects.filter(
                bot=bot,
                intent="entry",
                status__in=["ack", "part_filled", "filled"],
                submitted_at__isnull=False,
            )
            .exclude(pk=locked_order.pk)
            .order_by("-submitted_at")
            .first()
        )
        if last_entry and last_entry.submitted_at > now - timedelta(minutes=bot.trade_interval_minutes):
            reject(
                "BOT_MIN_TRADE_INTERVAL",
                "Minimum bot trade interval has not elapsed",
                last_trade_at=last_entry.submitted_at.isoformat(),
                bot_limit_minutes=bot.trade_interval_minutes,
            )

    # Only positions durably attributed to this platform count as managed
    # exposure. Manual and external trades remain visible but never become
    # bot-owned merely because their symbol matches.
    owned_positions = BrokerPosition.objects.filter(
        broker_account=account,
        ownership="ez_trade",
        status="open",
    )
    reservation_cutoff = now - timedelta(minutes=5)
    reservations = Order.objects.filter(
        broker_account=account,
        intent="entry",
        # `ack` is assigned immediately before order_send. Keep counting that
        # reservation until a fill is synchronized or the short lease expires,
        # otherwise another worker can slip through the account cap while the
        # first broker submission is in flight.
        status__in=["new", "ack"],
        risk_reserved_at__gte=reservation_cutoff,
    ).exclude(pk=locked_order.pk)
    account_positions = owned_positions.count() + reservations.count()
    symbol_positions = owned_positions.filter(symbol=locked_order.symbol).count() + reservations.filter(symbol=locked_order.symbol).count()
    bot_positions = owned_positions.filter(bot=bot).count() + reservations.filter(bot=bot).count()
    count_context = {
        "bot_position_count": bot_positions,
        "account_position_count": account_positions,
        "symbol_position_count": symbol_positions,
    }
    if bot.risk_max_concurrent_positions > 0 and bot_positions >= bot.risk_max_concurrent_positions:
        reject("BOT_MAX_POSITIONS", "Maximum bot positions reached", bot_limit=bot.risk_max_concurrent_positions, **count_context)
    if policy.max_total_open_positions > 0 and account_positions >= policy.max_total_open_positions:
        reject("ACCOUNT_MAX_POSITIONS", "Maximum total open positions reached", account_limit=policy.max_total_open_positions, **count_context)
    if policy.max_positions_per_symbol > 0 and symbol_positions >= policy.max_positions_per_symbol:
        reject("ACCOUNT_SYMBOL_POSITION_LIMIT", "Maximum aggregate positions for symbol reached", account_limit=policy.max_positions_per_symbol, **count_context)

    bid = _decimal(getattr(tick, "bid", 0))
    ask = _decimal(getattr(tick, "ask", 0))
    point = _decimal(getattr(symbol_info, "point", 0))
    volume_min = _decimal(getattr(symbol_info, "volume_min", 0))
    volume_max = _decimal(getattr(symbol_info, "volume_max", 0))
    volume_step = _decimal(getattr(symbol_info, "volume_step", 0))
    runtime_max_lot = get_runtime_config().max_order_lot
    if bid <= 0 or ask <= 0 or ask < bid or point <= 0:
        reject("BROKER_PRICE_UNAVAILABLE", "Fresh broker bid/ask and point size are required")
    if volume_min <= 0 or volume_max <= 0 or volume_step <= 0:
        reject("BROKER_VOLUME_SPEC_UNAVAILABLE", "Reliable broker volume minimum, maximum and step are required")
    if runtime_max_lot > 0 and volume_min > runtime_max_lot:
        reject("BROKER_MIN_VOLUME_EXCEEDS_MAX_ORDER_LOT", "broker_min_volume_exceeds_max_order_lot")

    spread_points = (ask - bid) / point
    market_price = (ask + bid) / Decimal("2")
    digits = getattr(symbol_info, "digits", None)
    profile_spread = _scalper_symbol_limit_points(
        locked_order,
        point,
        "max_spread_points",
        "max_spread_unit",
        market_price=market_price,
        digits=digits,
    ) or Decimal("0")
    spread_limit_points = _positive_min(_decimal(bot.max_spread_points), profile_spread)
    if spread_limit_points > 0 and spread_points > spread_limit_points:
        reject("BOT_MAX_SPREAD", "Spread exceeds configured bot limit", current_spread=str(spread_points), spread_limit=str(spread_limit_points))
    profile_deviation = _scalper_symbol_limit_points(
        locked_order,
        point,
        "max_slippage_points",
        "max_slippage_unit",
        market_price=market_price,
        digits=digits,
    ) or Decimal("0")
    bot_deviation = _decimal(bot.allowed_deviation_points)
    deviation_limit = _positive_min(bot_deviation, profile_deviation)
    deviation_points = int(deviation_limit)

    entry = ask if locked_order.side == "buy" else bid
    if locked_order.sl is None:
        reject("RISK_STOP_LOSS_REQUIRED", "Stop loss is required")
    stop = _decimal(locked_order.sl)
    take_profit = _decimal(locked_order.tp) if locked_order.tp is not None else None
    if locked_order.side == "buy" and not (stop < entry and (take_profit is None or take_profit > entry)):
        reject("INVALID_PROTECTION", "BUY protection is on the wrong side of the market")
    if locked_order.side == "sell" and not (stop > entry and (take_profit is None or take_profit < entry)):
        reject("INVALID_PROTECTION", "SELL protection is on the wrong side of the market")
    stops_level = _decimal(getattr(symbol_info, "trade_stops_level", None) or getattr(symbol_info, "stops_level", 0))
    if stops_level * point > 0 and abs(entry - stop) < stops_level * point:
        reject("BROKER_STOP_DISTANCE", "Stop loss violates the broker stop-distance rule")

    account_order_cap = _decimal(policy.max_order_lot_size)
    bot_order_cap = _decimal(bot.max_bot_lot_size)
    effective_cap = _positive_min(account_order_cap, bot_order_cap, runtime_max_lot, volume_max)
    loss_per_lot = Decimal("0")
    risk_amount = Decimal("0")
    bot_risk_pct = _decimal(bot.risk_per_trade_pct)
    effective_risk_pct = bot_risk_pct
    adaptive_risk_applied = False
    decision_params = (locked_order.decision.params or {}) if locked_order.decision_id else {}
    adaptive_risk = decision_params.get("risk_pct")
    if adaptive_risk is not None:
        adaptive_risk_pct = _decimal(adaptive_risk)
        if not adaptive_risk_pct.is_finite() or adaptive_risk_pct <= 0:
            reject(
                "ADAPTIVE_RISK_INVALID",
                "Decision risk percentage must be a positive finite value",
                decision_risk_pct=str(adaptive_risk),
            )
        effective_risk_pct = min(bot_risk_pct, adaptive_risk_pct)
        adaptive_risk_applied = effective_risk_pct < bot_risk_pct

    if bot.position_sizing_mode == "risk":
        tick_size = _decimal(getattr(symbol_info, "trade_tick_size", 0))
        tick_value = max(
            _decimal(getattr(symbol_info, "trade_tick_value_loss", 0)),
            _decimal(getattr(symbol_info, "trade_tick_value", 0)),
        )
        contract_size = _decimal(getattr(symbol_info, "trade_contract_size", 0))
        if tick_size <= 0 or tick_value <= 0 or contract_size <= 0:
            reject(
                "RISK_SYMBOL_SPEC_UNAVAILABLE",
                "Risk-based sizing requires reliable tick size, tick value and contract size",
                tick_size=str(tick_size), tick_value=str(tick_value), contract_size=str(contract_size),
            )
        loss_per_lot = abs(connector.calc_profit_for_account(account, locked_order.side, locked_order.symbol, 1, entry, stop))
        if loss_per_lot <= 0:
            reject("RISK_CALCULATION_UNAVAILABLE", "Broker monetary loss calculation failed")
        # A strategy may reduce risk in response to current conditions, but
        # may never use this channel to exceed the bot's configured ceiling.
        risk_amount = equity * effective_risk_pct / Decimal("100")
        if risk_amount <= 0:
            reject("RISK_AMOUNT_INVALID", "Risk amount is not positive")
        volume = risk_amount / loss_per_lot
        if effective_cap > 0:
            volume = min(volume, effective_cap)
        volume = _floor_to_step(volume, volume_step)
    else:
        requested = _decimal(locked_order.qty)
        if bot_order_cap > 0 and requested > bot_order_cap:
            reject("BOT_MAX_LOT", "Requested fixed lot exceeds the bot maximum", requested_volume=str(requested), bot_limit=str(bot_order_cap))
        if account_order_cap > 0 and requested > account_order_cap:
            reject("ACCOUNT_MAX_ORDER_LOT", "Requested fixed lot exceeds the account maximum order lot", requested_volume=str(requested), account_limit=str(account_order_cap))
        if runtime_max_lot > 0 and requested > runtime_max_lot:
            reject("PLATFORM_MAX_ORDER_LOT", "Requested fixed lot exceeds the platform maximum", requested_volume=str(requested), platform_limit=str(runtime_max_lot))
        if requested > volume_max:
            reject("BROKER_MAX_VOLUME", "Requested fixed lot exceeds the broker maximum", requested_volume=str(requested), broker_limit=str(volume_max))
        adjusted_requested = requested
        if adaptive_risk_applied and bot_risk_pct > 0:
            adjusted_requested = requested * effective_risk_pct / bot_risk_pct
        volume = _floor_to_step(adjusted_requested, volume_step)
        if volume != requested and not adaptive_risk_applied:
            reject("BROKER_VOLUME_STEP", "Fixed lot size does not align with the broker volume step", requested_volume=str(requested), volume_step=str(volume_step))
        try:
            loss_per_lot = abs(connector.calc_profit_for_account(account, locked_order.side, locked_order.symbol, 1, entry, stop))
            risk_amount = loss_per_lot * volume
        except Exception:
            loss_per_lot = Decimal("0")
            risk_amount = Decimal("0")

    if volume <= 0 or volume < volume_min:
        reject("BROKER_MIN_VOLUME", "Effective volume is below the broker minimum", effective_volume=str(volume), broker_limit=str(volume_min))

    aggregate_lots = owned_positions.aggregate(total=Sum("volume"))["total"] or Decimal("0")
    reserved_lots = reservations.aggregate(total=Sum("qty"))["total"] or Decimal("0")
    current_aggregate = _decimal(aggregate_lots) + _decimal(reserved_lots)
    if policy.max_aggregate_open_lots > 0 and current_aggregate + volume > policy.max_aggregate_open_lots:
        reject(
            "ACCOUNT_MAX_AGGREGATE_LOTS",
            "Maximum aggregate open lots would be exceeded",
            aggregate_lots=str(current_aggregate), effective_volume=str(volume), account_limit=str(policy.max_aggregate_open_lots),
        )

    margin_required = connector.calc_margin_for_account(account, locked_order.side, locked_order.symbol, volume, entry)
    if margin_required <= 0 or margin_required > free_margin * Decimal("0.90"):
        reject("ACCOUNT_MARGIN", "Insufficient free margin with required safety buffer", margin_required=str(margin_required), free_margin=str(free_margin))

    digits = int(getattr(symbol_info, "digits", 0) or 0)
    quantum = Decimal("1").scaleb(-digits) if digits >= 0 else point
    locked_order.qty = volume
    locked_order.remaining_qty = volume
    locked_order.requested_price = entry.quantize(quantum)
    locked_order.sl = stop.quantize(quantum)
    locked_order.risk_reserved_at = now
    if take_profit is not None:
        locked_order.tp = take_profit.quantize(quantum)
    locked_order.save(update_fields=["qty", "remaining_qty", "requested_price", "sl", "tp", "risk_reserved_at"])
    order.qty = locked_order.qty
    order.remaining_qty = locked_order.remaining_qty
    order.requested_price = locked_order.requested_price
    order.sl = locked_order.sl
    order.tp = locked_order.tp
    order.risk_reserved_at = locked_order.risk_reserved_at
    return PreTradeRiskResult(
        volume=volume,
        entry_price=entry,
        margin_required=margin_required,
        risk_amount=risk_amount,
        loss_per_lot=loss_per_lot,
        spread_points=spread_points,
        spread_limit_points=spread_limit_points,
        deviation_points=deviation_points,
        effective_risk_pct=effective_risk_pct,
    )
