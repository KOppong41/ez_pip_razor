from django.test import SimpleTestCase

from execution.services.market_hours import trade_mode_allows_entry, trade_mode_status


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
