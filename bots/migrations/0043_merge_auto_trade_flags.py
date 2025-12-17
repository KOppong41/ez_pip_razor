from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("bots", "0042_merge_20251212_2237"),
    ]

    operations = [
        migrations.AlterField(
            model_name="bot",
            name="auto_trade",
            field=models.BooleanField(
                default=True,
                help_text=(
                    "If enabled, the bot dispatches live orders and uses its asset/profile strategy presets. "
                    "Disable to keep trades in manual/sandbox mode using the selected strategies."
                ),
            ),
        ),
        migrations.RemoveField(
            model_name="bot",
            name="ai_trade_enabled",
        ),
    ]
