from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("bots", "0029_bot_id_code"),
    ]

    operations = [
        migrations.AddField(
            model_name="asset",
            name="category",
            field=models.CharField(
                choices=[
                    ("forex", "Forex"),
                    ("crypto", "Crypto"),
                    ("indices", "Indices"),
                    ("commodities", "Commodities"),
                ],
                default="forex",
                max_length=32,
                help_text="Category used for filters and dashboard summaries.",
            ),
        ),
    ]
