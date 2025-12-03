from django.db import migrations


def add_mt5(apps, schema_editor):
    Broker = apps.get_model("brokers", "Broker")
    Broker.objects.update_or_create(
        code="mt5",
        defaults={"name": "MetaTrader 5", "is_active": True},
    )


def remove_mt5(apps, schema_editor):
    Broker = apps.get_model("brokers", "Broker")
    Broker.objects.filter(code="mt5").delete()


class Migration(migrations.Migration):

    dependencies = [
        ("brokers", "0011_add_icmarket_mt5"),
    ]

    operations = [
        migrations.RunPython(add_mt5, reverse_code=remove_mt5),
    ]
