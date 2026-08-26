from django.db import migrations


def seed_exit_and_units(apps, schema_editor):
    ScalperProfile = apps.get_model("execution", "ScalperProfile")
    for profile in ScalperProfile.objects.all():
        cfg = profile.config or {}
        symbols = cfg.get("symbols") or {}
        changed = False
        for _, data in symbols.items():
            if not isinstance(data, dict):
                continue
            sl = data.get("sl_points", {})
            if isinstance(sl, dict) and "unit" not in sl:
                sl["unit"] = "points"
                data["sl_points"] = sl
                changed = True
            if "max_spread_unit" not in data and data.get("max_spread_points") is not None:
                data["max_spread_unit"] = "points"
                changed = True
            if "max_slippage_unit" not in data and data.get("max_slippage_points") is not None:
                data["max_slippage_unit"] = "points"
                changed = True
            # Default exit_mode to fixed_tp to preserve current behavior
            if "exit_mode" not in data:
                data["exit_mode"] = "fixed_tp"
                changed = True
        if changed:
            cfg["symbols"] = symbols
            profile.config = cfg
            profile.save(update_fields=["config", "updated_at"])


class Migration(migrations.Migration):
    dependencies = [
        ("execution", "0044_tradelog_closed_at_broker_tradelog_opened_at_broker"),
    ]

    operations = [
        migrations.RunPython(seed_exit_and_units, migrations.RunPython.noop),
    ]
