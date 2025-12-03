from django.db import migrations


def add_icmarket_mt5(apps, schema_editor):
    Broker = apps.get_model("brokers", "Broker")
    Broker.objects.update_or_create(
        code="icmarket_mt5",
        defaults={"name": "IC Markets / MT5", "is_active": True},
    )


def remove_icmarket_mt5(apps, schema_editor):
    Broker = apps.get_model("brokers", "Broker")
    Broker.objects.filter(code="icmarket_mt5").delete()


class Migration(migrations.Migration):

    dependencies = [
        ("brokers", "0010_default_brokers"),
    ]

    operations = [
        migrations.RunPython(add_icmarket_mt5, reverse_code=remove_icmarket_mt5),
    ]
