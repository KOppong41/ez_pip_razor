import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("execution", "0055_backfill_execution_owners"),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.CreateModel(
            name="HistoricalBacktest",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("bot_name", models.CharField(max_length=255)),
                ("symbol", models.CharField(max_length=32)),
                ("status", models.CharField(default="running", max_length=16)),
                ("source_name", models.CharField(max_length=255)),
                ("source_csv", models.TextField()),
                ("config", models.JSONField(default=dict)),
                ("dataset", models.JSONField(default=dict)),
                ("result", models.JSONField(default=dict)),
                ("error", models.CharField(blank=True, default="", max_length=255)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("completed_at", models.DateTimeField(null=True)),
                ("bot", models.ForeignKey(null=True, on_delete=django.db.models.deletion.SET_NULL, to="bots.bot")),
                ("owner", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, to=settings.AUTH_USER_MODEL)),
            ],
            options={
                "ordering": ["-created_at", "-id"],
                "indexes": [models.Index(fields=["owner", "created_at"], name="backtest_owner_created_idx")],
            },
        ),
    ]
