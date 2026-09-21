from datetime import datetime, timedelta, timezone as dt_timezone
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import patch

from django.test import TestCase
from django.utils import timezone

from bots.models import Asset, Bot
from brokers.models import BrokerAccount
from execution.models import JournalEntry, ScalperRunLog
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
            trading_schedule_enabled=False,
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
            resolve_symbol=lambda symbol: SimpleNamespace(execution_timeframes=("1m",), context_timeframes=("15m",)),
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
            resolve_symbol=lambda symbol: SimpleNamespace(execution_timeframes=("1m",), context_timeframes=("15m",)),
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

    def test_context_rejections_keep_distinct_statuses_and_frame_evidence(self):
        bars = [{"open": Decimal("100"), "close": Decimal("100"),
                 "high": Decimal("101"), "low": Decimal("99"), "tick_volume": 100}
                for _ in range(40)]
        profile = SimpleNamespace(symbol="EURUSD", enabled_strategies=["harami"])
        config = SimpleNamespace(default_strategy_profile="profile", strategy_profiles={"profile": profile},
                                 resolve_symbol=lambda symbol: SimpleNamespace(
                                     execution_timeframes=("1m",), context_timeframes=("15m", "1h")))
        constraints = BrokerSymbolConstraints(point=Decimal(".01"), min_lot=Decimal(".01"),
                                               max_lot=Decimal("100"), lot_step=Decimal(".01"),
                                               stops_level_points=Decimal("0"))
        for h1_detail, reason, status in (
            (None, "htf_bias_unavailable", "unavailable"),
            ({"bias": None}, "htf_bias_neutral", "neutral"),
            ({"bias": "sell"}, "htf_context_conflict", "conflict"),
        ):
            with (
                self.subTest(reason=reason),
                patch("execution.tasks.build_scalper_config", return_value=config),
                patch("execution.tasks.get_market_status_for_bot", return_value=SimpleNamespace(is_open=True)),
                patch("execution.tasks.bot_is_available_for_trading", return_value=True),
                patch("execution.tasks.get_broker_symbol_constraints", return_value=constraints),
                patch("execution.tasks.get_candles_for_account", return_value=bars),
                patch("execution.tasks._analyze_htf_bias", side_effect=[{"bias": "buy"}, h1_detail]),
                patch("execution.tasks.MT5Connector") as connector,
                patch("execution.tasks.select_ai_strategies") as selector,
            ):
                connector.return_value.symbol_info_for_account.return_value = SimpleNamespace(visible=True, trade_mode=4)
                connector.return_value.tick_for_account.return_value = SimpleNamespace(
                    bid=100, ask=100.1, time=timezone.now().timestamp())
                result = trade_scalper_strategies_for_bot.run(self.bot.id, timeframe="1m")
            self.assertEqual(result, {"status": "skipped", "reason": reason})
            selector.assert_not_called()
            summary = ScalperRunLog.objects.filter(bot=self.bot).latest("id").summary
            self.assertEqual(summary["htf_status"], status)
            self.assertEqual(summary["rejection_reason"], reason)
            self.assertEqual(summary["context"]["context"], {"15m": {"bias": "buy"}, "1h": h1_detail})
            self.assertEqual(summary["strategies_evaluated"], [])
        self.assertFalse(self.bot.orders.exists())
        self.assertFalse(self.bot.signals.exists())

    def test_schedule_gate_precedes_candle_and_strategy_evaluation(self):
        self.bot.trading_schedule_enabled = True
        self.bot.trading_windows = [
            {"start": "08:00", "end": "11:00", "timezone": "Europe/London",
             "allowed_days": ["mon", "tue", "wed", "thu", "fri"]},
            {"start": "07:30", "end": "14:00", "timezone": "America/New_York",
             "allowed_days": ["mon", "tue", "wed", "thu", "fri"]},
        ]
        self.bot.save()
        with (
            patch("execution.tasks.get_market_status_for_bot", return_value=SimpleNamespace(is_open=True)),
            patch("execution.tasks.bot_is_available_for_trading", return_value=True),
            patch("execution.tasks.get_broker_symbol_constraints", return_value=BrokerSymbolConstraints()) as constraints,
            patch("execution.tasks.get_candles_for_account") as candles,
            patch("execution.tasks.select_ai_strategies") as selector,
        ):
            # Reproduce the post-restart time from the investigation.
            closed_at = datetime(2026, 9, 21, 18, 12, tzinfo=dt_timezone.utc)
            with patch("django.utils.timezone.now", return_value=closed_at):
                result = trade_scalper_strategies_for_bot.run(self.bot.id, timeframe="1m")
            self.assertEqual(result, {"status": "skipped", "reason": "outside_trading_window"})
            constraints.assert_not_called()
            candles.assert_not_called()
            selector.assert_not_called()
            summary = ScalperRunLog.objects.filter(bot=self.bot).latest("id").summary
            self.assertEqual(summary["htf_status"], "not_evaluated")
            self.assertEqual(summary["strategies_evaluated"], [])
            self.assertEqual(summary["context"]["schedule"]["windows"], self.bot.trading_windows)
            self.assertEqual(summary["context"]["schedule"]["checked_at"], closed_at.isoformat())

            # The next London window passes the schedule and reaches the next guard.
            with patch("django.utils.timezone.now", return_value=datetime(2026, 9, 22, 7, tzinfo=dt_timezone.utc)):
                result = trade_scalper_strategies_for_bot.run(self.bot.id, timeframe="1m")
            self.assertEqual(result["reason"], "broker_constraints_unavailable")
            constraints.assert_called_once()

            self.bot.trading_schedule_enabled = False
            self.bot.save(update_fields=["trading_schedule_enabled"])
            with patch("django.utils.timezone.now", return_value=closed_at):
                result = trade_scalper_strategies_for_bot.run(self.bot.id, timeframe="1m")
            self.assertEqual(result["reason"], "broker_constraints_unavailable")
        self.assertFalse(self.bot.orders.exists())
        self.assertFalse(self.bot.signals.exists())
