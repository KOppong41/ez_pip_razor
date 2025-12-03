from decimal import Decimal
from datetime import time

from django.db import migrations, models


def create_default_profiles(apps, schema_editor):
    TradingProfile = apps.get_model("execution", "TradingProfile")
    profile_data = [
        {
            "slug": "very_safe",
            "name": "Very Safe (Beginner)",
            "description": "Low risk, few trades, tight drawdowns, long timeframes.",
            "risk_per_trade_pct": Decimal("0.5"),
            "max_trades_per_day": 2,
            "max_concurrent_positions": 1,
            "max_drawdown_pct": Decimal("3.0"),
            "decision_min_score": Decimal("0.65"),
            "signal_quality_threshold": Decimal("0.70"),
            "cooldown_seconds": 600,
            "allowed_days": ["mon", "tue", "wed", "thu", "fri"],
            "trading_start": time(8, 0),
            "trading_end": time(17, 0),
        },
        {
            "slug": "balanced",
            "name": "Balanced",
            "description": "Moderate risk, balanced frequency, default guardrails.",
            "risk_per_trade_pct": Decimal("1.0"),
            "max_trades_per_day": 6,
            "max_concurrent_positions": 2,
            "max_drawdown_pct": Decimal("5.0"),
            "decision_min_score": Decimal("0.6"),
            "signal_quality_threshold": Decimal("0.65"),
            "cooldown_seconds": 300,
            "allowed_days": ["mon", "tue", "wed", "thu", "fri"],
            "trading_start": time(6, 0),
            "trading_end": time(19, 0),
        },
        {
            "slug": "day_trader",
            "name": "Aggressive Day Trader",
            "description": "Higher frequency, larger risk, shorter cooldowns.",
            "risk_per_trade_pct": Decimal("2.0"),
            "max_trades_per_day": 20,
            "max_concurrent_positions": 4,
            "max_drawdown_pct": Decimal("8.0"),
            "decision_min_score": Decimal("0.55"),
            "signal_quality_threshold": Decimal("0.60"),
            "cooldown_seconds": 60,
            "allowed_days": ["mon", "tue", "wed", "thu", "fri"],
            "trading_start": time(6, 0),
            "trading_end": time(20, 0),
        },
        {
            "slug": "scalper",
            "name": "Scalper",
            "description": "Ultra-tight trades, very short windows, and high cadence.",
            "risk_per_trade_pct": Decimal("3.0"),
            "max_trades_per_day": 60,
            "max_concurrent_positions": 6,
            "max_drawdown_pct": Decimal("10.0"),
            "decision_min_score": Decimal("0.45"),
            "signal_quality_threshold": Decimal("0.55"),
            "cooldown_seconds": 15,
            "allowed_days": ["mon", "tue", "wed", "thu", "fri"],
            "trading_start": time(7, 0),
            "trading_end": time(16, 0),
        },
        {
            "slug": "long_term",
            "name": "Long-Term Investor",
            "description": "Rare trades, multi-day focus, wide drawdown cushion.",
            "risk_per_trade_pct": Decimal("0.75"),
            "max_trades_per_day": 1,
            "max_concurrent_positions": 2,
            "max_drawdown_pct": Decimal("7.0"),
            "decision_min_score": Decimal("0.7"),
            "signal_quality_threshold": Decimal("0.75"),
            "cooldown_seconds": 1800,
            "allowed_days": ["mon", "tue", "wed", "thu", "fri"],
            "trading_start": time(8, 0),
            "trading_end": time(20, 0),
        },
        {
            "slug": "custom",
            "name": "Custom / Advanced",
            "description": "No automatic guardrails—user sets every parameter.",
            "risk_per_trade_pct": Decimal("1.5"),
            "max_trades_per_day": 10,
            "max_concurrent_positions": 3,
            "max_drawdown_pct": Decimal("12.0"),
            "decision_min_score": Decimal("0.5"),
            "signal_quality_threshold": Decimal("0.5"),
            "cooldown_seconds": 120,
            "allowed_days": ["mon", "tue", "wed", "thu", "fri"],
            "trading_start": time(0, 0),
            "trading_end": time(23, 59),
        },
    ]

    for data in profile_data:
        TradingProfile.objects.update_or_create(slug=data["slug"], defaults=data)


class Migration(migrations.Migration):

    dependencies = [
        ("execution", "0028_executionsetting_bot_min_default_qty"),
    ]

    operations = [
        migrations.CreateModel(
            name="TradingProfile",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("slug", models.CharField(max_length=64, unique=True)),
                ("name", models.CharField(max_length=128)),
                ("description", models.TextField(blank=True, default="")),
                ("risk_per_trade_pct", models.DecimalField(decimal_places=2, default=Decimal("1.0"), max_digits=5)),
                ("max_trades_per_day", models.PositiveIntegerField(default=5)),
                ("max_concurrent_positions", models.PositiveIntegerField(default=3)),
                ("max_drawdown_pct", models.DecimalField(decimal_places=2, default=Decimal("5.0"), max_digits=5)),
                ("decision_min_score", models.DecimalField(decimal_places=4, default=Decimal("0.5"), max_digits=6)),
                ("signal_quality_threshold", models.DecimalField(decimal_places=4, default=Decimal("0.6"), max_digits=6)),
                ("cooldown_seconds", models.PositiveIntegerField(default=300)),
                ("allowed_days", models.JSONField(blank=True, default=list)),
                ("trading_start", models.TimeField(default=time(6, 0))),
                ("trading_end", models.TimeField(default=time(18, 0))),
                ("is_default", models.BooleanField(default=False)),
                ("updated_at", models.DateTimeField(auto_now=True)),
            ],
        ),
        migrations.RunPython(create_default_profiles),
    ]
