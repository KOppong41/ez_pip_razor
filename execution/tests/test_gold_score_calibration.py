from decimal import Decimal

from django.test import SimpleTestCase

from execution.services.strategies.scoring import above_minimum, score_setup
from execution.services.strategies.trend_pullback import TrendPullbackConfig, _atr, _ema, run_trend_pullback
from execution.services.strategies.momentum_ignition import MomentumIgnitionConfig, run_momentum_ignition
from execution.services.strategies.price_action_pinbar import run_price_action_pinbar
from execution.services.strategies.doji_breakout import run_doji_breakout
from execution.tasks import _rank_scalper_candidates
from execution.tests.test_strategy_price_normalization import _scaled_pinbar_candles


class GoldScoreCalibrationTests(SimpleTestCase):
    def test_shared_contract_requires_broad_quality_for_exceptional_score(self):
        weights = {"trend": ".30", "atr": ".15", "pullback": ".30", "rejection": ".25"}
        self.assertEqual(score_setup(dict.fromkeys(weights, 0), weights)[0], Decimal(".5"))
        self.assertEqual(score_setup(dict.fromkeys(weights, 1), weights)[0], Decimal("1"))
        components = dict.fromkeys(weights, 0)
        components["trend"] = 100
        self.assertEqual(score_setup(components, weights)[0], Decimal(".65"))
        self.assertEqual(above_minimum(Decimal("2"), Decimal("1")), Decimal(".5"))

    def test_strong_momentum_beats_threshold_trend_on_same_gold_candles(self):
        closes = [Decimal("2300") + Decimal(i) * Decimal(".05") for i in range(85)]
        closes += list(map(Decimal, ("2304.5", "2305.2", "2306", "2306.3", "2305.2")))
        candles = [{"open": close - Decimal(".1"), "close": close,
                    "high": close + Decimal(".4"), "low": close - Decimal(".5"), "tick_volume": 240}
                   for close in closes]
        ema = _ema(closes, 20)
        atr = _atr(candles, 12)
        slope = (ema[-1] - ema[-6]) / closes[-1]
        trend = run_trend_pullback(candles, TrendPullbackConfig(
            require_fractal_confirmation=False, min_trend_slope_pct=slope / 2,
            min_atr_pct=atr / closes[-1], pullback_atr_multiple=Decimal("1.5"),
            wick_rejection_ratio=Decimal("4"),
        ))
        momentum = run_momentum_ignition(candles, MomentumIgnitionConfig(min_impulse_pct=Decimal(".0002")))
        self.assertEqual((trend.action, momentum.action), ("open", "open"), (trend, momentum))
        self.assertLess(trend.score, .8)
        self.assertGreater(momentum.score, trend.score)
        ranked = _rank_scalper_candidates([
            {"bot_id": 1, "score": decision.score, "strategy": decision.strategy}
            for decision in (trend, momentum)
        ])
        self.assertEqual(ranked[0]["strategy"], "momentum_ignition")
        self.assertEqual(trend.metadata["score_contract"], "setup_quality_v1")
        self.assertAlmostEqual(trend.metadata["score_components"]["trend"], .75)

    def test_pin_and_doji_scores_share_contract_on_simultaneous_setups(self):
        candles = _scaled_pinbar_candles(Decimal("2300"))
        decisions = [run_price_action_pinbar("XAUUSD", candles), run_doji_breakout("XAUUSD", candles)]
        for decision in decisions:
            self.assertEqual(decision.action, "open", decision)
            self.assertEqual(decision.metadata["score_contract"], "setup_quality_v1")
            self.assertGreaterEqual(decision.score, .5)
            self.assertLess(decision.score, 1)
            self.assertTrue(all(.5 <= value <= 1 for value in decision.metadata["score_components"].values()))
        candidates = [{"bot_id": 2, "strategy": decision.strategy, "score": decision.score} for decision in decisions]
        self.assertEqual(_rank_scalper_candidates(candidates)[0]["score"], max(decision.score for decision in decisions))
