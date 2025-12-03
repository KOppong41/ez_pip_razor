from collections import defaultdict
from decimal import Decimal
from execution.models import Order, Execution, Position
from execution.services.portfolio import record_fill


def reconcile_orders_and_positions(apply: bool = False) -> dict:
    """
    Ensure every filled order has an Execution and update positions via record_fill.
    Safe to run repeatedly; idempotent per order.
    """
    created_execs = 0
    skipped_missing_price = 0

    filled_orders = Order.objects.filter(status="filled")
    for order in filled_orders:
        if order.executions.exists():
            continue
        if order.price is None:
            skipped_missing_price += 1
            continue
        if apply:
            record_fill(order, order.qty, order.price)
            created_execs += 1

    # Build a simple snapshot of open positions after reconciliation
    positions_snapshot = list(
        Position.objects.filter(status="open").values("broker_account_id", "symbol", "qty", "avg_price")
    )

    return {
        "filled_orders": filled_orders.count(),
        "executions_created": created_execs,
        "skipped_missing_price": skipped_missing_price,
        "positions": positions_snapshot,
    }
