from django.db import migrations, models


DEFAULT_BROKER_CHOICES = [
    ("exness_mt5", "Exness/MT5"),
    ("binance", "Binance"),
    ("paper", "Paper"),
    ("fbs", "FBS"),
]


def seed_default_brokers(apps, schema_editor):
    Broker = apps.get_model("brokers", "Broker")
    for code, name in DEFAULT_BROKER_CHOICES:
        Broker.objects.get_or_create(code=code, defaults={"name": name, "is_active": True})


def remove_seeded_brokers(apps, schema_editor):
    Broker = apps.get_model("brokers", "Broker")
    Broker.objects.filter(code__in=[code for code, _ in DEFAULT_BROKER_CHOICES]).delete()


class Migration(migrations.Migration):

    dependencies = [
        ("brokers", "0007_alter_brokeraccount_broker"),
    ]

    operations = [
        migrations.CreateModel(
            name="Broker",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("code", models.CharField(max_length=32, unique=True)),
                ("name", models.CharField(max_length=128)),
                ("is_active", models.BooleanField(default=True)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
            ],
            options={
                "ordering": ["name", "code"],
            },
        ),
        migrations.AlterField(
            model_name="brokeraccount",
            name="broker",
            field=models.CharField(max_length=20),
        ),
        migrations.RunPython(seed_default_brokers, reverse_code=remove_seeded_brokers),
    ]
