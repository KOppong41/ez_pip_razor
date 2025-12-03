from decimal import Decimal
from django.core.management.base import BaseCommand

from execution.models import Position


class Command(BaseCommand):
    help = "Close or remove flat positions (qty=0) by setting status=closed and avg_price=0."

    def handle(self, *args, **options):
        qs = Position.objects.filter(qty=0, status="open")
        count = qs.count()
        for pos in qs:
            pos.avg_price = Decimal("0")
            pos.sl = None
            pos.tp = None
            pos.status = "closed"
            pos.save(update_fields=["avg_price", "sl", "tp", "status"])
        self.stdout.write(self.style.SUCCESS(f"Processed {count} flat position(s)."))
