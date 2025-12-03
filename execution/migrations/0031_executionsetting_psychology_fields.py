from decimal import Decimal

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("execution", "0030_alter_tradingprofile_options"),
    ]

    operations = [
        migrations.AddField(
            model_name="executionsetting",
            name="max_loss_streak_before_pause",
            field=models.PositiveIntegerField(
                default=0,
                help_text="If >0, automatically pause a bot after this many consecutive losing trades (0 = disabled).",
            ),
        ),
        migrations.AddField(
            model_name="executionsetting",
            name="loss_streak_cooldown_min",
            field=models.PositiveIntegerField(
                default=0,
                help_text="Minutes to keep a bot paused after exceeding the loss streak (0 = no automatic resume).",
            ),
        ),
        migrations.AddField(
            model_name="executionsetting",
            name="drawdown_soft_limit_pct",
            field=models.DecimalField(
                max_digits=6,
                decimal_places=2,
                default=Decimal("0.00"),
                help_text="Optional soft daily drawdown limit as a percentage of starting balance; size will be reduced when breached (0 = disabled).",
            ),
        ),
        migrations.AddField(
            model_name="executionsetting",
            name="drawdown_hard_limit_pct",
            field=models.DecimalField(
                max_digits=6,
                decimal_places=2,
                default=Decimal("0.00"),
                help_text="Optional hard daily drawdown limit as a percentage of starting balance; size will be heavily reduced when breached (0 = disabled).",
            ),
        ),
        migrations.AddField(
            model_name="executionsetting",
            name="soft_size_multiplier",
            field=models.DecimalField(
                max_digits=8,
                decimal_places=4,
                default=Decimal("1.0000"),
                help_text="Position size multiplier once the soft drawdown limit is breached (e.g. 0.5 = half size).",
            ),
        ),
        migrations.AddField(
            model_name="executionsetting",
            name="hard_size_multiplier",
            field=models.DecimalField(
                max_digits=8,
                decimal_places=4,
                default=Decimal("1.0000"),
                help_text="Position size multiplier once the hard drawdown limit is breached (e.g. 0.25 = quarter size).",
            ),
        ),
    ]

