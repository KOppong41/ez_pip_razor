from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("brokers", "0008_broker_model"),
    ]

    operations = [
        migrations.AlterField(
            model_name="brokeraccount",
            name="mt5_path",
            field=models.CharField(
                blank=True,
                default=r"C:\Program Files\MetaTrader 5\terminal64.exe",
                max_length=512,
            ),
        ),
    ]
