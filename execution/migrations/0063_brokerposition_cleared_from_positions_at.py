from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("execution", "0062_riskpolicy_emergency_stop_triggered_at"),
    ]

    operations = [
        migrations.AddField(
            model_name="brokerposition",
            name="cleared_from_positions_at",
            field=models.DateTimeField(blank=True, null=True),
        ),
    ]
