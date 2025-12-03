from django.test import TestCase
from unittest.mock import patch, MagicMock
from decimal import Decimal

from bots.models import Bot
from brokers.models import BrokerAccount
from execution.models import Signal, Decision, Order
from execution.services.orchestrator import create_order_from_decision
from execution.connectors.mt5 import MT5Connector

class MT5ConnectorTest(TestCase):
    def setUp(self):
        self.bot = Bot.objects.create(name="mt5bot", status="active")
        self.ba = BrokerAccount.objects.create(
            name="Exness Demo", broker="exness_mt5", account_ref="login123",
            creds={"login": 123, "password": "x", "server": "Exness-MT5Trial", "path": "C:\\\\Program Files\\\\MetaTrader 5\\\\terminal64.exe"}
        )
        self.sig = Signal.objects.create(source="t", symbol="EURUSD", timeframe="5m", direction="buy", payload={}, dedupe_key="mt5-1")
        self.dec = Decision.objects.create(bot=self.bot, signal=self.sig, action="open", reason="t", score=0.1, params={})
        self.order, _ = create_order_from_decision(self.dec, self.ba, "0.10")

    @patch("execution.connectors.mt5.mt5")
    def test_place_order_done(self, mt5):
        # initialize/login ok
        mt5.initialize.return_value = True
        mt5.login.return_value = True
        mt5.symbol_select.return_value = True
        # retcode DONE
        mt5.TRADE_RETCODE_DONE = 10009
        mt5.ORDER_TYPE_BUY = 0
        mt5.TRADE_ACTION_DEAL = 1
        mt5.ORDER_FILLING_FOK = 2

        result = MagicMock()
        result.retcode = 10009
        result.price = 1.1000
        mt5.order_send.return_value = result

        MT5Connector().place_order(self.order)
        self.order.refresh_from_db()
        self.assertEqual(self.order.status, "filled")
        self.assertEqual(str(self.order.price), "1.10000000")

    @patch("execution.connectors.mt5.mt5")
    def test_place_order_error(self, mt5):
        mt5.initialize.return_value = True
        mt5.login.return_value = True
        mt5.symbol_select.return_value = True
        mt5.TRADE_RETCODE_DONE = 10009
        mt5.ORDER_TYPE_BUY = 0
        mt5.TRADE_ACTION_DEAL = 1
        mt5.ORDER_FILLING_FOK = 2

        result = MagicMock()
        result.retcode = 10019  # some error code
        mt5.order_send.return_value = result

        from execution.connectors.base import ConnectorError
        with self.assertRaises(ConnectorError):
            MT5Connector().place_order(self.order)
        self.order.refresh_from_db()
        self.assertEqual(self.order.status, "error")
