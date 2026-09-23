from copy import deepcopy

from django.db import migrations


def update_btc_recommendation(apps, schema_editor):
    Asset = apps.get_model("bots", "Asset")
    database = schema_editor.connection.alias
    for asset in Asset.objects.using(database).filter(symbol="BTCUSDm", recommended_config_version__lt=5):
        preset = deepcopy(asset.recommended_config or {})
        overrides = preset.setdefault("strategy_overrides", {})
        for strategy in ("momentum_ignition", "breakout_retest"):
            tuning = overrides.setdefault(strategy, {})
            tuning.setdefault("min_relative_volume", 1.0)
            tuning.setdefault("volume_lookback", 20)
        for strategy in ("momentum_ignition", "trend_pullback"):
            overrides.setdefault(strategy, {}).setdefault("require_confirmation", True)
        overrides.setdefault("breakout_retest", {}).setdefault("require_retest_rejection", True)
        asset.recommended_config = preset
        asset.recommended_config_version = 5
        asset.save(using=database, update_fields=["recommended_config", "recommended_config_version"])
    # Other recommendations are unchanged in this catalog release. Existing
    # bot snapshots and risk settings are deliberately not rewritten.
    Asset.objects.using(database).filter(recommended_config_version=4).update(recommended_config_version=5)


class Migration(migrations.Migration):
    dependencies = [("bots", "0054_bot_schedule_paused")]
    operations = [migrations.RunPython(update_btc_recommendation, migrations.RunPython.noop)]
