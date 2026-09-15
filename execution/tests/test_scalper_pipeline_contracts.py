from copy import deepcopy
from datetime import datetime, time, timezone as dt_timezone
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import patch

from django.test import TestCase

from bots.models import Asset, Bot
from bots.services import apply_recommendations_to_bot
from brokers.models import BrokerAccount
from execution.models import Signal
from execution.services.brokers import BrokerSymbolConstraints
from execution.services.decision import _build_scalp_params, make_decision_from_signal
from execution.services.scalper_config import build_scalper_config, resolve_allowed_strategy_pool
from execution.services.strategies.scalper import _score_components, plan_scalper_trade
from execution.services.strategy_registry import build_strategy_config_for_bot


class ScalperPipelineContracts(TestCase):
    def setUp(self):
        self.account = BrokerAccount.objects.create(name="Replay", broker="paper", connector="paper", account_ref="pipeline")
        self.asset = Asset.objects.get(symbol="EURUSDm")
        self.bot = Bot.objects.create(
            name="Pipeline", broker_account=self.account, asset=self.asset,
            engine_mode="scalper", auto_trade=True, status="active",
            trading_schedule_enabled=False, trade_interval_minutes=0,
            decision_min_score=Decimal("0.50"),
            scalper_params={"rollover_blackout": []},
        )
        self.clock = patch("django.utils.timezone.now", return_value=datetime(2026, 9, 15, 23, tzinfo=dt_timezone.utc))
        self.clock.start()
        self.addCleanup(self.clock.stop)
        self.news = patch("execution.services.economic_news.is_economic_news_blackout", return_value=False)
        self.news.start()
        self.addCleanup(self.news.stop)

    def signal(self, **overrides):
        values = dict(
            bot=self.bot, source="scalper_engine", symbol=self.asset.symbol,
            timeframe="5m", direction="buy", dedupe_key=f"contract-{Signal.objects.count()}",
            payload={"sl": "1.098765", "tp": "1.105432", "close": "1.1000", "score": 0.73,
                     "point": "0.00001", "digits": 5, "reason": "detector_setup"},
        )
        values.update(overrides)
        return Signal.objects.create(**values)

    @patch("execution.services.decision.plan_scalper_trade", side_effect=AssertionError("must not re-plan"))
    def test_engine_protection_and_score_are_preserved(self, planner):
        signal = self.signal()
        original = deepcopy(signal.payload)
        decision = make_decision_from_signal(signal)
        self.assertEqual(decision.action, "open")
        self.assertEqual(decision.score, 0.73)
        self.assertEqual(decision.reason, "detector_setup")
        self.assertEqual(decision.params["sl"], original["sl"])
        self.assertEqual(decision.params["tp"], original["tp"])
        self.assertIn("scalper", decision.params)
        signal.refresh_from_db()
        self.assertEqual(signal.payload["score"], original["score"])
        self.assertEqual(make_decision_from_signal(signal).id, decision.id)
        planner.assert_not_called()

    def test_schedule_disabled_does_not_install_profile_sessions(self):
        self.bot.scalper_params["sessions"] = [{"start": "05:00", "end": "21:00"}]
        config = build_scalper_config(self.bot)
        self.assertEqual(config.sessions, ())
        signal = self.signal(source="external")
        signal.payload.pop("sl")
        decision = plan_scalper_trade(signal, self.bot, config)
        self.assertEqual(decision.action, "open")

    def test_legacy_detector_tuning_cannot_install_a_second_schedule(self):
        self.bot.asset_preset_version_applied = 1
        self.bot.asset_strategy_overrides_applied = {
            name: {"session_hours": [[5, 21]]}
            for name in ("price_action_pinbar", "momentum_ignition")
        }
        for name in self.bot.asset_strategy_overrides_applied:
            self.assertEqual(build_strategy_config_for_bot(name, self.bot).session_hours, ())

    def test_configured_window_applies_to_forex_and_crypto(self):
        self.bot.trading_schedule_enabled = True
        self.bot.trading_timezone = "UTC"
        self.bot.allowed_trading_days = ["tue"]
        self.bot.trading_window_start, self.bot.trading_window_end = time(22), time(23, 59)
        self.bot.save()
        self.assertEqual(make_decision_from_signal(self.signal()).action, "open")
        self.bot.trading_window_end = time(22, 30)
        self.bot.save()
        blocked = make_decision_from_signal(self.signal())
        self.assertEqual(blocked.reason, "outside_trading_window")
        self.bot.asset = Asset.objects.get(symbol="BTCUSDm")
        self.bot.save()
        blocked = make_decision_from_signal(self.signal(symbol="BTCUSDm", timeframe="1m"))
        self.assertEqual(blocked.reason, "outside_trading_window")

    def test_engine_signal_still_enforces_news_and_score(self):
        with patch("execution.services.economic_news.is_economic_news_blackout", return_value=True):
            self.assertEqual(make_decision_from_signal(self.signal()).reason, "scalper:news_blackout")
        signal = self.signal()
        signal.payload["score"] = 0.2
        signal.save()
        self.assertEqual(make_decision_from_signal(signal).reason, "score_below_min")

    def test_full_preset_stays_frozen_until_reapplied(self):
        apply_recommendations_to_bot(self.bot, save=False)
        self.bot.save()
        before = build_scalper_config(self.bot).resolve_symbol(self.asset.symbol)
        preset = deepcopy(self.asset.recommended_config)
        preset["symbol_config"].update({
            "sl_points": {"min": 1, "max": 2, "unit": "percent"},
            "exit_mode": "hybrid", "trail_start_r": 0.5, "tp1_r": 1,
            "tp1_close_pct": 50, "be_buffer_r": 0.4,
            "max_spread_points": 42, "max_slippage_points": 24,
        })
        preset["context_timeframes"] = ["4h"]
        preset["enabled_strategies"] = ["range_reversion"]
        self.asset.recommended_config = preset
        self.asset.save()
        self.bot.refresh_from_db()
        self.assertEqual(build_scalper_config(self.bot).resolve_symbol(self.asset.symbol), before)
        self.bot.enabled_strategies = []
        self.assertNotEqual(resolve_allowed_strategy_pool(self.bot)[0], ["range_reversion"])
        apply_recommendations_to_bot(self.bot, save=False)
        after = build_scalper_config(self.bot).resolve_symbol(self.asset.symbol)
        self.assertEqual(after.sl_points_min, Decimal("1"))
        self.assertEqual(after.exit_mode, "hybrid")
        self.assertEqual(after.context_timeframes, ("4h",))

    def test_weighted_scores_distinguish_moderate_good_and_strong_setups(self):
        config = build_scalper_config(self.bot)
        symbol = config.resolve_symbol(self.asset.symbol)
        scores = []
        for strength, confidence in (("0", "0.5"), ("0.0001", "0.75"), ("0.00025", "1")):
            score, components = _score_components(
                "buy", "buy", False, symbol.sl_points_min, symbol, config,
                {"htf_bias_detail": {"ema_slope_pct": strength}, "strategy_metrics": {"confidence": confidence},
                 "session": "london", "point": "0.00001", "digits": 5, "spread_price": "0.00001"},
            )
            scores.append(score)
            self.assertLessEqual(score, 1)
            self.assertAlmostEqual(float(score), sum(components.values()))
        self.assertLess(scores[0], scores[1])
        self.assertLess(scores[1], scores[2])
        self.assertLess(scores[2], 1)

    @patch("execution.services.decision.get_price", return_value=Decimal("100"))
    def test_opposite_scalps_use_broker_point_and_digits(self, price):
        for name, point, digits in (("USDJPY", "0.001", 3), ("US30", "0.1", 1), ("XAGUSD", "0.001", 3), ("BTCUSD", "0.01", 2)):
            with self.subTest(symbol=name):
                signal = SimpleNamespace(bot=self.bot, symbol=name, direction="buy", timeframe="5m", payload={"atr_price": str(Decimal(point) * 100)})
                config = SimpleNamespace(time_in_trade_limit_min=30)
                constraints = BrokerSymbolConstraints(point=Decimal(point), digits=digits, stops_level_points=Decimal("150"))
                with patch("execution.services.brokers.get_broker_symbol_constraints", return_value=constraints):
                    params = _build_scalp_params(signal, scalper_cfg=config)
                self.assertEqual(Decimal(params["sl"]), Decimal("100") - Decimal(point) * 150)

    @patch("execution.services.decision.get_price", return_value=Decimal("100"))
    @patch("execution.services.brokers.get_broker_symbol_constraints", return_value=BrokerSymbolConstraints())
    def test_opposite_scalp_does_not_guess_missing_broker_point(self, constraints, price):
        with self.assertRaisesRegex(ValueError, "broker_point_unavailable"):
            _build_scalp_params(self.signal(), scalper_cfg=build_scalper_config(self.bot))
