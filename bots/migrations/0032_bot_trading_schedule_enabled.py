from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("bots", "0031_merge_20251128_1539"),
    ]

    operations = [
        migrations.AddField(
            model_name="bot",
            name="trading_schedule_enabled",
            field=models.BooleanField(
                default=True,
                help_text=(
                    "If enabled, the bot only opens new trades during the configured days and time window. "
                    "If disabled, it may open trades at any time (24/7), subject to other risk checks."
                ),
            ),
        ),
    ]

