from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("bots", "0020_asset_limits"),
    ]

    operations = [
        migrations.AddField(
            model_name="asset",
            name="is_active",
            field=models.BooleanField(default=True, help_text="If False, hide this asset from selection for new bots."),
        ),
    ]
