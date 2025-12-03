from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("bots", "0032_bot_trading_schedule_enabled"),
    ]

    operations = [
        migrations.AddField(
            model_name="bot",
            name="current_loss_streak",
            field=models.PositiveIntegerField(
                default=0,
                help_text="Automatically incremented when trades close at a loss; resets on wins.",
            ),
        ),
        migrations.AddField(
            model_name="bot",
            name="paused_until",
            field=models.DateTimeField(
                null=True,
                blank=True,
                help_text="If set, the bot will not auto-trade until this time is reached (used for automatic cool-down after loss streaks).",
            ),
        ),
    ]

