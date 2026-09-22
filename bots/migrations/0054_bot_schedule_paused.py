from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [("bots", "0053_gold_relative_volume")]

    operations = [
        migrations.AddField(
            model_name="bot",
            name="schedule_paused",
            field=models.BooleanField(
                default=False,
                editable=False,
                help_text="Paused by the trading schedule and eligible to resume in its next window.",
            ),
        ),
    ]
