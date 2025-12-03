from django.test import TestCase
from django.urls import reverse
from decimal import Decimal
from bots.models import Bot
from brokers.models import BrokerAccount
from execution.models import Signal, Decision, Order, Execution, Position
from time import sleep

class PaperConnectorFlowTest(TestCase):
    def setUp(self):
        self.bot = Bot.objects.create(name="BotP", status="active")
        self.ba = BrokerAccount.objects.create(name="Paper", broker="paper", account_ref="p1")
        self.sig = Signal.objects.create(source="test", symbol="EURUSD", timeframe="5m",
                                         direction="buy", payload={}, dedupe_key="dedupe-xyz")
        self.dec = Decision.objects.create(bot=self.bot, signal=self.sig, action="open", reason="ok", score=0.1, params={})
        # create order
        r = self.client.post("/api/orders/from-decision/", data={
            "decision_id": self.dec.id, "broker_account_id": self.ba.id, "qty": "0.05"
        }, content_type="application/json")
        self.order_id = r.json()["id"]

    def test_send_and_fill(self):
        # send to connector -> should ACK then fill via async task
        self.client.post(f"/api/orders/{self.order_id}/send/")
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
