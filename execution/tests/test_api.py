from django.test import TestCase
from django.urls import reverse
from bots.models import Bot
from brokers.models import BrokerAccount

class TradingAPITest(TestCase):
    def setUp(self):
        self.bot = Bot.objects.create(name="BotA", status="active")
        self.ba  = BrokerAccount.objects.create(
            name="Exness Demo", broker="exness_mt5", account_ref="123456", creds={"ref":"local"}
        )

    def test_signal_create_and_list(self):
        url = "/api/signals/"
        payload = {
            "source": "test",
            "symbol": "EURUSD",
            "timeframe": "5m",
            "direction": "buy",
            "payload": {"k":"v"},
            "dedupe_key": "abc-123",
        }
        r = self.client.post(url, data=payload, content_type="application/json")
        self.assertEqual(r.status_code, 201)
        r = self.client.get(url)
        self.assertEqual(r.status_code, 200)
        self.assertGreaterEqual(len(r.json()), 1)

    def test_order_quick_create(self):
        url = "/api/orders/quick-create/"
        payload = {
            "bot_id": self.bot.id,
            "broker_account_id": self.ba.id,
            "symbol": "EURUSD",
            "side": "buy",
            "qty": "0.10"
        }
        r = self.client.post(url, data=payload, content_type="application/json")
        self.assertEqual(r.status_code, 201)
        data = r.json()
        self.assertEqual(data["symbol"], "EURUSD")
        self.assertEqual(data["side"], "buy")
