from django.test import TestCase
from django.urls import reverse
from bots.models import Bot
from brokers.models import BrokerAccount
from execution.models import Signal, Decision, Order

class OrchestratorTest(TestCase):
    def setUp(self):
        self.bot = Bot.objects.create(name="BotA", status="active")
        self.ba = BrokerAccount.objects.create(name="Paper", broker="paper", account_ref="acc1")
        self.sig = Signal.objects.create(source="test", symbol="EURUSD", timeframe="5m", direction="buy",
                                         payload={"x":1}, dedupe_key="k1")
        self.dec = Decision.objects.create(bot=self.bot, signal=self.sig, action="open", reason="t", score=0.5, params={})

    def test_create_order_from_decision_idempotent(self):
        url = "/api/orders/from-decision/"
        payload = {"decision_id": self.dec.id, "broker_account_id": self.ba.id, "qty": "0.10"}
        r1 = self.client.post(url, data=payload, content_type="application/json")
        r2 = self.client.post(url, data=payload, content_type="application/json")
        self.assertEqual(r1.status_code, 201)
        self.assertEqual(r2.status_code, 200)  # same client_order_id hit
        self.assertEqual(Order.objects.count(), 1)

    def test_order_transitions(self):
        # create
        url_create = "/api/orders/from-decision/"
        payload = {"decision_id": self.dec.id, "broker_account_id": self.ba.id, "qty": "0.10"}
        r = self.client.post(url_create, data=payload, content_type="application/json")
        order_id = r.json()["id"]

        # new -> ack
        r = self.client.post(f"/api/orders/{order_id}/transition/", data={"to_status": "ack"}, content_type="application/json")
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.json()["status"], "ack")

        # ack -> filled (with price)
        r = self.client.post(f"/api/orders/{order_id}/transition/", data={"to_status": "filled", "price": "1.1111"}, content_type="application/json")
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.json()["status"], "filled")
        self.assertEqual(r.json()["price"], "1.11110000")

    def test_invalid_transition_rejected(self):
        # create
        r = self.client.post("/api/orders/from-decision/", data={"decision_id": self.dec.id, "broker_account_id": self.ba.id, "qty": "0.10"}, content_type="application/json")
        order_id = r.json()["id"]
        # new -> filled is invalid in our simple graph (must ack first)
        r = self.client.post(f"/api/orders/{order_id}/transition/", data={"to_status": "filled"}, content_type="application/json")
        self.assertEqual(r.status_code, 400)
