from copy import deepcopy
from django.db import migrations


def update_gold_recommendation(apps, schema_editor):
    Asset = apps.get_model("bots", "Asset")
    for asset in Asset.objects.using(schema_editor.connection.alias).filter(symbol="XAUUSDm", recommended_config_version__lt=3):
        preset = deepcopy(asset.recommended_config or {})
        old_pool = {"price_action_pinbar", "breakout_retest", "momentum_ignition"}
        if set(preset.get("enabled_strategies") or []) == old_pool:
            preset["enabled_strategies"] = ["trend_pullback", "breakout_retest", "momentum_ignition", "price_action_pinbar", "doji_breakout"]
        symbol = preset.setdefault("symbol_config", {})
        symbol["final_target_source"] = "strategy"
        schedule = preset.get("trading_schedule") or {}
        if schedule.get("timezone") == "America/New_York" and schedule.get("start") == "07:30" and schedule.get("end") == "14:00":
            days = ["mon", "tue", "wed", "thu", "fri"]
            schedule.setdefault("windows", [
                {"label": "London", "timezone": "Europe/London", "allowed_days": days, "start": "08:00", "end": "11:00"},
                {"label": "New York metals", "timezone": "America/New_York", "allowed_days": days, "start": "07:30", "end": "14:00"},
            ])
        asset.recommended_config = preset
        asset.recommended_config_version = 3
        asset.save(using=schema_editor.connection.alias, update_fields=["recommended_config", "recommended_config_version"])
    # Other version-2 recommendations are unchanged in this catalog release.
    Asset.objects.using(schema_editor.connection.alias).filter(recommended_config_version=2).update(recommended_config_version=3)


class Migration(migrations.Migration):
    dependencies = [("bots", "0051_bot_trading_windows")]
    operations = [migrations.RunPython(update_gold_recommendation, migrations.RunPython.noop)]
