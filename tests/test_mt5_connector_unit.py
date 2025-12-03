import types
import unittest
from unittest import mock

from execution.connectors import mt5 as mt5_module


class FakeMT5:
    def __init__(self):
        self.select_calls = 0
        self.initialized = False
        self.shutdown_calls = 0

    def initialize(self, **kwargs):
        self.initialized = True
        return True

    def login(self, **kwargs):
        self.initialized = True
        return True

    def shutdown(self):
        self.shutdown_calls += 1
        self.initialized = False
        return True

    def symbol_select(self, symbol, enable):
        self.select_calls += 1
        # first call fails to force reconnect, subsequent succeed
        return self.select_calls > 1

    def last_error(self):
        if not self.initialized or self.select_calls == 1:
            return (-10004, "No IPC connection")
        return (0, "ok")

    def terminal_info(self):
        return types.SimpleNamespace(trade_allowed=True)

    def account_info(self):
        return types.SimpleNamespace(trade_allowed=True)

    def symbol_info(self, symbol):
        return types.SimpleNamespace(visible=True, trade_mode=1)


class MT5ConnectorReconnectTests(unittest.TestCase):
    def test_check_health_retries_on_ipc_error(self):
        fake_mt5 = FakeMT5()
        connector = mt5_module.MT5Connector()

        with mock.patch.object(mt5_module, "mt5", fake_mt5):
            mt5_module._MT5Session._initialized = False
            connector.check_health(
                {"login": 1, "password": "p", "server": "s", "path": "C:/tmp/terminal64.exe"},
                "EURUSDm",
            )
            self.assertGreaterEqual(fake_mt5.select_calls, 2)
            self.assertGreaterEqual(fake_mt5.shutdown_calls, 1)


if __name__ == "__main__":
    unittest.main()
