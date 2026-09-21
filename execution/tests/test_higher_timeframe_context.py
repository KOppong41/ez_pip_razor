from decimal import Decimal
from unittest.mock import Mock

from django.test import SimpleTestCase

from execution.services.higher_timeframe_context import analyze_context
from execution.tasks import _analyze_htf_bias


class HigherTimeframeContextTests(SimpleTestCase):
    def test_flat_candles_are_neutral_and_both_frames_are_inspected(self):
        candles = [{"close": Decimal("100"), "high": Decimal("101"), "low": Decimal("99")}
                   for _ in range(40)]
        fetch = Mock(return_value=candles)
        bias, details, reason = analyze_context(["H1", "M15"], fetch, _analyze_htf_bias)
        self.assertEqual(reason, "htf_bias_neutral")
        self.assertIsNone(bias)
        self.assertEqual(list(details), ["15m", "1h"])
        self.assertEqual([call.args[0] for call in fetch.call_args_list], ["15m", "1h"])
        for detail in details.values():
            self.assertIsNone(detail["bias"])
            self.assertEqual(detail["position_in_range"], 0.5)

    def test_missing_or_malformed_analysis_is_unavailable_even_with_a_neutral_peer(self):
        for unavailable in (None, {}, {"bias": "invalid"}):
            for frame in ("15m", "1h"):
                with self.subTest(frame=frame, unavailable=unavailable):
                    frames = {"15m": {"bias": None}, "1h": {"bias": None}}
                    frames[frame] = unavailable
                    bias, details, reason = analyze_context(frames, frames.get, lambda value: value)
                    self.assertEqual(reason, "htf_bias_unavailable")
                    self.assertIsNone(bias)
                    self.assertEqual(details, frames)

    def test_directional_and_neutral_frames_still_block_entries(self):
        # These are the final recorded Gold biases on 18 September.
        frames = {"15m": {"bias": "buy"}, "1h": {"bias": None, "position_in_range": 0.5429524604}}
        bias, details, reason = analyze_context(frames, frames.get, lambda value: value)
        self.assertIsNone(bias)
        self.assertEqual(reason, "htf_bias_neutral")
        self.assertEqual(details, frames)

    def test_directional_agreement_passes_and_disagreement_remains_blocked(self):
        for direction in ("buy", "sell"):
            frames = {"15m": {"bias": direction}, "1h": {"bias": direction}}
            bias, details, reason = analyze_context(frames, frames.get, lambda value: value)
            self.assertEqual((bias, reason), (direction, None))
            self.assertEqual(details["dominant_timeframe"], "1h")
            self.assertEqual(details["immediate_timeframe"], "15m")
            frames["1h"]["bias"] = "sell" if direction == "buy" else "buy"
            self.assertEqual(analyze_context(frames, frames.get, lambda value: value)[::2],
                             (None, "htf_context_conflict"))

    def test_unsupported_timeframe_does_not_fetch_candles(self):
        fetch = Mock()
        self.assertEqual(analyze_context(["M15", "invalid"], fetch, _analyze_htf_bias),
                         (None, {}, "htf_timeframe_unsupported"))
        fetch.assert_not_called()
