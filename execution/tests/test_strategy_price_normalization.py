from decimal import Decimal

from django.test import SimpleTestCase

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
