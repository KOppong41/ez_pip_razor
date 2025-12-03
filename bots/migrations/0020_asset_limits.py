from django.db import migrations, models
from decimal import Decimal


class Migration(migrations.Migration):

    dependencies = [
        ("bots", "0019_alter_bot_engine_mode_default"),
    ]

    operations = [
        migrations.AddField(
            model_name="asset",
            name="max_spread",
            field=models.DecimalField(decimal_places=8, default=Decimal("0"), help_text="Optional max spread allowed for this asset. 0 = no limit.", max_digits=20),
        ),
        migrations.AddField(
            model_name="asset",
            name="min_notional",
            field=models.DecimalField(decimal_places=8, default=Decimal("0"), help_text="Optional minimum notional (price*qty) for this asset. 0 = no limit.", max_digits=20),
        ),
    ]
