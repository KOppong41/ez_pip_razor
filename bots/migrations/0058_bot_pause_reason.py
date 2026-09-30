from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [("bots", "0057_alter_bot_allocation_amount_and_more")]

    operations = [
        migrations.AddField(
            model_name="bot",
            name="pause_reason",
            field=models.CharField(
                max_length=24,
                blank=True,
                default="",
                editable=False,
                choices=[
                    ("", "None"),
                    ("manual", "Manual"),
                    ("schedule", "Trading schedule"),
                    ("loss_cooldown", "Loss cooldown"),
                ],
                help_text="Identifies which pause is eligible for automatic resume.",
            ),
        ),
    ]
