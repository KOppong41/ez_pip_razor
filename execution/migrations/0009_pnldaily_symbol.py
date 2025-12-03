from django.db import migrations, models


def backfill_symbol(apps, schema_editor):
    PnLDaily = apps.get_model("execution", "PnLDaily")
    for row in PnLDaily.objects.all():
        if not row.symbol:
            row.symbol = "LEGACY"
            row.save(update_fields=["symbol"])


class Migration(migrations.Migration):

    dependencies = [
        ("execution", "0008_alter_order_status"),
    ]

    operations = [
        migrations.AddField(
            model_name="pnldaily",
            name="symbol",
            field=models.CharField(blank=True, default="", max_length=32),
        ),
        migrations.RunPython(backfill_symbol, migrations.RunPython.noop),
        migrations.AlterUniqueTogether(
            name="pnldaily",
            unique_together={("broker_account", "symbol", "date")},
        ),
    ]
