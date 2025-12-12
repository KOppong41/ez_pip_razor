from decimal import Decimal
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("bots", "0040_auto_allocation_limits"),
    ]

    operations = [
        migrations.AddField(
            model_name="bot",
            name="allocation_start_pnl",
            field=models.DecimalField(
                decimal_places=8,
                default=Decimal("0.0"),
                help_text="Internal baseline for realized PnL when the allocation was last reset.",
                max_digits=20,
            ),
        ),
        migrations.AddField(
            model_name="bot",
            name="allocation_started_at",
            field=models.DateTimeField(
                blank=True,
                help_text="Timestamp when the current allocation baseline was set.",
                null=True,
            ),
        ),
    ]
