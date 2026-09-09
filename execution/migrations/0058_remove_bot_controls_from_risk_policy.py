from decimal import Decimal

from django.db import migrations, models
import django.core.validators


class Migration(migrations.Migration):
    dependencies = [
        ("execution", "0057_account_exposure_limits_and_risk_reservation"),
        ("bots", "0046_bot_execution_risk_controls"),
    ]

    operations = [
        migrations.RemoveField(model_name="riskpolicy", name="risk_per_trade_pct"),
        migrations.RemoveField(model_name="riskpolicy", name="max_entry_trades_per_day"),
        migrations.RemoveField(model_name="riskpolicy", name="max_spread_points"),
        migrations.RemoveField(model_name="riskpolicy", name="deviation_points"),
        migrations.RemoveField(model_name="riskpolicy", name="live_trading_confirmed"),
        migrations.RemoveField(model_name="riskpolicy", name="emergency_close_owned_positions"),
        migrations.AlterField(
            model_name="riskpolicy",
            name="max_daily_loss_pct",
            field=models.DecimalField(decimal_places=3, default=Decimal("1.5"), max_digits=6, validators=[django.core.validators.MinValueValidator(Decimal("0"))]),
        ),
        migrations.AlterField(
            model_name="riskpolicy",
            name="max_account_drawdown_pct",
            field=models.DecimalField(decimal_places=3, default=Decimal("5.0"), max_digits=6, validators=[django.core.validators.MinValueValidator(Decimal("0"))]),
        ),
        migrations.AlterField(
            model_name="riskpolicy",
            name="max_order_lot_size",
            field=models.DecimalField(decimal_places=8, default=Decimal("0.05"), max_digits=12, validators=[django.core.validators.MinValueValidator(Decimal("0"))]),
        ),
        migrations.AlterField(
            model_name="riskpolicy",
            name="stop_after_daily_profit_pct",
            field=models.DecimalField(decimal_places=3, default=Decimal("0"), max_digits=6, validators=[django.core.validators.MinValueValidator(Decimal("0"))]),
        ),
    ]
