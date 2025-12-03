from django.db import migrations, models
from decimal import Decimal


def seed_assets(apps, schema_editor):
    Asset = apps.get_model("bots", "Asset")
    seeds = [
        # Majors
        ("EURUSDm", "EUR/USD", "0.10", "0.10"),
        ("GBPUSDm", "GBP/USD", "0.10", "0.10"),
        ("USDJPYm", "USD/JPY", "0.10", "0.10"),
        ("USDCHFm", "USD/CHF", "0.10", "0.10"),
        ("USDCADm", "USD/CAD", "0.10", "0.10"),
        ("AUDUSDm", "AUD/USD", "0.10", "0.10"),
        ("NZDUSDm", "NZD/USD", "0.10", "0.10"),
        # Crosses
        ("EURJPYm", "EUR/JPY", "0.10", "0.10"),
        ("EURGBPm", "EUR/GBP", "0.10", "0.10"),
        ("EURAUDm", "EUR/AUD", "0.10", "0.10"),
        ("EURNZDm", "EUR/NZD", "0.10", "0.10"),
        ("GBPJPYm", "GBP/JPY", "0.10", "0.10"),
        ("AUDJPYm", "AUD/JPY", "0.10", "0.10"),
        ("NZDJPYm", "NZD/JPY", "0.10", "0.10"),
        # Metals
        ("XAUUSDm", "Gold", "0.01", "0.01"),
        ("XAGUSDm", "Silver", "0.01", "0.01"),
        # Indices (typical synthetic symbols; adjust per broker)
        ("US30m", "Dow Jones 30", "0.10", "0.10"),
        ("US500m", "S&P 500", "0.10", "0.10"),
        ("NAS100m", "Nasdaq 100", "0.10", "0.10"),
        ("GER40m", "DAX 40", "0.10", "0.10"),
        ("UK100m", "FTSE 100", "0.10", "0.10"),
        # Crypto (example)
        ("BTCUSDm", "Bitcoin/USD", "0.01", "0.01"),
        ("ETHUSDm", "Ethereum/USD", "0.01", "0.01"),
    ]

    for sym, name, min_q, rec_q in seeds:
        Asset.objects.get_or_create(
            symbol=sym,
            defaults={
                "display_name": name,
                "min_qty": Decimal(min_q),
                "recommended_qty": Decimal(rec_q),
            },
        )


class Migration(migrations.Migration):

    dependencies = [
        ("bots", "0016_alter_bot_kill_switch_max_unrealized_pct_and_more"),
    ]

    operations = [
        migrations.CreateModel(
            name="Asset",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("symbol", models.CharField(max_length=32, unique=True)),
                ("display_name", models.CharField(blank=True, default="", max_length=64)),
                ("min_qty", models.DecimalField(decimal_places=8, default=Decimal("0.01"), help_text="Broker minimum lot size for this symbol (e.g., 0.01 for XAUUSDm, 0.10 for EURUSDm).", max_digits=20)),
                ("recommended_qty", models.DecimalField(decimal_places=8, default=Decimal("0.10"), help_text="Suggested default lot size for bots using this asset.", max_digits=20)),
            ],
            options={
                "ordering": ["symbol"],
            },
        ),
        migrations.RunPython(
            code=seed_assets,
            reverse_code=migrations.RunPython.noop,
        ),
    ]
