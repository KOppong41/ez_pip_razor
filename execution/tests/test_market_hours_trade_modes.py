from datetime import timedelta
from types import SimpleNamespace
from unittest.mock import patch

from django.test import SimpleTestCase, override_settings
from django.utils import timezone

from execution.services.market_hours import (
    get_market_status,
    trade_mode_allows_entry,
    trade_mode_status,
)


class MT5SymbolTradeModeTests(SimpleTestCase):
    def test_all_documented_modes_are_mapped(self):
        self.assertEqual(
            [trade_mode_status(mode) for mode in range(5)],
            ["disabled", "long_only", "short_only", "close_only", "open"],
        )

    def test_entry_direction_is_enforced(self):
        self.assertFalse(trade_mode_allows_entry(0, "buy"))
        self.assertTrue(trade_mode_allows_entry(1, "buy"))
        self.assertFalse(trade_mode_allows_entry(1, "sell"))
        self.assertFalse(trade_mode_allows_entry(2, "buy"))
        self.assertTrue(trade_mode_allows_entry(2, "sell"))
        self.assertFalse(trade_mode_allows_entry(3, "sell"))
        self.assertTrue(trade_mode_allows_entry(4, "buy"))
        self.assertTrue(trade_mode_allows_entry(4, "sell"))
        self.assertFalse(trade_mode_allows_entry(None, "buy"))

    def test_directional_modes_are_open_for_health_checks(self):
        self.assertTrue(trade_mode_allows_entry(1))
        self.assertTrue(trade_mode_allows_entry(2))


@override_settings(MT5_TICK_FUTURE_TOLERANCE_SECONDS=120)
class MT5MarketClockTests(SimpleTestCase):
    @patch("execution.services.market_hours.is_mt5_available", return_value=True)
    @patch("execution.services.market_hours.MT5Connector")
    def test_future_tick_closes_entry_gate_with_clock_skew_reason(
        self,
        connector_class,
        _available,
    ):
        now = timezone.now()
        connector = connector_class.return_value
        connector.symbol_info_for_account.return_value = SimpleNamespace(
            visible=True,
            trade_mode=4,
        )
        connector.tick_for_account.return_value = SimpleNamespace(
            time=(now + timedelta(seconds=121)).timestamp()
        )

        status = get_market_status(
            symbol="BTCUSDm",
            asset_category="crypto",
            broker_account=SimpleNamespace(id=1),
            now=now,
            use_mt5_probe=True,
            side="buy",
        )

        self.assertFalse(status.is_open)
        self.assertEqual(status.reason, "clock_skew")
        self.assertGreater(status.details["clock_skew_seconds"], 120)
