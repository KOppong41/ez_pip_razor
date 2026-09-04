from django.test import TestCase
from django.urls import reverse
from decimal import Decimal
from bots.models import Asset, Bot
from brokers.models import BrokerAccount
from execution.models import Signal, Decision, Order, Execution, Position
from time import sleep
from unittest.mock import patch
from django.contrib.auth import get_user_model

class PaperConnectorFlowTest(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_superuser("paper-admin", "paper@example.com", "pw")
        self.client.force_login(self.user)
        self.ba = BrokerAccount.objects.create(owner=self.user, name="Paper", broker="paper", connector="paper", account_ref="p1")
        self.bot = Bot.objects.create(owner=self.user, name="BotP", status="active", broker_account=self.ba, asset=Asset.objects.create(symbol="PAPEREURUSD"))
        self.sig = Signal.objects.create(owner=self.user, bot=self.bot, source="test", symbol="EURUSD", timeframe="5m",
                                         direction="buy", payload={}, dedupe_key="dedupe-xyz")
        self.dec = Decision.objects.create(owner=self.user, bot=self.bot, signal=self.sig, action="open", reason="ok", score=0.1, params={"sl": "1.0", "tp": "1.2"})
        # create order
        r = self.client.post("/api/orders/from-decision/", data={
            "decision_id": self.dec.id, "broker_account_id": self.ba.id, "qty": "0.05"
        }, content_type="application/json")
        self.order_id = r.json()["id"]

    @patch("execution.connectors.paper.current_app.send_task")
    def test_send_and_fill(self, send_task):
        # send to connector -> should ACK then fill via async task
        self.client.post(f"/api/orders/{self.order_id}/send/")
        send_task.assert_called_once_with(
            "execution.tasks.simulate_fill_task",
            args=[self.order_id],
        )
        # Run task synchronously by calling it directly (no need to sleep if using eager)
        from execution.tasks import simulate_fill_task
        simulate_fill_task(self.order_id)

        r = self.client.get(f"/api/orders/?id={self.order_id}")
        order = Order.objects.get(id=self.order_id)
        self.assertEqual(order.status, "filled")
        self.assertEqual(str(order.price), "1.10000000")
        self.assertEqual(Execution.objects.filter(order=order).count(), 1)

        pos = Position.objects.get(broker_account=self.ba, symbol="EURUSD")
        self.assertEqual(str(pos.qty), "0.05000000")
        self.assertEqual(str(pos.avg_price), "1.10000000")

    def test_cancel(self):
        self.client.post(f"/api/orders/{self.order_id}/cancel/")
        order = Order.objects.get(id=self.order_id)
        self.assertEqual(order.status, "canceled")
