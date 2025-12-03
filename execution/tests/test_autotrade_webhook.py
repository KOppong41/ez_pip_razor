from django.test import TestCase
from django.urls import reverse
from bots.models import Bot
from brokers.models import BrokerAccount
from copytrade.models import Follower

class AutoTradeWebhookTest(TestCase):
    def setUp(self):
        self.bot = Bot.objects.create(
            name="Auto", status="active", default_qty="0.10",
            allowed_symbols=["EURUSD"], allowed_timeframes=["5m"], auto_trade=True
        )
        ba = BrokerAccount.objects.create(name="Paper1", broker="paper", account_ref="p1")
        Follower.objects.create(master_bot=self.bot, broker_account=ba, model="proportional", params={"multiplier":1})

    def test_pipeline_runs(self):
        r = self.client.post(reverse("alert-webhook"), data={
            "source":"tradingview","symbol":"EURUSD","timeframe":"5m","direction":"buy","payload":{},"dedupe_key":"tv-1"
        }, content_type="application/json")
        self.assertIn(r.status_code, (200, 201))
        body = r.json()
        self.assertTrue(body["auto_trade"])
        self.assertGreaterEqual(body["orders_sent"], 1)
