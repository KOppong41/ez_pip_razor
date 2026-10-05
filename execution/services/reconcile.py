from collections import defaultdict
from decimal import Decimal
from django.db import transaction
from execution.models import BrokerPosition, Order, Execution, Position
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
        if apply:
            with transaction.atomic():
                # Serialize the absence check with every other reconciliation
                # pass before recording a fill without a broker deal ticket.
                locked = Order.objects.select_for_update().get(pk=order.pk)
                if locked.status != "filled" or locked.executions.exists():
                    continue
                if locked.price is None:
                    skipped_missing_price += 1
                    continue
                record_fill(locked, locked.qty, locked.price)
                created_execs += 1
        elif not order.executions.exists() and order.price is None:
            skipped_missing_price += 1

    # Paper positions use the simulator ledger; live positions come only from
    # the broker-reconciled ticket model.
    positions_snapshot = list(
        Position.objects.filter(
            status="open",
            broker_account__connector="paper",
        ).values("broker_account_id", "symbol", "qty", "avg_price")
    )
    positions_snapshot.extend(
        {
            "broker_account_id": position.broker_account_id,
            "symbol": position.symbol,
            "qty": position.volume if position.side == "buy" else -position.volume,
            "avg_price": position.open_price,
            "broker_position_ticket": position.broker_position_ticket,
        }
        for position in BrokerPosition.objects.filter(status="open")
    )

    return {
        "filled_orders": filled_orders.count(),
        "executions_created": created_execs,
        "skipped_missing_price": skipped_missing_price,
        "positions": positions_snapshot,
    }
