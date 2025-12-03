from django.db import migrations, models
import decimal


class Migration(migrations.Migration):

    dependencies = [
        ("execution", "0009_pnldaily_symbol"),
    ]

    operations = [
        migrations.CreateModel(
            name="TradeLog",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("symbol", models.CharField(max_length=32)),
                ("side", models.CharField(choices=[("buy", "buy"), ("sell", "sell")], max_length=4)),
                ("qty", models.DecimalField(decimal_places=8, max_digits=20)),
                ("price", models.DecimalField(blank=True, decimal_places=8, max_digits=20, null=True)),
                ("status", models.CharField(default="new", max_length=32)),
                ("pnl", models.DecimalField(blank=True, decimal_places=8, max_digits=20, null=True)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("bot", models.ForeignKey(blank=True, null=True, on_delete=models.SET_NULL, to="bots.bot")),
                ("broker_account", models.ForeignKey(blank=True, null=True, on_delete=models.SET_NULL, to="brokers.brokeraccount")),
                ("order", models.ForeignKey(on_delete=models.CASCADE, related_name="trade_logs", to="execution.order")),
            ],
            options={
                "ordering": ["-created_at"],
            },
        ),
        migrations.AddIndex(
            model_name="tradelog",
            index=models.Index(fields=["symbol", "status", "created_at"], name="execution_t_symbol_673a49_idx"),
        ),
    ]
