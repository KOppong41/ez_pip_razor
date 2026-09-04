from datetime import timedelta
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import patch

from django.test import TestCase
from django.utils import timezone

from bots.models import Asset, Bot
from brokers.models import BrokerAccount
from execution.models import JournalEntry
from execution.services.brokers import BrokerSymbolConstraints
from execution.tasks import trade_scalper_strategies_for_bot


class ScalperHtfSafetyTest(TestCase):
    def setUp(self):
        self.account = BrokerAccount.objects.create(
            name="HTF demo",
            broker="mt5",
            connector="mt5_local",
            account_ref="htf-safety",
            is_active=True,
            is_verified=True,
        )
        asset = Asset.objects.create(symbol="EURUSD", category="forex")
        self.bot = Bot.objects.create(
            name="HTF bot",
            status="active",
            auto_trade=True,
            broker_account=self.account,
            asset=asset,
            scalper_params={"strategy_profile": "profile"},
        )

    @patch("execution.tasks.build_scalper_config")
    @patch("execution.tasks.get_broker_symbol_constraints")
    @patch("execution.tasks.get_candles_for_account")
    @patch("execution.tasks.MT5Connector")
    @patch("execution.tasks.bot_is_available_for_trading", return_value=True)
    @patch("execution.tasks.get_market_status_for_bot")
    def test_missing_htf_bias_skips_before_strategy_execution(
        self,
        market_status,
        _available,
        connector_class,
        get_candles,
        constraints,
        scalper_config,
    ):
        market_status.return_value = SimpleNamespace(is_open=True, reason="test")
        constraints.return_value = SimpleNamespace(
            stops_level_points=Decimal("0"),
            point=Decimal("0.00001"),
            lot_step=Decimal("0.01"),
            min_lot=Decimal("0.01"),
            max_lot=Decimal("100"),
            freeze_level_points=None,
            max_deviation=Decimal("20"),
        )
        profile = SimpleNamespace(symbol="EURUSD", enabled_strategies=["harami"])
        scalper_config.return_value = SimpleNamespace(
            default_strategy_profile="profile",
            strategy_profiles={"profile": profile},
        )
        connector = connector_class.return_value
        connector.symbol_info_for_account.return_value = SimpleNamespace(
            visible=True,
            trade_mode=2,
        )
        connector.tick_for_account.return_value = SimpleNamespace(
            bid=1.1000,
            ask=1.1001,
            last=1.1000,
            time=timezone.now().timestamp(),
        )
        now = timezone.now()
        entry_bars = [
            {
                "time": now - timedelta(minutes=24 - index),
                "open": Decimal("1.1000"),
                "high": Decimal("1.1010"),
                "low": Decimal("1.0990"),
                "close": Decimal("1.1000"),
                "tick_volume": 1,
            }
            for index in range(25)
        ]
        get_candles.side_effect = [entry_bars, []]

        result = trade_scalper_strategies_for_bot.run(
            self.bot.id,
            timeframe="1m",
            n_bars=25,
        )

        self.assertEqual(result, {"status": "skipped", "reason": "htf_bias_unavailable"})
        self.assertFalse(self.bot.orders.exists())
        self.assertTrue(
            JournalEntry.objects.filter(
                bot=self.bot,
                event_type="scalper_engine_run",
                context__reason="htf_bias_unavailable",
            ).exists()
        )

    @patch("execution.tasks.build_scalper_config")
    @patch("execution.tasks.get_broker_symbol_constraints")
    @patch("execution.tasks.bot_is_available_for_trading", return_value=True)
    @patch("execution.tasks.get_market_status_for_bot")
    def test_missing_broker_constraints_skip_before_market_analysis(
        self,
        market_status,
        _available,
        constraints,
        scalper_config,
    ):
        market_status.return_value = SimpleNamespace(is_open=True, reason="test")
        constraints.return_value = BrokerSymbolConstraints()
        profile = SimpleNamespace(symbol="EURUSD", enabled_strategies=["harami"])
        scalper_config.return_value = SimpleNamespace(
            default_strategy_profile="profile",
            strategy_profiles={"profile": profile},
        )

        result = trade_scalper_strategies_for_bot.run(
            self.bot.id,
            timeframe="1m",
            n_bars=25,
        )

        self.assertEqual(
            result,
            {"status": "skipped", "reason": "broker_constraints_unavailable"},
        )
        event = JournalEntry.objects.get(
            bot=self.bot,
            event_type="scalper_engine_run",
            context__reason="broker_constraints_unavailable",
        )
        self.assertIn("point", event.context["missing_constraints"])
