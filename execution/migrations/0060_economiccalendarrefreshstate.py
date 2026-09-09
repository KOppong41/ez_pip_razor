from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("execution", "0059_economiccalendarevent"),
    ]

    operations = [
        migrations.CreateModel(
            name="EconomicCalendarRefreshState",
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
                ("provider", models.CharField(max_length=32, unique=True)),
                ("last_attempt_at", models.DateTimeField(blank=True, null=True)),
                (
                    "last_success_at",
                    models.DateTimeField(blank=True, db_index=True, null=True),
                ),
                ("last_error", models.TextField(blank=True, default="")),
                ("event_count", models.PositiveIntegerField(default=0)),
            ],
        ),
    ]
