from copy import deepcopy

from django.db import migrations


def update_gold_volume_recommendation(apps, schema_editor):
    Asset = apps.get_model("bots", "Asset")
    database = schema_editor.connection.alias
    for asset in Asset.objects.using(database).filter(symbol="XAUUSDm", recommended_config_version__lt=4):
        preset = deepcopy(asset.recommended_config or {})
        overrides = preset.setdefault("strategy_overrides", {})
        for strategy in ("momentum_ignition", "breakout_retest"):
            tuning = overrides.setdefault(strategy, {})
            tuning.setdefault("min_relative_volume", 1.0)
            tuning.setdefault("volume_lookback", 20)
        asset.recommended_config = preset
        asset.recommended_config_version = 4
        asset.save(using=database, update_fields=["recommended_config", "recommended_config_version"])
    # No other asset recommendation or frozen bot setting changes.
    Asset.objects.using(database).filter(recommended_config_version=3).update(recommended_config_version=4)


class Migration(migrations.Migration):
    dependencies = [("bots", "0052_gold_reference_preset")]
    operations = [migrations.RunPython(update_gold_volume_recommendation, migrations.RunPython.noop)]
