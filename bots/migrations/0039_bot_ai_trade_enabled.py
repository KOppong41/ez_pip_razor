from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("bots", "0038_fix_btc_min_qty"),
    ]

    operations = [
        migrations.AddField(
            model_name="bot",
            name="ai_trade_enabled",
            field=models.BooleanField(
                default=False,
                help_text="If enabled, ignore manual strategy selection and let the AI selector choose strategies per market conditions.",
            ),
        ),
    ]
