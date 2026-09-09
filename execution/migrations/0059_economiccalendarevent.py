from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [("execution", "0058_remove_bot_controls_from_risk_policy")]

    operations = [
        migrations.CreateModel(
            name="EconomicCalendarEvent",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("provider", models.CharField(default="tradingeconomics", max_length=32)),
                ("external_id", models.CharField(max_length=128)),
                ("starts_at", models.DateTimeField(db_index=True)),
                ("country", models.CharField(blank=True, default="", max_length=64)),
                ("currency", models.CharField(blank=True, default="", max_length=8)),
                ("title", models.CharField(max_length=255)),
                ("category", models.CharField(blank=True, default="", max_length=128)),
                ("importance", models.PositiveSmallIntegerField(default=0)),
                ("raw_payload", models.JSONField(blank=True, default=dict)),
                ("fetched_at", models.DateTimeField(auto_now=True)),
            ],
            options={"ordering": ["starts_at", "external_id"]},
        ),
        migrations.AddConstraint(
            model_name="economiccalendarevent",
            constraint=models.UniqueConstraint(
                fields=("provider", "external_id"),
                name="execution_unique_economic_calendar_event",
            ),
        ),
        migrations.AddIndex(
            model_name="economiccalendarevent",
            index=models.Index(fields=["importance", "starts_at"], name="execution_e_importa_bb405b_idx"),
        ),
        migrations.AddIndex(
            model_name="economiccalendarevent",
            index=models.Index(fields=["country", "starts_at"], name="execution_e_country_6809f5_idx"),
        ),
        migrations.AddIndex(
            model_name="economiccalendarevent",
            index=models.Index(fields=["currency", "starts_at"], name="execution_e_currenc_739c76_idx"),
        ),
    ]
