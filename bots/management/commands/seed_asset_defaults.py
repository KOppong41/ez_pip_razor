from decimal import Decimal
from django.core.management.base import BaseCommand

from bots.models import Asset


DEFAULTS = {
    "EURUSDm": {"max_spread": Decimal("0.0003"), "min_notional": Decimal("1")},
    "USDJPYm": {"max_spread": Decimal("0.03"), "min_notional": Decimal("1")},
    "XAUUSDm": {"max_spread": Decimal("0.50"), "min_notional": Decimal("5")},
    "BTCUSDm": {"max_spread": Decimal("10.0"), "min_notional": Decimal("10")},
}


class Command(BaseCommand):
    help = "Populate sensible default max_spread and min_notional for known assets."

    def handle(self, *args, **options):
        updated = 0
        for sym, vals in DEFAULTS.items():
            try:
                asset = Asset.objects.get(symbol=sym)
            except Asset.DoesNotExist:
                continue
            changed = False
            if vals.get("max_spread") is not None:
                asset.max_spread = vals["max_spread"]
                changed = True
            if vals.get("min_notional") is not None:
                asset.min_notional = vals["min_notional"]
                changed = True
            if changed:
                asset.save(update_fields=["max_spread", "min_notional"])
                updated += 1
        self.stdout.write(self.style.SUCCESS(f"Updated {updated} asset(s) with defaults."))
