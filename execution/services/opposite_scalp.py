"""An opposite scalp is one smaller, temporary overlay of an owned primary."""
from decimal import Decimal

from django.db.models import Q

from execution.models import BrokerPosition, Order


def overlay_params(position):
    order = getattr(position, "originating_order", None)
    decision = getattr(order, "decision", None) if order else None
    return (decision.params or {}) if decision else {}


def is_overlay(position):
    return bool(overlay_params(position).get("is_opposite_scalp"))


def initial_risk(position):
    order = getattr(position, "originating_order", None)
    stop = getattr(order, "sl", None)
    return abs(position.open_price - stop) if stop is not None else Decimal("0")


def existing_overlay(primary, *, exclude_order=None):
    orders = Order.objects.filter(
        broker_account=primary.broker_account, bot=primary.bot, intent="entry",
        decision__params__is_opposite_scalp=True,
        decision__params__primary_position_id=primary.pk,
    )
    if exclude_order is not None:
        orders = orders.exclude(pk=exclude_order)
    # A filled child consumes this primary's allowance for its whole lifetime.
    # Pending/ambiguous requests also reserve it before broker acknowledgement.
    return orders.filter(Q(filled_qty__gt=0) | Q(status__in=["new", "ack", "part_filled", "filled"])
                         | Q(attempts__status__in=["submitting", "ambiguous"])).exists()


def validate_primary(bot, side, primary, account_info, tick, raw_positions, *, exclude_order=None):
    if not bot.allow_opposite_scalp:
        return "opposite_scalp_disabled"
    # MT5 account trade_mode describes demo/live; margin_mode describes
    # netting/exchange/hedging. Only RETAIL_HEDGING (2) supports this overlay.
    if getattr(account_info, "margin_mode", None) != 2 or getattr(account_info, "hedge_allowed", True) is False:
        return "opposite_scalp_requires_hedging_account"
    if not primary or primary.bot_id != bot.id or primary.broker_account_id != bot.broker_account_id:
        return "opposite_scalp_primary_unavailable"
    if primary.status != "open" or primary.ownership != "ez_trade" or primary.side == side or is_overlay(primary):
        return "opposite_scalp_primary_unavailable"
    if Order.objects.filter(broker_account=primary.broker_account, intent="exit",
        broker_position_ticket=primary.broker_position_ticket, status__in=["new", "ack", "part_filled"]).exists():
        return "opposite_scalp_primary_closing"
    raw = next((p for p in raw_positions if int(getattr(p, "ticket", 0)) == primary.broker_position_ticket), None)
    if raw is None or Decimal(str(getattr(raw, "volume", 0))) <= 0:
        return "opposite_scalp_primary_unavailable"
    if existing_overlay(primary, exclude_order=exclude_order):
        return "opposite_scalp_already_used"
    sign = Decimal("1") if primary.side == "buy" else Decimal("-1")
    market = Decimal(str(getattr(tick, "bid" if primary.side == "buy" else "ask", 0) or 0))
    stop = Decimal(str(getattr(raw, "sl", 0) or 0))
    risk = initial_risk(primary)
    protected = stop > 0 and sign * (stop - primary.open_price) >= 0
    profitable = market > 0 and risk > 0 and sign * (market - primary.open_price) >= risk * Decimal("0.5")
    if not (protected or profitable):
        return "opposite_scalp_primary_unprotected"
    return None


def primary_for_overlay(bot, symbol, side):
    positions = BrokerPosition.objects.filter(bot=bot, broker_account=bot.broker_account,
        symbol=symbol, status="open", ownership="ez_trade").exclude(side=side).select_related("originating_order__decision")
    primaries = [position for position in positions if not is_overlay(position)]
    return primaries[0] if len(primaries) == 1 else None
