from django.core.management.base import BaseCommand
from decimal import Decimal

from bots.models import Asset, Bot


DEFAULT_ASSETS = [
    # Majors
    ("EURUSDm", "EUR/USD", "0.10", "0.10"),
    ("GBPUSDm", "GBP/USD", "0.10", "0.10"),
    ("USDJPYm", "USD/JPY", "0.10", "0.10"),
    ("USDCHFm", "USD/CHF", "0.10", "0.10"),
    ("USDCADm", "USD/CAD", "0.10", "0.10"),
    ("AUDUSDm", "AUD/USD", "0.10", "0.10"),
    ("NZDUSDm", "NZD/USD", "0.10", "0.10"),
    # Crosses
    ("EURJPYm", "EUR/JPY", "0.10", "0.10"),
    ("EURGBPm", "EUR/GBP", "0.10", "0.10"),
    ("EURAUDm", "EUR/AUD", "0.10", "0.10"),
    ("EURNZDm", "EUR/NZD", "0.10", "0.10"),
    ("EURCADm", "EUR/CAD", "0.10", "0.10"),
    ("GBPJPYm", "GBP/JPY", "0.10", "0.10"),
    ("GBPCHFm", "GBP/CHF", "0.10", "0.10"),
    ("AUDJPYm", "AUD/JPY", "0.10", "0.10"),
    ("AUDCADm", "AUD/CAD", "0.10", "0.10"),
    ("AUDNZDm", "AUD/NZD", "0.10", "0.10"),
    ("NZDJPYm", "NZD/JPY", "0.10", "0.10"),
    ("NZDCADm", "NZD/CAD", "0.10", "0.10"),
    ("NZDCHFm", "NZD/CHF", "0.10", "0.10"),
    ("CADJPYm", "CAD/JPY", "0.10", "0.10"),
    ("CADCHFm", "CAD/CHF", "0.10", "0.10"),
    ("EURCHFm", "EUR/CHF", "0.10", "0.10"),
    # Metals
    ("XAUUSDm", "Gold", "0.01", "0.01"),
    ("XAGUSDm", "Silver", "0.01", "0.01"),
    # Energies
    ("USOILm", "WTI Oil", "0.10", "0.10"),
    ("UKOILm", "Brent Oil", "0.10", "0.10"),
    # Indices (synthetic; adjust per broker)
    ("US30m", "Dow Jones 30", "0.10", "0.10"),
    ("US500m", "S&P 500", "0.10", "0.10"),
    ("NAS100m", "Nasdaq 100", "0.10", "0.10"),
    ("GER40m", "DAX 40", "0.10", "0.10"),
    ("UK100m", "FTSE 100", "0.10", "0.10"),
    # Crypto examples
    ("BTCUSDm", "Bitcoin/USD", "0.01", "0.01"),
    ("ETHUSDm", "Ethereum/USD", "0.01", "0.01"),
]


class Command(BaseCommand):
    help = "Seed Asset records and optionally sync bot default_qty to the asset recommended_qty."

    def add_arguments(self, parser):
        parser.add_argument(
            "--sync-bots",
            action="store_true",
            help="Update bots' default_qty to asset.recommended_qty (if different).",
        )
        parser.add_argument(
            "--force",
            action="store_true",
            help="Force sync even if bot default_qty is higher than recommended.",
        )

    def handle(self, *args, **options):
        created = 0
        for sym, name, min_q, rec_q in DEFAULT_ASSETS:
            obj, was_created = Asset.objects.get_or_create(
                symbol=sym,
                defaults={
                    "display_name": name,
                    "min_qty": Decimal(min_q),
                    "recommended_qty": Decimal(rec_q),
                },
            )
            if was_created:
                created += 1
        self.stdout.write(self.style.SUCCESS(f"Assets seeded/ensured. New: {created}"))

        if options["sync_bots"]:
            updated = 0
            for bot in Bot.objects.select_related("asset"):
                if not bot.asset:
                    continue
                rec = bot.asset.recommended_qty
                if options["force"] or bot.default_qty != rec:
                    bot.default_qty = rec
                    bot.save(update_fields=["default_qty"])
                    updated += 1
            self.stdout.write(self.style.SUCCESS(f"Synced {updated} bot(s) to recommended qty."))
