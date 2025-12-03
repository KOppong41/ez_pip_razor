from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("bots", "0018_bot_asset_fk"),
    ]

    operations = [
        migrations.AlterField(
            model_name="bot",
            name="engine_mode",
            field=models.CharField(
                choices=[("external", "External signals (TradingView/Telegram)"), ("harami", "Internal engine (candlestick/SMC)")],
                default="harami",
                help_text="How this bot receives trade ideas: 'external' = signals from TradingView/Telegram/webhooks, 'harami' = internal candlestick/SMC engine.",
                max_length=32,
            ),
        ),
    ]
