from datetime import timedelta
from decimal import Decimal
from django.core.management.base import BaseCommand
from django.utils import timezone

from execution.models import TradeLog


class Command(BaseCommand):
    help = "Summarize trade performance over a lookback window."

    def add_arguments(self, parser):
        parser.add_argument("--days", type=int, default=7, help="Lookback window in days (default 7).")

    def handle(self, *args, **options):
        days = options["days"]
        since = timezone.now() - timedelta(days=days)
        qs = TradeLog.objects.filter(created_at__gte=since)
        total = qs.count()
        filled = qs.filter(status="filled").count()
        errors = qs.filter(status="error").count()
        canceled = qs.filter(status="canceled").count()

        # Naive win-rate: percentage of filled trades (errors treated as failures)
        success_rate = (filled / total * 100) if total else 0

        self.stdout.write(f"Performance last {days} day(s):")
        self.stdout.write(f" - total trades logged: {total}")
        self.stdout.write(f" - filled: {filled}")
        self.stdout.write(f" - errors: {errors}")
        self.stdout.write(f" - canceled: {canceled}")
        self.stdout.write(f" - success rate (filled/total): {success_rate:.2f}%")

        # Breakdown by symbol
        self.stdout.write("\nBy symbol:")
        for sym in sorted(set(qs.values_list("symbol", flat=True))):
            sym_qs = qs.filter(symbol=sym)
            total_s = sym_qs.count()
            filled_s = sym_qs.filter(status="filled").count()
            errors_s = sym_qs.filter(status="error").count()
            success_s = (filled_s / total_s * 100) if total_s else 0
            self.stdout.write(f"  {sym}: total={total_s}, filled={filled_s}, errors={errors_s}, success%={success_s:.2f}")
