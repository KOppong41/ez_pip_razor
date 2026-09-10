from decimal import Decimal
from copy import deepcopy

from django.test import SimpleTestCase

from execution.services.strategies.doji_breakout import (
    _atr as _doji_atr,
    run_doji_breakout,
)
from execution.services.strategies.price_action_pinbar import PinBarConfig, run_price_action_pinbar
from execution.services.strategies.trend_pullback import _atr


def _scaled_pinbar_candles(scale: Decimal):
    rows = []
    for index in range(85):
        close = scale * (Decimal("1") + Decimal(index) * Decimal("0.0001"))
        rows.append(
            {
                "open": close - scale * Decimal("0.0002"),
                "high": close + scale * Decimal("0.001"),
                "low": close - scale * Decimal("0.001"),
                "close": close,
            }
        )
    close = scale * Decimal("1.0086")
    prior_close = scale * Decimal("1.0083")
    shared_low = close - scale * Decimal("0.002")
    rows[-2] = {
        "open": prior_close - scale * Decimal("0.0002"),
        "high": prior_close + scale * Decimal("0.0001"),
        "low": shared_low,
        "close": prior_close,
    }
    rows[-1] = {
        "open": close - scale * Decimal("0.0002"),
        "high": close + scale * Decimal("0.0001"),
        "low": shared_low,
        "close": close,
    }
    return rows


def _scaled_doji_breakout_candles(scale: Decimal):
    rows = []
    for index in range(82):
        close = scale * (Decimal("1") + Decimal(index) * Decimal("0.0001"))
        rows.append(
            {
                "open": close - scale * Decimal("0.00005"),
                "high": close + scale * Decimal("0.0005"),
                "low": close - scale * Decimal("0.0005"),
                "close": close,
            }
        )

    doji_close = scale * Decimal("1.0083")
    shared_low = doji_close - scale * Decimal("0.0012")
    wick_candle = {
        "open": doji_close - scale * Decimal("0.00005"),
        "high": doji_close + scale * Decimal("0.0001"),
        "low": shared_low,
        "close": doji_close,
    }
    rows[-5] = dict(wick_candle)
    rows[-2] = dict(wick_candle)
    rows[-1] = {
        "open": doji_close + scale * Decimal("0.00025"),
        "high": doji_close + scale * Decimal("0.0005"),
        "low": doji_close,
        "close": doji_close + scale * Decimal("0.0004"),
    }
    return rows


class StrategyPriceNormalizationTests(SimpleTestCase):
    def test_atr_percentage_is_invariant_across_price_scales(self):
        low = _scaled_pinbar_candles(Decimal("1.1"))
        high = _scaled_pinbar_candles(Decimal("60000"))

        low_ratio = _atr(low, 12) / low[-1]["close"]
        high_ratio = _atr(high, 12) / high[-1]["close"]

        self.assertAlmostEqual(float(low_ratio), float(high_ratio), places=12)

    def test_pinbar_decision_is_invariant_across_price_scales(self):
        cfg = PinBarConfig(session_hours=())
        decisions = [
            run_price_action_pinbar("TEST", _scaled_pinbar_candles(scale), cfg)
            for scale in (Decimal("1.1"), Decimal("2300"), Decimal("60000"))
        ]

        self.assertEqual({decision.action for decision in decisions}, {"open"})
        self.assertEqual({decision.direction for decision in decisions}, {"buy"})
        self.assertLess(max(d.score for d in decisions) - min(d.score for d in decisions), 1e-12)
        self.assertTrue(all(0.0 <= decision.score <= 1.0 for decision in decisions))

    def test_doji_breakout_is_invariant_across_price_scales(self):
        decisions = [
            run_doji_breakout("TEST", _scaled_doji_breakout_candles(scale))
            for scale in (Decimal("1.1"), Decimal("2300"), Decimal("60000"))
        ]

        self.assertEqual({decision.action for decision in decisions}, {"open"})
        self.assertEqual({decision.direction for decision in decisions}, {"buy"})
        self.assertEqual(len({decision.reason for decision in decisions}), 1)
        self.assertTrue(all(0.0 <= decision.score <= 1.0 for decision in decisions))

    def test_doji_score_changes_with_breakout_quality(self):
        strong_candles = _scaled_doji_breakout_candles(Decimal("2300"))
        weak_candles = deepcopy(strong_candles)
        atr_price = _doji_atr(weak_candles[:-1], 12)
        boundary = weak_candles[-2]["high"]
        weak_close = boundary + atr_price * Decimal("0.11")
        weak_candles[-1] = {
            "open": boundary + atr_price * Decimal("0.105"),
            "high": weak_close + atr_price * Decimal("0.01"),
            "low": boundary,
            "close": weak_close,
        }

        strong = run_doji_breakout("TEST", strong_candles)
        weak = run_doji_breakout("TEST", weak_candles)

        self.assertEqual({strong.action, weak.action}, {"open"})
        self.assertNotEqual(strong.score, weak.score)
        self.assertIn("score_components", strong.metadata)
