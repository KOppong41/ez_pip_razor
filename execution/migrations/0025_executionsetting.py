from decimal import Decimal
from django.conf import settings
from django.db import migrations, models


def seed_default(apps, schema_editor):
    Setting = apps.get_model("execution", "ExecutionSetting")
    defaults = {
        "key": "default",
        "decision_min_score": getattr(settings, "DECISION_MIN_SCORE", 0.5),
        "decision_flip_score": getattr(settings, "DECISION_FLIP_SCORE", 0.8),
        "decision_allow_hedging": getattr(settings, "DECISION_ALLOW_HEDGING", False),
        "decision_flip_cooldown_min": getattr(settings, "DECISION_FLIP_COOLDOWN_MIN", 15),
        "decision_max_flips_per_day": getattr(settings, "DECISION_MAX_FLIPS_PER_DAY", 3),
        "decision_order_cooldown_sec": getattr(settings, "DECISION_ORDER_COOLDOWN_SEC", 60),
        "decision_scalp_sl_offset": getattr(settings, "DECISION_SCALP_SL_OFFSET", Decimal("0.0003")),
        "decision_scalp_tp_offset": getattr(settings, "DECISION_SCALP_TP_OFFSET", Decimal("0.0005")),
        "decision_scalp_qty_multiplier": getattr(settings, "DECISION_SCALP_QTY_MULTIPLIER", Decimal("0.3")),
        "order_ack_timeout_seconds": getattr(settings, "ORDER_ACK_TIMEOUT_SECONDS", 180),
        "early_exit_max_unrealized_pct": getattr(settings, "EARLY_EXIT_MAX_UNREALIZED_PCT", Decimal("0.02")),
        "trailing_trigger": getattr(settings, "TRAILING_TRIGGER", Decimal("0.0005")),
        "trailing_distance": getattr(settings, "TRAILING_DISTANCE", Decimal("0.0003")),
        "paper_start_balance": getattr(settings, "PAPER_START_BALANCE", Decimal("100000")),
        "mt5_default_contract_size": getattr(settings, "MT5_DEFAULT_CONTRACT_SIZE", 100000),
    }
    Setting.objects.get_or_create(key="default", defaults=defaults)


class Migration(migrations.Migration):

    dependencies = [
        ("execution", "0024_owner_fields"),
    ]

    operations = [
        migrations.CreateModel(
            name="ExecutionSetting",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("key", models.CharField(default="default", max_length=32, unique=True)),
                ("decision_min_score", models.DecimalField(decimal_places=4, default=Decimal("0.5"), max_digits=6)),
                ("decision_flip_score", models.DecimalField(decimal_places=4, default=Decimal("0.8"), max_digits=6)),
                ("decision_allow_hedging", models.BooleanField(default=False)),
                ("decision_flip_cooldown_min", models.PositiveIntegerField(default=15)),
                ("decision_max_flips_per_day", models.PositiveIntegerField(default=3)),
                ("decision_order_cooldown_sec", models.PositiveIntegerField(default=60)),
                ("decision_scalp_sl_offset", models.DecimalField(decimal_places=6, default=Decimal("0.0003"), max_digits=12)),
                ("decision_scalp_tp_offset", models.DecimalField(decimal_places=6, default=Decimal("0.0005"), max_digits=12)),
                ("decision_scalp_qty_multiplier", models.DecimalField(decimal_places=4, default=Decimal("0.3"), max_digits=8)),
                ("order_ack_timeout_seconds", models.PositiveIntegerField(default=180)),
                ("early_exit_max_unrealized_pct", models.DecimalField(decimal_places=4, default=Decimal("0.02"), max_digits=6)),
                ("trailing_trigger", models.DecimalField(decimal_places=6, default=Decimal("0.0005"), max_digits=10)),
                ("trailing_distance", models.DecimalField(decimal_places=6, default=Decimal("0.0003"), max_digits=10)),
                ("paper_start_balance", models.DecimalField(decimal_places=2, default=Decimal("100000"), max_digits=20)),
                ("mt5_default_contract_size", models.PositiveIntegerField(default=100000)),
            ],
            options={
                "verbose_name": "Execution setting",
                "verbose_name_plural": "Execution settings",
            },
        ),
        migrations.RunPython(seed_default, migrations.RunPython.noop),
    ]
