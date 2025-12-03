from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("execution", "0020_tradelog"),
    ]

    operations = [
        migrations.AlterField(
            model_name="order",
            name="status",
            field=models.CharField(choices=[("new", "new"), ("ack", "ack"), ("filled", "filled"), ("part_filled", "part_filled"), ("canceled", "canceled"), ("error", "error")], default="new", max_length=50),
        ),
        migrations.AddIndex(
            model_name="order",
            index=models.Index(fields=["status", "-created_at"], name="order_status_created_idx"),
        ),
        migrations.AddIndex(
            model_name="position",
            index=models.Index(fields=["status", "broker_account", "symbol"], name="position_status_broker_symbol_idx"),
        ),
        migrations.AddIndex(
            model_name="decision",
            index=models.Index(fields=["bot", "-decided_at"], name="decision_bot_decided_idx"),
        ),
        migrations.AddIndex(
            model_name="signal",
            index=models.Index(fields=["symbol", "-received_at"], name="signal_symbol_received_idx"),
        ),
    ]
