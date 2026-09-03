from decimal import Decimal
from types import SimpleNamespace

from django.test import TestCase, override_settings

from bots.models import Asset, Bot
from brokers.models import BrokerAccount
from execution.models import Order, RiskPolicy
from execution.services.equity import update_equity_high_water
from execution.services.live_risk import RiskRejected, enforce_pretrade_risk


class LiveRiskSymbolLimitsTest(TestCase):
    def setUp(self):
        self.account = BrokerAccount.objects.create(
            name="MT5 demo",
            broker="mt5",
            connector="mt5_local",
            account_ref="live-risk-test",
            is_verified=True,
        )
        self.asset, _ = Asset.objects.get_or_create(
            symbol="XAUUSDm",
            defaults={"category": "commodities", "min_qty": Decimal("0.01")},
        )
        self.asset.category = "commodities"
        self.asset.min_qty = Decimal("0.01")
        self.asset.save(update_fields=["category", "min_qty"])
        self.policy = RiskPolicy.objects.create(
            broker_account=self.account,
            entries_enabled=True,
            max_spread_points=Decimal("30"),
            deviation_points=8,
        )
        self.account_info = SimpleNamespace(
            trade_mode=0,
            equity=500,
            balance=500,
            margin=0,
            margin_free=500,
            margin_level=0,
            currency="USD",
        )
        self.symbol_info = SimpleNamespace(
            point=0.001,
            digits=3,
            trade_stops_level=0,
            volume_min=0.01,
            volume_max=200,
            volume_step=0.01,
        )
        self.tick = SimpleNamespace(bid=Decimal("100.000"), ask=Decimal("100.260"))
        self.connector = SimpleNamespace(
            calc_profit_for_account=lambda *args: Decimal("-200"),
            calc_margin_for_account=lambda *args: Decimal("10"),
        )

    def _order(self, engine_mode="scalper", suffix="scalper"):
        bot = Bot.objects.create(
            name=f"Risk bot {suffix}",
            status="active",
            auto_trade=True,
            engine_mode=engine_mode,
            broker_account=self.account,
            asset=self.asset,
        )
        return Order.objects.create(
            bot=bot,
            broker_account=self.account,
            client_order_id=f"risk-{suffix}",
            symbol="XAUUSDm",
            side="buy",
            qty=Decimal("0.01"),
            sl=Decimal("98.260"),
            tp=Decimal("102.260"),
        )

    def test_scalper_uses_unit_aware_symbol_limits(self):
        result = enforce_pretrade_risk(
            self._order(),
            self.connector,
            self.tick,
            self.symbol_info,
            self.account_info,
        )

        self.assertEqual(result.volume, Decimal("0.01"))
        self.assertEqual(result.spread_points, Decimal("260"))
        self.assertEqual(result.spread_limit_points, Decimal("500"))
        self.assertEqual(result.deviation_points, 100)

    def test_non_scalper_keeps_account_spread_limit(self):
        with self.assertRaisesRegex(RiskRejected, "Spread exceeds configured limit"):
            enforce_pretrade_risk(
                self._order(engine_mode="external", suffix="external"),
                self.connector,
                self.tick,
                self.symbol_info,
                self.account_info,
            )

    def test_broker_current_manual_exposure_counts_toward_position_cap(self):
        self.policy.max_positions = 1
        self.policy.save(update_fields=["max_positions"])
        broker_positions = (SimpleNamespace(symbol="GBPUSD"),)

        with self.assertRaisesRegex(RiskRejected, "Maximum open positions reached"):
            enforce_pretrade_risk(
                self._order(suffix="broker-current-position"),
                self.connector,
                self.tick,
                self.symbol_info,
                self.account_info,
                broker_positions=broker_positions,
            )

    def test_account_drawdown_uses_persistent_high_water(self):
        self.policy.max_daily_loss_pct = Decimal("100")
        self.policy.max_account_drawdown_pct = Decimal("5")
        self.policy.equity_high_water = Decimal("1000")
        self.policy.save(
            update_fields=[
                "max_daily_loss_pct",
                "max_account_drawdown_pct",
                "equity_high_water",
            ]
        )
        self.account_info.equity = Decimal("949")
        self.account_info.balance = Decimal("949")

        with self.assertRaisesRegex(RiskRejected, "Maximum account drawdown reached"):
            enforce_pretrade_risk(
                self._order(suffix="persistent-drawdown"),
                self.connector,
                self.tick,
                self.symbol_info,
                self.account_info,
            )

        self.policy.refresh_from_db()
        self.assertEqual(self.policy.equity_high_water, Decimal("1000"))

    def test_stale_writer_cannot_lower_equity_high_water(self):
        self.policy.equity_high_water = Decimal("1000")
        self.policy.save(update_fields=["equity_high_water"])
        stale_policy = RiskPolicy.objects.get(pk=self.policy.pk)

        update_equity_high_water(self.policy, Decimal("1200"))
        drawdown = update_equity_high_water(stale_policy, Decimal("1100"))

        self.policy.refresh_from_db()
        self.assertEqual(self.policy.equity_high_water, Decimal("1200"))
        self.assertEqual(drawdown.quantize(Decimal("0.01")), Decimal("8.33"))

    @override_settings(ACCOUNT_RISK_OPENING_SNAPSHOT_GRACE_SECONDS=-1)
    def test_live_entry_fails_closed_when_daily_baseline_is_unavailable(self):
        self.policy.live_trading_confirmed = True
        self.policy.save(update_fields=["live_trading_confirmed"])
        self.account_info.trade_mode = 2

        with self.assertRaisesRegex(RiskRejected, "Daily risk baseline is unavailable"):
            enforce_pretrade_risk(
                self._order(suffix="missing-live-baseline"),
                self.connector,
                self.tick,
                self.symbol_info,
                self.account_info,
                broker_positions=(),
            )
