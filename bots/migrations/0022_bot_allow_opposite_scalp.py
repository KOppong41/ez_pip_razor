from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("bots", "0021_asset_active"),
    ]

    operations = [
        migrations.AddField(
            model_name="bot",
            name="allow_opposite_scalp",
            field=models.BooleanField(
                default=False,
                help_text="Allow opening a small opposite-direction scalp while keeping the main position open.",
            ),
        ),
    ]
