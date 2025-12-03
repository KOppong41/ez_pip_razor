from django.test import TestCase
from execution.models import Signal, Position, Decision
from django.urls import reverse
from brokers.models import BrokerAccount
from django.contrib.auth import get_user_model

class DecisionPipelineTest(TestCase):
    def setUp(self):
        User = get_user_model()
        self.user = User.objects.create_superuser(username="u", password="pw", email="u@example.com")
        self.client.force_login(self.user)
        self.acct = BrokerAccount.objects.create(name="Paper", broker="paper", account_ref="p1", owner=self.user)
        self.sig = Signal.objects.create(
            source="test", symbol="EURUSD", timeframe="5m", direction="buy",
            payload={"k":"v"}, dedupe_key="d1"
        )

    def test_decision_open_when_no_risk_breach(self):
        url = f"/api/signals/{self.sig.id}/decide/"
        r = self.client.post(url)
        self.assertEqual(r.status_code, 201)
        body = r.json()
        self.assertEqual(body["action"], "open")
        self.assertIn("naive", body["reason"])
        self.assertEqual(Decision.objects.count(), 1)

    def test_decision_ignore_when_symbol_at_capacity(self):
        # Create an open position for that symbol to trigger risk max_positions_per_symbol=1
        Position.objects.create(broker_account=self.acct, symbol="EURUSD", qty=1, avg_price=1.0, status="open")
        url = f"/api/signals/{self.sig.id}/decide/"
        r = self.client.post(url)
        self.assertEqual(r.status_code, 201)
        body = r.json()
        self.assertEqual(body["action"], "ignore")
        self.assertIn("max_positions_per_symbol", body["reason"])
