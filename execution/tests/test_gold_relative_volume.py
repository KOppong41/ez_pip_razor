from copy import deepcopy
from decimal import Decimal
from types import SimpleNamespace

from django.test import SimpleTestCase

from execution.services.strategy_registry import SCALPER_STRATEGY_REGISTRY, build_strategy_config_for_bot
from execution.services.strategies.volume import relative_tick_volume


class GoldRelativeVolumeTests(SimpleTestCase):
    def config(self, strategy, symbol="XAUUSDm", **overrides):
        bot = SimpleNamespace(asset=SimpleNamespace(symbol=symbol), asset_preset_version_applied=4,
                              asset_strategy_overrides_applied={strategy: {"min_relative_volume": 1, **overrides}})
        return build_strategy_config_for_bot(strategy, bot)

    def candles(self, strategy):
        def candle(opening, high, low, close):
            return dict(zip(("open", "high", "low", "close"), map(Decimal, (opening, high, low, close))), tick_volume=20)
        bars = [candle("100", "101", "99", "100") for _ in range(20)]
        if strategy == "breakout_retest":
            bars += [candle("100.8", "101.4", "100.7", "101.2"), candle("101.1", "101.3", "101", "101.1")]
        else:
            bars += [candle("100", "100.4", "99.9", "100.3"), candle("100.3", "100.8", "100.2", "100.7"),
                     candle("100.7", "101.2", "100.6", "101"), candle("101", "101.1", "100.7", "100.9")]
        bars[-2]["tick_volume"] = 30
        return bars

    def test_relative_volume_requires_an_explicit_applied_override(self):
        for strategy in ("momentum_ignition", "breakout_retest"):
            for symbol in ("XAUUSDm", "GOLD", "XAUUSD.micro"):
                cfg = self.config(strategy, symbol, min_tick_volume=999, min_breakout_volume=999)
                self.assertEqual(cfg.min_relative_volume, 1)
                self.assertEqual(cfg.volume_lookback, 20)
                legacy = SimpleNamespace(asset=SimpleNamespace(symbol=symbol), asset_preset_version_applied=3,
                                         asset_strategy_overrides_applied={strategy: {"min_tick_volume": 80, "min_breakout_volume": 80}})
                self.assertEqual(build_strategy_config_for_bot(strategy, legacy).min_relative_volume, 0)

    def test_both_gold_detectors_and_scores_are_invariant_to_feed_volume_scale(self):
        for strategy in ("momentum_ignition", "breakout_retest"):
            cfg = self.config(strategy, lookback=3, impulse_lookback=3)
            runner = SCALPER_STRATEGY_REGISTRY[strategy].runner
            original = runner(self.candles(strategy), cfg)
            self.assertEqual(original.action, "open", original)
            self.assertEqual(original.metadata["relative_volume"], 1.5)
            for multiplier in (1, 10, 100):
                bars = self.candles(strategy)
                for bar in bars:
                    bar["tick_volume"] *= multiplier
                result = runner(bars, cfg)
                self.assertEqual((result.action, result.direction, result.score),
                                 (original.action, original.direction, original.score))
                # A quiet signal is rejected even when its absolute count exceeds 80.
                bars[-2]["tick_volume"] = 10 * multiplier
                self.assertEqual(runner(bars, cfg).reason, f"{strategy}_low_volume")

    def test_baseline_excludes_signal_and_retest_and_uses_rolling_median(self):
        bars = [{"tick_volume": 20} for _ in range(22)]
        bars[0]["tick_volume"] = 100000
        bars[-2]["tick_volume"] = 40
        bars[-1]["tick_volume"] = 999999
        ratio, metadata = relative_tick_volume(bars, 20)
        self.assertEqual(ratio, 2)
        self.assertEqual(metadata["volume_baseline_median"], 20)
        self.assertEqual(relative_tick_volume([{"tick_volume": 999999}] + bars, 20)[0], 2)

    def test_unusable_baselines_skip_both_detectors_without_absolute_fallback(self):
        for strategy in ("momentum_ignition", "breakout_retest"):
            cfg = self.config(strategy, lookback=3, impulse_lookback=3)
            runner = SCALPER_STRATEGY_REGISTRY[strategy].runner
            for bad_volume in (None, -1, "NaN", "Infinity", "invalid", 0):
                bars = self.candles(strategy)
                for bar in bars[:-2]:
                    bar["tick_volume"] = bad_volume
                self.assertEqual(runner(bars, cfg).reason, f"{strategy}_volume_unavailable")
            self.assertEqual(runner(self.candles(strategy)[-6:], cfg).reason, f"{strategy}_volume_unavailable")
            bars = self.candles(strategy)
            bars[-2].pop("tick_volume")
            self.assertEqual(runner(bars, cfg).reason, f"{strategy}_volume_unavailable")

    def test_stronger_relative_volume_improves_only_volume_score_component(self):
        for strategy in ("momentum_ignition", "breakout_retest"):
            cfg = self.config(strategy, lookback=3, impulse_lookback=3)
            runner = SCALPER_STRATEGY_REGISTRY[strategy].runner
            bars = self.candles(strategy)
            weak = runner(bars, cfg)
            strong_bars = deepcopy(bars)
            strong_bars[-2]["tick_volume"] = 40
            strong = runner(strong_bars, cfg)
            self.assertGreater(strong.score, weak.score)
            self.assertEqual(strong.metadata["volume_baseline_median"], weak.metadata["volume_baseline_median"])
            for key, value in weak.metadata["score_components"].items():
                if key != "volume":
                    self.assertEqual(strong.metadata["score_components"][key], value)
