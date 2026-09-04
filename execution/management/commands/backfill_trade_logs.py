from datetime import timedelta
from django.core.management.base import BaseCommand
from django.utils import timezone

from execution.models import Execution, Order, TradeLog
from execution.services.timezones import to_broker_timezone


class Command(BaseCommand):
    help = "Backfill TradeLog entries from filled orders only."

    def add_arguments(self, parser):
        parser.add_argument(
            "--days",
            type=int,
            default=None,
            help="Limit to orders created within the last N days. Default: all orders.",
        )

    def handle(self, *args, **options):
        cutoff = None
        if options.get("days"):
            cutoff = timezone.now() - timedelta(days=options["days"])

        # TradeLog represents a completed trade, so opening orders are used
        # only as execution sources and must not become separate history rows.
        qs = Order.objects.filter(
            intent="exit",
            status__in=["filled", "part_filled"],
        )
        if cutoff:
            qs = qs.filter(created_at__gte=cutoff)

        already_logged = set(TradeLog.objects.values_list("order_id", flat=True))
        created = 0
        updated = 0
        for order in qs.iterator():
            entry_execution = None
            exit_execution = (
                Execution.objects.filter(order=order)
                .order_by("-exec_time", "-id")
                .first()
            )
            if exit_execution and exit_execution.broker_position_ticket:
                entry_execution = (
                    Execution.objects.filter(
                        order__broker_account=order.broker_account,
                        order__symbol=order.symbol,
                        order__intent="entry",
                        broker_position_ticket=exit_execution.broker_position_ticket,
                    )
                    .order_by("exec_time", "id")
                    .first()
                )
                if entry_execution is None:
                    entry_order = (
                        Order.objects.filter(
                            broker_account=order.broker_account,
                            symbol=order.symbol,
                            intent="entry",
                            broker_position_ticket=exit_execution.broker_position_ticket,
                        )
                        .order_by("created_at", "id")
                        .first()
                    )
                    if entry_order:
                        entry_execution = (
                            Execution.objects.filter(order=entry_order)
                            .order_by("exec_time", "id")
                            .first()
                        )

            values = {
                "price": entry_execution.price if entry_execution else order.price,
                "broker_ticket": (
                    getattr(entry_execution, "broker_position_ticket", None)
                    or getattr(exit_execution, "broker_position_ticket", None)
                    or order.broker_position_ticket
                    or order.broker_ticket
                ),
            }
            existing = TradeLog.objects.filter(order=order).order_by("created_at").first()
            if existing:
                changed = []
                for field, value in values.items():
                    if value is not None and getattr(existing, field) != value:
                        setattr(existing, field, value)
                        changed.append(field)
                if changed:
                    existing.save(update_fields=changed)
                    updated += 1
                continue
            TradeLog.objects.create(
                order=order,
                bot=order.bot,
                broker_account=order.broker_account,
                symbol=order.symbol,
                side=order.side,
                qty=order.qty,
                price=values["price"],
                status=order.status,
                pnl=None,
                broker_ticket=values["broker_ticket"],
                opened_at_broker=to_broker_timezone(getattr(order, "created_at", None), order.broker_account),
            )
            created += 1

        self.stdout.write(self.style.SUCCESS(f"Backfilled {created} trade log(s); repaired {updated} existing log(s)."))
