from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [("execution", "0052_execution_latency_timestamps")]

    operations = [
        migrations.AddField(
            model_name="order",
            name="dispatch_publish_error",
            field=models.TextField(blank=True, default=""),
        ),
        migrations.AddField(
            model_name="order",
            name="dispatch_publish_failed_at",
            field=models.DateTimeField(blank=True, null=True),
        ),
    ]
