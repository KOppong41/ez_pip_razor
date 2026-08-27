from datetime import datetime, timedelta
from decimal import Decimal
from unittest.mock import patch

from django.test import SimpleTestCase

from execution.management.commands.backtest_engine import (
    BacktestConfig,
    _maybe_exit,
    backtest_engine,
    resample_candles,
)
from execution.services.engine import EngineDecision


def candle(at, open_, high, low, close):
    return {
        "time": at,
        "open": Decimal(str(open_)),
        "high": Decimal(str(high)),
        "low": Decimal(str(low)),
        "close": Decimal(str(close)),
        "tick_volume": 1,
    }


class BacktestEconomicsTest(SimpleTestCase):
    def setUp(self):
        self.now = datetime(2026, 1, 1, 9, 0)
        self.position = {
            "direction": "buy",
            "raw_entry_price": Decimal("1.1000"),
            "entry_price": Decimal("1.10006"),
            "entry_time": self.now,
            "sl": Decimal("1.0900"),
            "tp": Decimal("1.1100"),
            "score": 1.0,
        }
        self.bar = candle(
            self.now + timedelta(minutes=5),
            "1.1000",
            "1.1200",
            "1.0800",
            "1.1050",
        )

    def test_same_bar_policy_is_explicit(self):
        stop = _maybe_exit(self.position, self.bar, BacktestConfig(same_bar_policy="stop_first"))
        target = _maybe_exit(self.position, self.bar, BacktestConfig(same_bar_policy="target_first"))
        self.assertEqual(stop.reason, "sl")
        self.assertEqual(target.reason, "tp")

    def test_net_pnl_includes_spread_slippage_and_commission(self):
        cfg = BacktestConfig(
            quantity=Decimal("1"),
            contract_size=Decimal("100000"),
            point_size=Decimal("0.00001"),
            spread_points=Decimal("10"),
            slippage_points=Decimal("1"),
            commission_per_lot=Decimal("7"),
            same_bar_policy="target_first",
        )
        result = _maybe_exit(self.position, self.bar, cfg)
        self.assertEqual(result.gross_pnl, Decimal("1000.0000"))
        self.assertEqual(result.spread_cost, Decimal("10.00000"))
        self.assertEqual(result.slippage_cost, Decimal("2.00000"))
        self.assertEqual(result.commission, Decimal("7"))
        self.assertEqual(result.pnl, Decimal("981.00000"))

    def test_htf_resampling_uses_completed_group_timestamp(self):
        bars = [
            candle(self.now + timedelta(minutes=5 * i), 1 + i, 2 + i, i, Decimal("1.5") + i)
            for i in range(6)
        ]
        htf = resample_candles(bars, 3)
        self.assertEqual(len(htf), 2)
        self.assertEqual(htf[0]["time"], bars[2]["time"])
        self.assertEqual(htf[0]["open"], bars[0]["open"])
        self.assertEqual(htf[0]["close"], bars[2]["close"])

    @patch("execution.management.commands.backtest_engine.run_engine")
    def test_backtest_passes_only_completed_htf_bars(self, run_engine):
        bars = [
            candle(self.now + timedelta(minutes=5 * i), "1.1000", "1.1010", "1.0990", "1.1000")
            for i in range(7)
        ]
        run_engine.return_value = EngineDecision(action="skip")
        backtest_engine(bars, "EURUSD", "5m", 0.5, 1, htf_multiple=3)
        first_context = run_engine.call_args_list[0].args[0]
        second_context = run_engine.call_args_list[1].args[0]
        self.assertIsNone(first_context.htf_candles)
        self.assertIsNotNone(second_context.htf_candles)
        self.assertEqual(second_context.htf_candles[-1]["time"], bars[2]["time"])
