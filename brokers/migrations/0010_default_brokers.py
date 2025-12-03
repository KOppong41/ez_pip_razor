from django.db import migrations

def create_default_brokers(apps, schema_editor):
    Broker = apps.get_model("brokers", "Broker")
    defaults = [
        {"code": "exness_mt5", "name": "Exness / MT5"},
        {"code": "binance", "name": "Binance API"},
        {"code": "paper", "name": "Paper Trading"},
        {"code": "fbs", "name": "FBS"},
    ]
    for entry in defaults:
        Broker.objects.update_or_create(code=entry["code"], defaults=entry)

class Migration(migrations.Migration):

    dependencies = [
        ("brokers", "0009_alter_brokeraccount_mt5_path"),
    ]

    operations = [
        migrations.RunPython(create_default_brokers),
    ]
