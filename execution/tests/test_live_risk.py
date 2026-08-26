from decimal import Decimal
from types import SimpleNamespace

from django.test import TestCase

from bots.models import Asset, Bot
from brokers.models import BrokerAccount
from execution.models import Order, RiskPolicy
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
