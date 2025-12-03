from decimal import Decimal

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("bots", "0033_bot_psychology_fields"),
    ]

    operations = [
        migrations.AddField(
            model_name="bot",
            name="loss_streak_autopause_enabled",
            field=models.BooleanField(
                default=False,
                help_text=(
                    "If enabled, this bot will auto-pause after a configurable loss streak. "
                    "Global settings still act as an upper safety bound."
                ),
            ),
        ),
        migrations.AddField(
            model_name="bot",
            name="max_loss_streak_before_pause",
            field=models.PositiveIntegerField(
                default=0,
                help_text="If >0 and auto-pause is enabled, pause this bot after this many consecutive losing trades.",
            ),
        ),
        migrations.AddField(
            model_name="bot",
            name="loss_streak_cooldown_min",
            field=models.PositiveIntegerField(
                default=0,
                help_text="Minutes to keep this bot paused after a loss streak pause trigger (0 = stay paused until manually resumed).",
            ),
        ),
        migrations.AddField(
            model_name="bot",
            name="soft_drawdown_limit_pct",
            field=models.DecimalField(
                max_digits=6,
                decimal_places=2,
                default=Decimal("0.00"),
                help_text=(
                    "Optional per-bot soft daily drawdown limit as a percentage of starting balance. "
                    "When breached, position size is reduced according to the soft multiplier."
                ),
            ),
        ),
        migrations.AddField(
            model_name="bot",
            name="hard_drawdown_limit_pct",
            field=models.DecimalField(
                max_digits=6,
                decimal_places=2,
                default=Decimal("0.00"),
                help_text=(
                    "Optional per-bot hard daily drawdown limit as a percentage of starting balance. "
                    "When breached, position size is heavily reduced according to the hard multiplier."
                ),
            ),
        ),
        migrations.AddField(
            model_name="bot",
            name="soft_size_multiplier",
            field=models.DecimalField(
                max_digits=8,
                decimal_places=4,
                default=Decimal("1.0000"),
                help_text="Size multiplier once the soft drawdown limit is breached (e.g. 0.5 = half size).",
            ),
        ),
        migrations.AddField(
            model_name="bot",
            name="hard_size_multiplier",
            field=models.DecimalField(
                max_digits=8,
                decimal_places=4,
                default=Decimal("1.0000"),
                help_text="Size multiplier once the hard drawdown limit is breached (e.g. 0.25 = quarter size).",
            ),
        ),
    ]

