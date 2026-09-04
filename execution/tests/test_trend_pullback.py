from decimal import Decimal
from unittest.mock import patch

from django.test import SimpleTestCase

from execution.services.strategies.trend_pullback import (
    TrendPullbackConfig,
    run_trend_pullback,
)


def _candles():
    rows = []
    for idx in range(6):
        rows.append(
            {
                "open": Decimal("10.0"),
                "high": Decimal("10.3"),
                "low": Decimal("9.8"),
                "close": Decimal("10.1"),
                "time": idx,
            }
        )
    return rows


class TrendPullbackFractalTests(SimpleTestCase):
    def _config(self):
        return TrendPullbackConfig(
            ema_period=2,
            slope_lookback=1,
            min_trend_slope_pct=Decimal("0.001"),
            atr_period=2,
            min_atr_points=Decimal("0.1"),
            pullback_atr_multiple=Decimal("1"),
            wick_rejection_ratio=Decimal("1"),
            fractal_period=2,
        )

    @patch("execution.services.strategies.trend_pullback._atr", return_value=Decimal("1"))
    @patch("execution.services.strategies.trend_pullback._ema")
    @patch("execution.services.strategies.trend_pullback.fractals")
    def test_uses_latest_confirmed_fractal_not_unconfirmable_last_bar(self, fractals, ema, _atr):
        ema.return_value = [Decimal("9.5"), Decimal("9.6"), Decimal("9.7"), Decimal("9.8"), Decimal("9.9"), Decimal("10.0")]
        markers = [{"up": False, "down": False} for _ in range(6)]
        markers[3]["up"] = True  # len(6) - period(2) - 1
        fractals.return_value = markers

        result = run_trend_pullback(_candles(), self._config())

        self.assertEqual(result.action, "open")
        self.assertEqual(result.direction, "buy")

    @patch("execution.services.strategies.trend_pullback._atr", return_value=Decimal("1"))
    @patch("execution.services.strategies.trend_pullback._ema")
    @patch("execution.services.strategies.trend_pullback.fractals")
    def test_missing_confirmed_fractal_blocks_entry(self, fractals, ema, _atr):
        ema.return_value = [Decimal("9.5"), Decimal("9.6"), Decimal("9.7"), Decimal("9.8"), Decimal("9.9"), Decimal("10.0")]
        fractals.return_value = [{"up": False, "down": False} for _ in range(6)]

        result = run_trend_pullback(_candles(), self._config())

        self.assertEqual(result.action, "skip")
        self.assertEqual(result.reason, "trend_pullback_no_fractal")
