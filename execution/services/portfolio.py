
from decimal import Decimal
from django.db import transaction
from execution.models import Order, Execution, Position, TradeLog
from execution.services.psychology import update_bot_after_realized_pnl

@transaction.atomic
def record_fill(
    order: Order,
    qty: Decimal,
    price: Decimal,
    fee: Decimal = Decimal("0"),
    account_balance: Decimal | None = None,
) -> Execution:
    """
    Record a single fill for an order, update the running Position,
    and optionally store the account balance after this fill.
    """
    exe = Execution.objects.create(
        order=order,
        qty=qty,
        price=price,
        fee=fee,
        account_balance=account_balance,
        owner=getattr(order, "owner", None),
    )

    # --- existing position logic, keep as-is ---
    pos, _ = Position.objects.get_or_create(
        broker_account=order.broker_account,
        symbol=order.symbol,
        defaults={"qty": Decimal("0"), "avg_price": Decimal("0"), "owner": getattr(order, "owner", None)},
    )

    # Snapshot before applying this fill to detect closed/flip events
    old_qty = pos.qty
    old_avg = pos.avg_price

    # Update position qty/avg_price with side-aware math
    delta = qty if order.side == "buy" else -qty
    new_qty = pos.qty + delta

    # If adding in same direction, use weighted average; if crossing zero, reset avg_price to entry price
    if pos.qty == 0:
        pos.avg_price = price
    elif (pos.qty > 0 and new_qty > 0) or (pos.qty < 0 and new_qty < 0):
        total = abs(pos.qty) + abs(delta)
        pos.avg_price = (pos.avg_price * abs(pos.qty) + price * abs(delta)) / total
    else:
        # crossing/flattening then flipping; new entry price becomes avg
        pos.avg_price = price

    pos.qty = new_qty

    # Maintain status/cleanup when flat
    if pos.qty == 0:
        pos.avg_price = Decimal("0")
        pos.sl = None
        pos.tp = None
        pos.status = "closed"
    else:
        pos.status = "open"

    # Detect realized PnL when reducing or closing an existing position.
    realized_pnl = None
    if old_qty != 0:
        # Position direction before this fill
        if (old_qty > 0 and delta < 0) or (old_qty < 0 and delta > 0):
            # This fill is reducing/closing a position in the opposite direction.
            closing_qty = min(abs(old_qty), abs(delta))
            if closing_qty > 0:
                # For longs, profit when price increases; for shorts, opposite.
                direction = Decimal("1") if old_qty > 0 else Decimal("-1")
                realized_pnl = (Decimal(str(price)) - Decimal(str(old_avg))) * closing_qty * direction

    pos.save()

    # Attach realized PnL to the TradeLog for this order (if any).
    if realized_pnl is not None:
        try:
            tl = TradeLog.objects.filter(order=order).latest("created_at")
        except TradeLog.DoesNotExist:
            tl = None
        if tl:
            tl.pnl = realized_pnl
            # Classify outcome for easier analytics
            if realized_pnl > 0:
                tl.status = "win"
            elif realized_pnl < 0:
                tl.status = "loss"
            else:
                tl.status = "breakeven"
            tl.save(update_fields=["pnl", "status"])

            # Update bot-level psychology state (loss streak / pause) based on this realized result.
            try:
                update_bot_after_realized_pnl(order, realized_pnl)
            except Exception:
                # Fail-soft: PnL recording should never block portfolio updates.
                pass

    return exe
