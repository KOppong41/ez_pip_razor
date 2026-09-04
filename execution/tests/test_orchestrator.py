from django.test import TestCase
from django.urls import reverse
from bots.models import Asset, Bot
from brokers.models import BrokerAccount
from execution.models import Signal, Decision, Order
from django.contrib.auth import get_user_model

class OrchestratorTest(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_superuser("orch-admin", "orch@example.com", "pw")
        self.client.force_login(self.user)
        self.ba = BrokerAccount.objects.create(owner=self.user, name="Paper", broker="paper", connector="paper", account_ref="acc1")
        self.bot = Bot.objects.create(owner=self.user, name="BotA", status="active", broker_account=self.ba, asset=Asset.objects.create(symbol="ORCHEURUSD"))
        self.sig = Signal.objects.create(owner=self.user, bot=self.bot, source="test", symbol="EURUSD", timeframe="5m", direction="buy",
                                         payload={"x":1}, dedupe_key="k1")
        self.dec = Decision.objects.create(owner=self.user, bot=self.bot, signal=self.sig, action="open", reason="t", score=0.5, params={"sl": "1.0", "tp": "1.2"})

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
        # Immediate fills from new are valid. A terminal fill cannot move back to ack.
        self.client.post(f"/api/orders/{order_id}/transition/", data={"to_status": "filled"}, content_type="application/json")
        r = self.client.post(f"/api/orders/{order_id}/transition/", data={"to_status": "ack"}, content_type="application/json")
        self.assertEqual(r.status_code, 400)
