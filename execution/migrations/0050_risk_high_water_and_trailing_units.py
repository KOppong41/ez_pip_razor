from decimal import Decimal

from django.db import migrations, models


def seed_equity_high_water(apps, schema_editor):
    RiskPolicy = apps.get_model("execution", "RiskPolicy")
    AccountSnapshot = apps.get_model("execution", "AccountSnapshot")
    for policy in RiskPolicy.objects.all().iterator():
        peak = (
            AccountSnapshot.objects.filter(broker_account_id=policy.broker_account_id)
            .order_by("-equity", "-captured_at")
            .first()
        )
        if peak is None or peak.equity is None or peak.equity <= 0:
            continue
        policy.equity_high_water = peak.equity
        policy.equity_high_water_at = peak.captured_at
        policy.save(update_fields=["equity_high_water", "equity_high_water_at"])


def normalize_default_trailing_values(apps, schema_editor):
    ExecutionSetting = apps.get_model("execution", "ExecutionSetting")
    for config in ExecutionSetting.objects.all().iterator():
        if config.trailing_trigger == Decimal("0.0005"):
            config.trailing_trigger = Decimal("50")
            config.trailing_trigger_unit = "points"
        else:
            config.trailing_trigger_unit = "price"
        if config.trailing_distance == Decimal("0.0003"):
            config.trailing_distance = Decimal("30")
            config.trailing_distance_unit = "points"
        else:
            config.trailing_distance_unit = "price"
        config.save(
            update_fields=[
                "trailing_trigger",
                "trailing_trigger_unit",
                "trailing_distance",
                "trailing_distance_unit",
            ]
        )


class Migration(migrations.Migration):
    dependencies = [("execution", "0049_order_decision")]

    operations = [
        migrations.AddField(
            model_name="riskpolicy",
            name="equity_high_water",
            field=models.DecimalField(
                decimal_places=8,
                default=Decimal("0"),
                help_text="Persistent highest observed broker equity used for account drawdown protection.",
                max_digits=20,
            ),
        ),
        migrations.AddField(
            model_name="riskpolicy",
            name="equity_high_water_at",
            field=models.DateTimeField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name="executionsetting",
            name="trailing_trigger_unit",
            field=models.CharField(
                choices=[
                    ("points", "Broker points"),
                    ("pips", "Pips"),
                    ("price", "Raw price"),
                    ("percent", "Percent of market price"),
                    ("atr", "ATR multiple"),
                ],
                default="points",
                max_length=16,
            ),
        ),
        migrations.AddField(
            model_name="executionsetting",
            name="trailing_distance_unit",
            field=models.CharField(
                choices=[
                    ("points", "Broker points"),
                    ("pips", "Pips"),
                    ("price", "Raw price"),
                    ("percent", "Percent of market price"),
                    ("atr", "ATR multiple"),
                ],
                default="points",
                max_length=16,
            ),
        ),
        migrations.AlterField(
            model_name="executionsetting",
            name="trailing_trigger",
            field=models.DecimalField(
                decimal_places=6,
                default=Decimal("50"),
                help_text="Profit trigger expressed in trailing_trigger_unit before trailing starts.",
                max_digits=12,
            ),
        ),
        migrations.AlterField(
            model_name="executionsetting",
            name="trailing_distance",
            field=models.DecimalField(
                decimal_places=6,
                default=Decimal("30"),
                help_text="Trail distance expressed in trailing_distance_unit.",
                max_digits=12,
            ),
        ),
        migrations.RunPython(seed_equity_high_water, migrations.RunPython.noop),
        migrations.RunPython(normalize_default_trailing_values, migrations.RunPython.noop),
    ]
