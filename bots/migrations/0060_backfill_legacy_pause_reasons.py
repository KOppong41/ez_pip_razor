"""Classify legacy pauses on databases that already applied migration 0058."""

from django.db import migrations


def backfill_pause_reasons(apps, schema_editor):
    Bot = apps.get_model("bots", "Bot")
    paused = Bot.objects.using(schema_editor.connection.alias).filter(status="paused", pause_reason="")
    paused.filter(schedule_paused=True).update(pause_reason="schedule")
    paused.filter(schedule_paused=False, paused_until__isnull=False).update(pause_reason="loss_cooldown")
    paused.update(pause_reason="manual")


class Migration(migrations.Migration):
    dependencies = [("bots", "0059_category_quality_presets")]
    operations = [migrations.RunPython(backfill_pause_reasons, migrations.RunPython.noop)]
