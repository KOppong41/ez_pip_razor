from datetime import timedelta
from django.core.management.base import BaseCommand
from django.utils import timezone

from execution.models import Order, TradeLog


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

        qs = Order.objects.filter(status__in=["filled", "part_filled"])
        if cutoff:
            qs = qs.filter(created_at__gte=cutoff)

        already_logged = set(TradeLog.objects.values_list("order_id", flat=True))
        created = 0
        for order in qs.iterator():
            if order.id in already_logged:
                continue
            TradeLog.objects.create(
                order=order,
                bot=order.bot,
                broker_account=order.broker_account,
                symbol=order.symbol,
                side=order.side,
                qty=order.qty,
                price=order.price,
                status=order.status,
                pnl=None,
            )
            created += 1

        self.stdout.write(self.style.SUCCESS(f"Backfilled {created} trade log(s)."))
