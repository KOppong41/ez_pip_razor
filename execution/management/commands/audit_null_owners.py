from django.core.management.base import BaseCommand
from execution.models import Signal, Decision, Order, Execution, Position, PnLDaily, TradeLog


class Command(BaseCommand):
    help = "Report execution-domain rows with null owner for remediation."

    def handle(self, *args, **options):
        models = [
            ("Signal", Signal),
            ("Decision", Decision),
            ("Order", Order),
            ("Execution", Execution),
            ("Position", Position),
            ("PnLDaily", PnLDaily),
            ("TradeLog", TradeLog),
        ]
        total = 0
        for name, model in models:
            count = model.objects.filter(owner__isnull=True).count()
            total += count
            self.stdout.write(f"{name}: {count} null owners")
        self.stdout.write(f"Total null-owner rows: {total}")
