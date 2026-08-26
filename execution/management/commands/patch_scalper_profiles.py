from django.core.management.base import BaseCommand
from execution.models import ScalperProfile


class Command(BaseCommand):
    help = "Patch scalper profiles with sane units and exit_mode defaults for XAUUSD, BTCUSD, ETHUSD."

    def handle(self, *args, **options):
        targets = {
            "XAUUSD": {
                "sl_points": {"min": 30, "max": 120, "unit": "points"},
                "max_spread_points": 25,
                "max_spread_unit": "points",
                "max_slippage_points": 10,
                "max_slippage_unit": "points",
                "exit_mode": "hybrid",
                "tp1_r": 1.2,
                "tp1_close_pct": 70,
                "trail_start_r": 0.9,
            },
            "BTCUSD": {
                "sl_points": {"min": 80, "max": 250, "unit": "price"},
                "max_spread_points": 60,
                "max_spread_unit": "price",
                "max_slippage_points": 30,
                "max_slippage_unit": "price",
                "exit_mode": "hybrid",
                "tp1_r": 1.2,
                "tp1_close_pct": 70,
                "trail_start_r": 0.9,
            },
            "ETHUSD": {
                "sl_points": {"min": 8, "max": 20, "unit": "price"},
                "max_spread_points": 5,
                "max_spread_unit": "price",
                "max_slippage_points": 3,
                "max_slippage_unit": "price",
                "exit_mode": "hybrid",
                "tp1_r": 1.2,
                "tp1_close_pct": 70,
                "trail_start_r": 0.8,
            },
        }

        updated = 0
        for profile in ScalperProfile.objects.all():
            cfg = profile.config or {}
            symbols = cfg.get("symbols") or {}
            changed = False
            for sym, patch in targets.items():
                entry = symbols.get(sym)
                if not entry:
                    continue
                entry.update(patch)
                symbols[sym] = entry
                changed = True
            if changed:
                cfg["symbols"] = symbols
                profile.config = cfg
                profile.save(update_fields=["config"])
                updated += 1
        self.stdout.write(self.style.SUCCESS(f"Patched {updated} scalper profile(s)."))
