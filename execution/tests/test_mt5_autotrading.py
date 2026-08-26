import os
from pathlib import Path
from types import SimpleNamespace
from unittest import skipUnless
from unittest.mock import MagicMock, patch

from django.test import SimpleTestCase

from execution.connectors.base import ConnectorError
from execution.services.mt5_autotrading import ensure_algo_trading_enabled


class MT5AutoTradingTest(SimpleTestCase):
    def test_external_python_api_block_is_actionable(self):
        api = MagicMock()
        api.terminal_info.return_value = SimpleNamespace(
            trade_allowed=False, tradeapi_disabled=True
        )

        with self.assertRaisesRegex(ConnectorError, "external Python trading"):
            ensure_algo_trading_enabled(api, terminal_path="terminal64.exe")

    @skipUnless(os.name == "nt", "Windows terminal automation")
    @patch("win32com.client.Dispatch")
    @patch("execution.services.mt5_autotrading._terminal_window_pid", return_value=42)
    @patch.object(Path, "is_file", return_value=True)
    def test_ctrl_e_is_sent_once_and_verified(self, _is_file, _pid, dispatch):
        api = MagicMock()
        api.terminal_info.side_effect = [
            SimpleNamespace(trade_allowed=False, tradeapi_disabled=False),
            SimpleNamespace(trade_allowed=True, tradeapi_disabled=False),
        ]
        shell = dispatch.return_value
        shell.AppActivate.return_value = True

        ensure_algo_trading_enabled(
            api, terminal_path=r"C:\Program Files\MetaTrader 5\terminal64.exe"
        )

        shell.AppActivate.assert_called_once_with(42)
        shell.SendKeys.assert_called_once_with("^e")
