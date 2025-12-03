from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("brokers", "0001_initial"),
    ]

    operations = [
        migrations.AddField(
            model_name="brokeraccount",
            name="mt5_login",
            field=models.CharField(blank=True, default="", max_length=64),
        ),
        migrations.AddField(
            model_name="brokeraccount",
            name="mt5_password_enc",
            field=models.TextField(blank=True, default=""),
        ),
        migrations.AddField(
            model_name="brokeraccount",
            name="mt5_path",
            field=models.CharField(blank=True, default="", max_length=512),
        ),
        migrations.AddField(
            model_name="brokeraccount",
            name="mt5_server",
            field=models.CharField(blank=True, default="", max_length=128),
        ),
    ]
