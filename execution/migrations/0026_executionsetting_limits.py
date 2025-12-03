from decimal import Decimal
from django.db import migrations, models


def seed_limits(apps, schema_editor):
    Setting = apps.get_model("execution", "ExecutionSetting")
    for s in Setting.objects.all():
        changed = False
        if getattr(s, "max_order_lot", None) is None:
            s.max_order_lot = Decimal("0.05")
            changed = True
        if getattr(s, "max_order_notional", None) is None:
            s.max_order_notional = Decimal("5000")
            changed = True
        if changed:
            s.save(update_fields=["max_order_lot", "max_order_notional"])


class Migration(migrations.Migration):

    dependencies = [
        ("execution", "0025_executionsetting"),
    ]

    operations = [
        migrations.AddField(
            model_name="executionsetting",
            name="max_order_lot",
            field=models.DecimalField(decimal_places=4, default=Decimal("0.05"), help_text="Maximum lot size per order; set 0 to disable.", max_digits=8),
        ),
        migrations.AddField(
            model_name="executionsetting",
            name="max_order_notional",
            field=models.DecimalField(decimal_places=2, default=Decimal("5000"), help_text="Maximum notional (account currency) per order; set 0 to disable.", max_digits=20),
        ),
        migrations.RunPython(seed_limits, migrations.RunPython.noop),
    ]
