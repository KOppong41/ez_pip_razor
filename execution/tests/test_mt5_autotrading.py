from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from django.test import SimpleTestCase, override_settings

from execution.connectors.base import ConnectorError
from execution.connectors.mt5 import _check_ready
from execution.services.mt5_autotrading import ensure_algo_trading_enabled
from execution.services.mt5_session import MT5SessionService


class MT5AutoTradingTest(SimpleTestCase):
    def test_external_python_api_block_is_actionable(self):
        api = MagicMock()
        api.terminal_info.return_value = SimpleNamespace(trade_allowed=True, tradeapi_disabled=True)
        with self.assertRaisesRegex(ConnectorError, "external Python trading"):
            ensure_algo_trading_enabled(api)
        api.order_send.assert_not_called()

    @override_settings(MT5_AUTO_ENABLE_ALGO_TRADING=True)
    def test_legacy_auto_enable_setting_cannot_override_operator_switch(self):
        api = MagicMock()
        api.terminal_info.return_value = SimpleNamespace(trade_allowed=False, tradeapi_disabled=False)
        with self.assertRaisesRegex(ConnectorError, "manually"):
            ensure_algo_trading_enabled(api, terminal_path="terminal64.exe")
        self.assertEqual([call[0] for call in api.mock_calls], ["terminal_info"])

    def test_login_and_reconnect_allow_monitoring_with_algo_trading_disabled(self):
        api = MagicMock()
        api.terminal_info.return_value = SimpleNamespace(trade_allowed=False, tradeapi_disabled=True)
        api.account_info.return_value = SimpleNamespace(login=42, server="demo", trade_allowed=False)
        with (patch.object(MT5SessionService, "_api", return_value=api),
              patch.object(MT5SessionService, "_initialized", False),
              patch.object(MT5SessionService, "_active_login", None),
              patch.object(MT5SessionService, "_active_server", None)):
            for _ in range(2):
                MT5SessionService.ensure_login(path="terminal64.exe", login=42, password="test", server="demo")
            self.assertTrue(MT5SessionService.health()["connected"])
        api.initialize.assert_called_once()
        api.order_send.assert_not_called()
        api.order_check.assert_not_called()

    def test_order_readiness_checks_current_operator_switch_even_on_warm_session(self):
        with patch("execution.connectors.mt5.mt5") as api:
            api.account_info.return_value = SimpleNamespace(trade_allowed=True)
            api.terminal_info.return_value = SimpleNamespace(trade_allowed=False, tradeapi_disabled=False)
            with self.assertRaisesRegex(ConnectorError, "manually"):
                _check_ready("BTCUSDm")
            api.terminal_info.return_value = SimpleNamespace(trade_allowed=True, tradeapi_disabled=True)
            with self.assertRaisesRegex(ConnectorError, "external Python trading"):
                _check_ready("BTCUSDm")
            api.order_send.assert_not_called()
