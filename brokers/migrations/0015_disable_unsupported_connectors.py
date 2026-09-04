from django.db import migrations, models


def disable_unsupported_connectors(apps, schema_editor):
    BrokerAccount = apps.get_model("brokers", "BrokerAccount")
    BrokerAccount.objects.filter(
        connector__in=["ctrader_api", "exness_web"]
    ).update(is_active=False, is_verified=False)


class Migration(migrations.Migration):
    dependencies = [("brokers", "0014_brokeraccount_timezone")]

    operations = [
        migrations.RunPython(disable_unsupported_connectors, migrations.RunPython.noop),
        migrations.AlterField(
            model_name="brokeraccount",
            name="connector",
            field=models.CharField(
                choices=[
                    ("mt5_local", "MetaTrader 5 Desktop (local terminal)"),
                    ("paper", "Paper Simulator"),
                ],
                default="mt5_local",
                help_text="Transport used to connect to this broker account.",
                max_length=32,
            ),
        ),
    ]
