from decimal import Decimal
from django.core.validators import MaxValueValidator, MinValueValidator
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("bots", "0039_bot_ai_trade_enabled"),
    ]

    operations = [
        migrations.RemoveField(
            model_name="bot",
            name="risk_max_positions_per_symbol",
        ),
        migrations.AddField(
            model_name="bot",
            name="allocation_amount",
            field=models.DecimalField(
                decimal_places=8,
                default=Decimal("0.0"),
                help_text="Virtual bankroll for this bot in account currency. When cumulative realized losses reach this allocation (or the configured loss %), trading pauses.",
                max_digits=20,
                validators=[MinValueValidator(Decimal("0"))],
            ),
        ),
        migrations.AddField(
            model_name="bot",
            name="allocation_loss_pct",
            field=models.DecimalField(
                decimal_places=2,
                default=Decimal("100.00"),
                help_text="Loss limit expressed as a percent of the allocation. Defaults to 100% (stop once the entire allocation is lost). Set to 0 to disable.",
                max_digits=5,
                validators=[MinValueValidator(Decimal("0")), MaxValueValidator(Decimal("1000"))],
            ),
        ),
        migrations.AddField(
            model_name="bot",
            name="allocation_profit_pct",
            field=models.DecimalField(
                decimal_places=2,
                default=Decimal("0.00"),
                help_text="Optional profit target as a percent of the allocation. Set to 0 to disable the cap.",
                max_digits=5,
                validators=[MinValueValidator(Decimal("0")), MaxValueValidator(Decimal("1000"))],
            ),
        ),
    ]
