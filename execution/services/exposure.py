"""Durable entry reservations. Elapsed time cannot prove a broker outcome."""
from dataclasses import dataclass
from decimal import Decimal

from django.db.models import Q, Sum

from execution.models import BrokerPosition, Execution, Order

OUTSTANDING = ("new", "ack", "part_filled")
UNRESOLVED_ATTEMPTS = ("submitting", "ambiguous")


def has_submission_evidence(order):
    return bool(order.submitted_at or order.broker_order_ticket or order.filled_qty > 0
                or order.attempts.filter(status__in=("submitting", "ambiguous", "accepted", "partial", "reconciled")).exists())


@dataclass(frozen=True)
class EntryReservation:
    order_id: int
    bot_id: int
    symbol: str
    lots: Decimal
    position_slots: int


def entry_reservations(account, *, exclude_order_id=None):
    """Count unfilled and not-yet-synchronized fills without duplicating positions.

    Call inside the account row lock for authoritative admission decisions.
    A closed position or recorded exit accounts for previously filled volume;
    a missing position is unresolved and still consumes exposure.
    """
    known = list(BrokerPosition.objects.filter(broker_account=account, ownership="ez_trade"))
    candidates = Order.objects.filter(broker_account=account, intent="entry").filter(
        Q(status__in=OUTSTANDING) | Q(risk_reserved_at__isnull=False, filled_qty__gt=0)
        | Q(attempts__status__in=UNRESOLVED_ATTEMPTS, attempts__resolved_at__isnull=True)
    ).filter(
        Q(risk_reserved_at__isnull=False) | Q(status__in=("ack", "part_filled"))
        | Q(submitted_at__isnull=False) | Q(broker_order_ticket__isnull=False)
        | Q(attempts__status__in=UNRESOLVED_ATTEMPTS, attempts__resolved_at__isnull=True)
    ).exclude(pk=exclude_order_id).distinct()
    reservations = []
    for order in candidates:
        linked = [p for p in known if p.originating_order_id == order.pk or (
            order.broker_position_ticket and p.broker_position_ticket == order.broker_position_ticket)]
        # Replacing-position projections subtract their volume elsewhere, but
        # their already synchronized fills must not become reservations again.
        represented = sum((p.volume for p in linked if p.status in {"open", "missing"}), Decimal(0))
        tickets = {p.broker_position_ticket for p in linked}
        if order.broker_position_ticket:
            tickets.add(order.broker_position_ticket)
        exits = Execution.objects.filter(order__broker_account=account, order__intent="exit",
                                         broker_position_ticket__in=tickets).aggregate(total=Sum("qty"))["total"] or Decimal(0)
        filled = max(Decimal(0), order.filled_qty)
        if linked and all(p.status == "closed" for p in linked):
            exits = max(exits, filled)
        unsynchronized = max(Decimal(0), filled - exits - represented)
        remaining = max(Decimal(0), order.qty - filled, order.remaining_qty) if order.status in OUTSTANDING else Decimal(0)
        if order.attempts.filter(status__in=UNRESOLVED_ATTEMPTS, resolved_at__isnull=True).exists():
            remaining = max(remaining, order.qty - filled)
        lots = remaining + unsynchronized
        if lots > 0:
            reservations.append(EntryReservation(order.pk, order.bot_id, order.symbol, lots, int(represented <= 0)))
    return reservations
