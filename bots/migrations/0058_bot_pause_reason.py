from django.db import migrations, models


def backfill_pause_reasons(apps, schema_editor):
    Bot = apps.get_model("bots", "Bot")
    paused = Bot.objects.using(schema_editor.connection.alias).filter(status="paused", pause_reason="")
    paused.filter(schedule_paused=True).update(pause_reason="schedule")
    # A legacy timer is not proof of loss ownership: manual pauses used to
    # leave paused_until intact. Keep uncertain rows operator-owned.
    paused.update(pause_reason="manual")


class Migration(migrations.Migration):
    dependencies = [("bots", "0057_alter_bot_allocation_amount_and_more")]

    operations = [
        migrations.AddField(
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
                ],
                help_text="Identifies which pause is eligible for automatic resume.",
            ),
        ),
        migrations.RunPython(backfill_pause_reasons, migrations.RunPython.noop),
    ]
