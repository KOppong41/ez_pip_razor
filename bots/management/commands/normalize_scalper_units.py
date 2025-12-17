from django.core.management.base import BaseCommand
from execution.models import ScalperProfile


class Command(BaseCommand):
    help = "Backfill unit metadata (sl_points unit, spread/slippage units) for scalper profiles and report results."

    def handle(self, *args, **options):
        fallback_unit = "points"
        updated = 0
        skipped = 0
        for profile in ScalperProfile.objects.all():
            cfg = profile.config or {}
            symbols = cfg.get("symbols") or {}
            changed = False
            for key, data in symbols.items():
                if not isinstance(data, dict):
                    continue
                sl = data.get("sl_points", {})
                if isinstance(sl, dict) and "unit" not in sl:
                    sl["unit"] = fallback_unit
                    data["sl_points"] = sl
                    changed = True
                if "max_spread_unit" not in data and data.get("max_spread_points") is not None:
                    data["max_spread_unit"] = fallback_unit
                    changed = True
                if "max_slippage_unit" not in data and data.get("max_slippage_points") is not None:
                    data["max_slippage_unit"] = fallback_unit
                    changed = True
                symbols[key] = data
            if changed:
                cfg["symbols"] = symbols
                cfg["unit_version"] = "legacy_points"
                profile.config = cfg
                profile.save(update_fields=["config", "updated_at"])
                updated += 1
            else:
                skipped += 1

        self.stdout.write(self.style.SUCCESS(f"Updated {updated} profile(s); {skipped} already had units."))
