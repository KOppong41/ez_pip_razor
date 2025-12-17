from django.db import migrations
from django.utils.timezone import now
from django.db.models import F
import json


def backfill_units(apps, schema_editor):
    ScalperProfile = apps.get_model("execution", "ScalperProfile")
    defaults = {
        "sl_points_unit": None,
        "max_spread_unit": None,
        "max_slippage_unit": None,
    }
    # Treat missing units as legacy "points" to preserve behavior.
    fallback_unit = "points"
    updated = 0
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


class Migration(migrations.Migration):

    dependencies = [
        ("bots", "0043_merge_auto_trade_flags"),
        ("execution", "0044_tradelog_closed_at_broker_tradelog_opened_at_broker"),
    ]

    operations = [
        migrations.RunPython(backfill_units, migrations.RunPython.noop),
    ]
