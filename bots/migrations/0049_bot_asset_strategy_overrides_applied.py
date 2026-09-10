from copy import deepcopy

from django.db import migrations, models


def freeze_applied_strategy_overrides(apps, schema_editor):
    Bot = apps.get_model("bots", "Bot")
    queryset = Bot.objects.filter(
        asset_preset_version_applied__isnull=False,
        asset__isnull=False,
    ).select_related("asset")
    for bot in queryset.iterator():
        config = bot.asset.recommended_config or {}
        overrides = (
            config.get("strategy_overrides") or {}
            if isinstance(config, dict)
            else {}
        )
        bot.asset_strategy_overrides_applied = deepcopy(overrides)
        bot.save(update_fields=["asset_strategy_overrides_applied"])


class Migration(migrations.Migration):
    dependencies = [("bots", "0048_refresh_asset_strategy_overrides")]

    operations = [
        migrations.AddField(
            model_name="bot",
            name="asset_strategy_overrides_applied",
            field=models.JSONField(
                blank=True,
                default=dict,
                help_text=(
                    "Frozen detector tuning copied from the asset recommendation "
                    "when that preset version was applied."
                ),
            ),
        ),
        migrations.RunPython(
            freeze_applied_strategy_overrides,
            migrations.RunPython.noop,
        ),
    ]
