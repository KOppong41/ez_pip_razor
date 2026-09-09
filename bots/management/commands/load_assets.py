from decimal import Decimal

from django.core.management.base import BaseCommand

from bots.models import Asset
from core.asset_trading_presets import (
    ASSET_CATALOG,
    ASSET_PRESET_VERSION,
    ASSET_TRADING_PRESETS,
)


class Command(BaseCommand):
    help = "Idempotently ensure the canonical asset catalogue and recommendations."

    def handle(self, *args, **options):
        created = 0
        updated = 0
        for symbol, metadata in ASSET_CATALOG.items():
            values = {
                "display_name": metadata["display_name"],
                "category": metadata["category"],
                "min_qty": Decimal(metadata["min_qty"]),
                "recommended_qty": Decimal(metadata["recommended_qty"]),
                "recommended_config": ASSET_TRADING_PRESETS[symbol],
                "recommended_config_version": ASSET_PRESET_VERSION,
                "is_active": True,
            }
            _asset, was_created = Asset.objects.update_or_create(
                symbol=symbol,
                defaults=values,
            )
            created += int(was_created)
            updated += int(not was_created)
        self.stdout.write(
            self.style.SUCCESS(
                f"Assets and recommendations ensured. New: {created}; updated: {updated}"
            )
        )
