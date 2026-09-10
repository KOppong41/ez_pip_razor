from copy import deepcopy
from datetime import datetime, time, timezone as dt_timezone
from decimal import Decimal
from types import SimpleNamespace

from django.test import SimpleTestCase

from execution.services.protection_policy import validate_protection
from execution.services.strategies.breakout_retest import (
    BreakoutRetestConfig,
    run_breakout_retest,
)
from execution.services.strategies.momentum_ignition import (
    MomentumIgnitionConfig,
    run_momentum_ignition,
)
from execution.services.strategies.price_action_pinbar import PinBarConfig
from execution.services.strategies.range_reversion import (
    RangeReversionConfig,
    run_range_reversion,
)
from execution.services.strategies.scalper import _estimate_sl_distance_points
from execution.services.strategies.scalper import _score_components
from execution.services.trading_type import is_within_trading_window


class AssetPresetProtectionTests(SimpleTestCase):
    def test_fixed_tp_requires_both_protection_prices(self):
        self.assertEqual(
            validate_protection(
                intent="entry", sl=Decimal("99"), tp=None, exit_mode="fixed_tp"
            ),
            (False, "missing_tp"),
        )

    def test_managed_entry_accepts_sl_without_tp(self):
        for mode in ("hybrid", "trail_only"):
            with self.subTest(mode=mode):
                self.assertEqual(
                    validate_protection(
                        intent="entry", sl=Decimal("99"), tp=None, exit_mode=mode
                    ),
                    (True, "ok"),
                )

    def test_exit_needs_neither_sl_nor_tp(self):
        self.assertEqual(
            validate_protection(intent="exit", sl=None, tp=None),
            (True, "ok"),
        )

    def test_broker_stop_level_overrides_tight_percent_preset(self):
        symbol_config = SimpleNamespace(
            sl_points_min=Decimal("0.10"),
            sl_points_max=Decimal("0.30"),
            sl_points_unit="percent",
        )
        distance = _estimate_sl_distance_points(
            symbol_config,
            {
                "point": "0.01",
                "digits": 2,
                "entry": "100",
                "atr_price": "0.20",
                "min_stop_points": "50",
            },
        )
        self.assertEqual(distance, Decimal("0.5"))


class AssetPresetScheduleTests(SimpleTestCase):
    @staticmethod
    def _bot(**overrides):
        values = {
            "trading_schedule_enabled": True,
            "trading_timezone": "America/New_York",
            "allowed_trading_days": ["mon", "tue", "wed", "thu", "fri"],
            "trading_window_start": time(8, 0),
            "trading_window_end": time(12, 0),
            "trading_profile": "very_safe",
        }
        values.update(overrides)
        return SimpleNamespace(**values)

    def test_new_york_window_is_dst_safe(self):
        bot = self._bot()
        winter = datetime(2026, 1, 12, 13, 30, tzinfo=dt_timezone.utc)
        summer = datetime(2026, 7, 13, 12, 30, tzinfo=dt_timezone.utc)
        before_summer_window = datetime(2026, 7, 13, 11, 30, tzinfo=dt_timezone.utc)

        self.assertTrue(is_within_trading_window(bot, winter))
        self.assertTrue(is_within_trading_window(bot, summer))
        self.assertFalse(is_within_trading_window(bot, before_summer_window))

    def test_disabled_crypto_schedule_allows_weekends(self):
        bot = self._bot(
            trading_schedule_enabled=False,
            trading_timezone="UTC",
            allowed_trading_days=["mon", "tue", "wed", "thu", "fri", "sat", "sun"],
        )
        saturday = datetime(2026, 9, 12, 3, 0, tzinfo=dt_timezone.utc)
        self.assertTrue(is_within_trading_window(bot, saturday))


class AssetPresetScoreTests(SimpleTestCase):
    def assert_normalized_open(self, decision):
        self.assertEqual(decision.action, "open", decision.reason)
        self.assertGreaterEqual(decision.score, 0.0)
        self.assertLessEqual(decision.score, 1.0)

    def test_breakout_retest_score_is_normalized(self):
        candles = [
            {"open": Decimal("100"), "high": Decimal("101"), "low": Decimal("99"), "close": Decimal("100"), "tick_volume": 100},
            {"open": Decimal("100"), "high": Decimal("100.8"), "low": Decimal("99.2"), "close": Decimal("100.2"), "tick_volume": 100},
            {"open": Decimal("100.2"), "high": Decimal("100.9"), "low": Decimal("99.4"), "close": Decimal("100.5"), "tick_volume": 100},
            {"open": Decimal("100.8"), "high": Decimal("101.4"), "low": Decimal("100.7"), "close": Decimal("101.2"), "tick_volume": 100},
            {"open": Decimal("101.1"), "high": Decimal("101.3"), "low": Decimal("101.0"), "close": Decimal("101.1"), "tick_volume": 100},
        ]
        self.assert_normalized_open(
            run_breakout_retest(candles, BreakoutRetestConfig(lookback=3))
        )

        stronger = deepcopy(candles)
        stronger[-2]["tick_volume"] = 160
        weak = run_breakout_retest(candles, BreakoutRetestConfig(lookback=3))
        strong = run_breakout_retest(stronger, BreakoutRetestConfig(lookback=3))
        self.assertLess(weak.score, strong.score)
        self.assertLess(weak.score, 1.0)
        self.assertIn("score_components", weak.metadata)

    def test_momentum_ignition_score_is_normalized(self):
        candles = [
            {"open": Decimal("99.9"), "high": Decimal("100.1"), "low": Decimal("99.8"), "close": Decimal("100"), "tick_volume": 100},
            {"open": Decimal("100"), "high": Decimal("100.4"), "low": Decimal("99.9"), "close": Decimal("100.3"), "tick_volume": 100},
            {"open": Decimal("100.3"), "high": Decimal("100.8"), "low": Decimal("100.2"), "close": Decimal("100.7"), "tick_volume": 100},
            {"open": Decimal("100.7"), "high": Decimal("101.2"), "low": Decimal("100.6"), "close": Decimal("101"), "tick_volume": 100},
            {"open": Decimal("101"), "high": Decimal("101.1"), "low": Decimal("100.7"), "close": Decimal("100.9"), "tick_volume": 100},
        ]
        self.assert_normalized_open(
            run_momentum_ignition(
                candles,
                MomentumIgnitionConfig(impulse_lookback=3, session_hours=()),
            )
        )

        stronger = deepcopy(candles)
        stronger[-2]["tick_volume"] = 160
        stronger[-1]["close"] = Decimal("101.15")
        stronger[-1]["high"] = Decimal("101.2")
        weak = run_momentum_ignition(
            candles,
            MomentumIgnitionConfig(impulse_lookback=3),
        )
        strong = run_momentum_ignition(
            stronger,
            MomentumIgnitionConfig(impulse_lookback=3),
        )
        self.assertLess(weak.score, strong.score)
        self.assertLess(weak.score, 1.0)
        self.assertIn("score_components", weak.metadata)

    def test_strategy_detectors_have_no_hidden_default_session_gate(self):
        self.assertEqual(MomentumIgnitionConfig().session_hours, ())
        self.assertEqual(PinBarConfig().session_hours, ())

    def test_range_reversion_score_is_normalized(self):
        candles = [
            {"open": Decimal("101"), "high": Decimal("102"), "low": Decimal("100"), "close": Decimal("101"), "tick_volume": 100},
            {"open": Decimal("101"), "high": Decimal("101.8"), "low": Decimal("100.2"), "close": Decimal("101.7"), "tick_volume": 100},
            {"open": Decimal("101"), "high": Decimal("101.9"), "low": Decimal("100.3"), "close": Decimal("100.4"), "tick_volume": 100},
            {"open": Decimal("101.4"), "high": Decimal("101.9"), "low": Decimal("100.5"), "close": Decimal("101.8"), "tick_volume": 100},
        ]
        self.assert_normalized_open(
            run_range_reversion(candles, RangeReversionConfig(lookback=3))
        )

    def test_range_reversion_rejects_directionally_efficient_trend(self):
        candles = [
            {
                "open": Decimal(index),
                "high": Decimal(index) + Decimal("1.2"),
                "low": Decimal(index) - Decimal("0.2"),
                "close": Decimal(index) + Decimal("1"),
                "tick_volume": 100,
            }
            for index in range(100, 106)
        ]

        decision = run_range_reversion(
            candles,
            RangeReversionConfig(lookback=5),
        )

        self.assertEqual(decision.action, "skip")
        self.assertEqual(decision.reason, "range_reversion_trending_regime")

    def test_scalper_score_uses_percent_spread_and_atr_context(self):
        symbol = SimpleNamespace(
            sl_points_min=Decimal("0.10"),
            sl_points_max=Decimal("0.30"),
            sl_points_unit="percent",
            max_spread_points=Decimal("0.015"),
            max_spread_unit="percent",
        )
        config = SimpleNamespace(sessions=())
        payload = {
            "point": "0.001",
            "digits": 3,
            "close": "3000",
            "atr_price": "3",
            "spread_price": "0.40",
        }

        _, components = _score_components(
            "buy", "buy", False, Decimal("0.20"), symbol, config, payload
        )
        self.assertEqual(Decimal(str(components["market"])), Decimal("0.2"))

        payload["spread_price"] = "0.50"
        _, wide_components = _score_components(
            "buy", "buy", False, Decimal("0.20"), symbol, config, payload
        )
        self.assertEqual(
            Decimal(str(wide_components["market"])),
            Decimal("0.08"),
        )
