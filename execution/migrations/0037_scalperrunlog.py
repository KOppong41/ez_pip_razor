from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        ("bots", "0017_asset"),
        ("execution", "0036_tighten_slippage_guardrail"),
    ]

    operations = [
        migrations.CreateModel(
            name="ScalperRunLog",
            fields=[
                (
                    "id",
                    models.BigAutoField(
                        auto_created=True,
                        primary_key=True,
                        serialize=False,
                        verbose_name="ID",
                    ),
                ),
                ("timeframe", models.CharField(default="1m", max_length=8)),
                ("session", models.CharField(blank=True, default="", max_length=32)),
                ("summary", models.JSONField(blank=True, default=dict)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                (
                    "bot",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="scalper_run_logs",
                        to="bots.bot",
                    ),
                ),
            ],
            options={
                "ordering": ["-created_at"],
            },
        ),
        migrations.AddIndex(
            model_name="scalperrunlog",
            index=models.Index(
                fields=["bot", "created_at"], name="execution_sc_bot_crea_7c01f6_idx"
            ),
        ),
        migrations.AddIndex(
            model_name="scalperrunlog",
            index=models.Index(
                fields=["session", "created_at"],
                name="execution_sc_session_c0c5c7_idx",
            ),
        ),
    ]
