from copy import deepcopy

from django.db import migrations, models


def freeze_applied_presets(apps, schema_editor):
    Bot = apps.get_model("bots", "Bot")
    database = schema_editor.connection.alias
    for bot in Bot.objects.using(database).filter(
        asset_preset_version_applied__isnull=False,
        asset__isnull=False,
    ).select_related("asset").iterator():
        # These fields previously read the current asset on every evaluation.
        # Freeze that effective layer; older unsaved asset versions cannot be
        # reconstructed. Preserve the detector tuning already frozen by 0049.
        preset = deepcopy(bot.asset.recommended_config or {})
        if preset:
            preset["strategy_overrides"] = deepcopy(bot.asset_strategy_overrides_applied or {})
        bot.asset_recommended_config_applied = preset
        bot.save(using=database, update_fields=["asset_recommended_config_applied"])


class Migration(migrations.Migration):
    dependencies = [("bots", "0049_bot_asset_strategy_overrides_applied")]

    operations = [
        migrations.AddField(
            model_name="bot",
            name="asset_recommended_config_applied",
            field=models.JSONField(
                blank=True,
                default=dict,
                help_text="Complete asset recommendation frozen when the preset was explicitly applied.",
            ),
        ),
        migrations.RunPython(freeze_applied_presets, migrations.RunPython.noop),
    ]
