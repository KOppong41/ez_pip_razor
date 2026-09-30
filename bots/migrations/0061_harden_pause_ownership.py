"""Remove auto-resume ownership from historically ambiguous timed pauses."""

from django.db import migrations, models


def harden_pause_ownership(apps, schema_editor):
    Bot = apps.get_model("bots", "Bot")
    database = schema_editor.connection.alias
    paused = Bot.objects.using(database).filter(status="paused")
    paused.filter(pause_reason="", schedule_paused=True).update(pause_reason="schedule")
    paused.filter(pause_reason="").update(pause_reason="manual")

    # 0058/0060 inferred loss_cooldown from paused_until alone. That inference
    # cannot be distinguished from a manual pause with a leftover timer.
    # Only a timer written with explicit loss-pause provenance may auto-resume.
    for bot in paused.filter(pause_reason="loss_cooldown").iterator():
        marker = (bot.scalper_params or {}).get("_loss_pause_owner")
        proven = (
            isinstance(marker, dict)
            and bot.paused_until is not None
            and marker.get("until") == bot.paused_until.isoformat()
        )
        if not proven:
            Bot.objects.using(database).filter(pk=bot.pk).update(
                pause_reason="manual", schedule_paused=False, paused_until=None,
            )


class Migration(migrations.Migration):
    dependencies = [("bots", "0060_backfill_legacy_pause_reasons")]
    operations = [
        migrations.AlterField(
            model_name="bot",
            name="pause_reason",
            field=models.CharField(
                max_length=24,
                blank=True,
                default="",
                editable=False,
                choices=[
                    ("", "None"),
                    ("manual", "Manual"),
                    ("schedule", "Trading schedule"),
                    ("loss_cooldown", "Loss cooldown"),
                    ("loss_lock", "Loss limit pause"),
                ],
                help_text="Identifies which pause is eligible for automatic resume.",
            ),
        ),
        migrations.RunPython(harden_pause_ownership, migrations.RunPython.noop),
    ]
