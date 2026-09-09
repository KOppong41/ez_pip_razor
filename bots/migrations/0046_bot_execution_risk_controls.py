from decimal import Decimal

from django.db import migrations, models
import django.core.validators


def migrate_account_controls_to_bots(apps, schema_editor):
    Bot = apps.get_model("bots", "Bot")
    RiskPolicy = apps.get_model("execution", "RiskPolicy")
    policies = {policy.broker_account_id: policy for policy in RiskPolicy.objects.all().iterator()}
    for bot in Bot.objects.exclude(broker_account_id=None).iterator():
        policy = policies.get(bot.broker_account_id)
        if policy is None:
            continue
        bot.position_sizing_mode = "risk"
        bot.risk_per_trade_pct = policy.risk_per_trade_pct
        bot.max_bot_lot_size = policy.max_order_lot_size
        bot.max_spread_points = policy.max_spread_points
        bot.allowed_deviation_points = policy.deviation_points
        bot.allow_live_account_execution = policy.live_trading_confirmed
        bot.close_positions_on_emergency_stop = policy.emergency_close_owned_positions
        # Existing bot limits are already explicit. Applying the lower old
        # account cap preserves or reduces risk without silently increasing it.
        if policy.max_entry_trades_per_day > 0:
            bot.max_trades_per_day = min(
                bot.max_trades_per_day,
                policy.max_entry_trades_per_day,
            )
        if policy.max_total_open_positions > 0:
            bot.risk_max_concurrent_positions = min(
                bot.risk_max_concurrent_positions,
                policy.max_total_open_positions,
            )
        bot.mt5_magic_number = 500_000_000 + int(bot.pk)
        bot.save(
            update_fields=[
                "position_sizing_mode",
                "risk_per_trade_pct",
                "max_bot_lot_size",
                "max_spread_points",
                "allowed_deviation_points",
                "allow_live_account_execution",
                "close_positions_on_emergency_stop",
                "max_trades_per_day",
                "risk_max_concurrent_positions",
                "mt5_magic_number",
            ]
        )


class Migration(migrations.Migration):
    dependencies = [
        ("bots", "0045_merge_20251217_0520"),
        ("execution", "0057_account_exposure_limits_and_risk_reservation"),
    ]

    operations = [
        migrations.AddField(
            model_name="bot",
            name="position_sizing_mode",
            field=models.CharField(
                choices=[("fixed", "Fixed lot"), ("risk", "Risk based")],
                default="risk",
                help_text="Use the fixed default lot or calculate volume from equity and stop-loss risk.",
                max_length=12,
            ),
        ),
        migrations.AddField(
            model_name="bot",
            name="risk_per_trade_pct",
            field=models.DecimalField(
                decimal_places=3,
                default=Decimal("0.5"),
                help_text="Percentage of current account equity risked when position sizing is risk based.",
                max_digits=6,
                validators=[
                    django.core.validators.MinValueValidator(Decimal("0")),
                    django.core.validators.MaxValueValidator(Decimal("100")),
                ],
            ),
        ),
        migrations.AddField(
            model_name="bot",
            name="max_bot_lot_size",
            field=models.DecimalField(
                decimal_places=8,
                default=Decimal("0.05"),
                help_text="Hard maximum volume for any entry created by this bot.",
                max_digits=12,
                validators=[django.core.validators.MinValueValidator(Decimal("0.00000001"))],
            ),
        ),
        migrations.AddField(
            model_name="bot",
            name="max_spread_points",
            field=models.DecimalField(
                decimal_places=4,
                default=Decimal("30"),
                help_text="Maximum entry spread in raw broker/MT5 points. Set to 0 to disable.",
                max_digits=12,
                validators=[django.core.validators.MinValueValidator(Decimal("0"))],
            ),
        ),
        migrations.AddField(
            model_name="bot",
            name="allowed_deviation_points",
            field=models.PositiveIntegerField(default=8, help_text="Maximum order deviation requested from MT5, in raw broker points."),
        ),
        migrations.AddField(
            model_name="bot",
            name="allow_live_account_execution",
            field=models.BooleanField(default=False, help_text="Explicit permission for this bot to submit entries to a live-money account."),
        ),
        migrations.AddField(
            model_name="bot",
            name="close_positions_on_emergency_stop",
            field=models.BooleanField(default=False, help_text="Close only this bot's owned positions when its account emergency stop is active."),
        ),
        migrations.AddField(
            model_name="bot",
            name="mt5_magic_number",
            field=models.BigIntegerField(blank=True, editable=False, help_text="Stable per-bot MT5 ownership identifier.", null=True, unique=True),
        ),
        migrations.AlterField(
            model_name="bot",
            name="risk_max_concurrent_positions",
            field=models.PositiveIntegerField(
                default=1,
                help_text="Maximum number of open positions this bot may hold across all symbols at the same time.",
            ),
        ),
        migrations.AlterField(
            model_name="bot",
            name="default_qty",
            field=models.DecimalField(
                decimal_places=8,
                default=Decimal("0.10"),
                help_text=(
                    "Order volume used only when Position sizing mode is Fixed lot. "
                    "It is ignored for risk-based sizing."
                ),
                max_digits=20,
            ),
        ),
        migrations.RunPython(migrate_account_controls_to_bots, migrations.RunPython.noop),
    ]
