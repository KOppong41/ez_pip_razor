from decimal import Decimal

from django.db import migrations, models
import django.core.validators


def seed_aggregate_limit(apps, schema_editor):
    RiskPolicy = apps.get_model("execution", "RiskPolicy")
    for policy in RiskPolicy.objects.all().iterator():
        per_order = Decimal(str(policy.max_order_lot_size or 0))
        positions = int(policy.max_total_open_positions or 0)
        policy.max_aggregate_open_lots = per_order * positions if positions > 0 else per_order
        policy.save(update_fields=["max_aggregate_open_lots"])


class Migration(migrations.Migration):
    dependencies = [("execution", "0056_historicalbacktest")]

    operations = [
        migrations.RenameField(
            model_name="riskpolicy",
            old_name="max_lot",
            new_name="max_order_lot_size",
        ),
        migrations.RenameField(
            model_name="riskpolicy",
            old_name="max_positions",
            new_name="max_total_open_positions",
        ),
        migrations.AddField(
            model_name="riskpolicy",
            name="max_aggregate_open_lots",
            field=models.DecimalField(
                decimal_places=8,
                default=Decimal("0.05"),
                help_text="Combined open volume across positions owned by bots on this account. 0 disables.",
                max_digits=20,
                validators=[django.core.validators.MinValueValidator(Decimal("0"))],
            ),
        ),
        migrations.AddField(
            model_name="order",
            name="risk_reserved_at",
            field=models.DateTimeField(
                blank=True,
                db_index=True,
                help_text="Short-lived account exposure reservation made by final pre-trade validation.",
                null=True,
            ),
        ),
        migrations.RunPython(seed_aggregate_limit, migrations.RunPython.noop),
    ]
