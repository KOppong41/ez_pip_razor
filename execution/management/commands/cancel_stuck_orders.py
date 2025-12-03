from datetime import timedelta
from django.core.management.base import BaseCommand
from django.utils import timezone

from execution.models import Order
from execution.services.orchestrator import update_order_status


class Command(BaseCommand):
    help = "Cancel 'new'/'ack' orders that have been hanging past a timeout."

    def add_arguments(self, parser):
        parser.add_argument(
            "--minutes",
            type=int,
            default=5,
            help="Timeout in minutes for new/ack orders (default 5).",
        )

    def handle(self, *args, **options):
        minutes = options["minutes"]
        cutoff = timezone.now() - timedelta(minutes=minutes)
        qs = Order.objects.filter(status__in=["new", "ack"], created_at__lt=cutoff)
        count = qs.count()
        for order in qs:
            update_order_status(order, "canceled", error_msg="Auto-canceled: stuck beyond timeout")
        self.stdout.write(self.style.SUCCESS(f"Canceled {count} stuck order(s)."))
