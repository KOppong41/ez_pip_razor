from django.test import TestCase
from django.urls import reverse
from bots.models import Bot
from brokers.models import BrokerAccount
from execution.models import Order, Signal

class AutoTradeWebhookTest(TestCase):
    def setUp(self):
        self.bot = Bot.objects.create(
            name="Auto", status="active", default_qty="0.10",
            allowed_symbols=["EURUSD"], allowed_timeframes=["5m"], auto_trade=True
        )
        BrokerAccount.objects.create(name="Paper1", broker="paper", account_ref="p1", connector="paper")

    def test_pipeline_runs(self):
        r = self.client.post(reverse("alert-webhook"), data={
            "source":"tradingview","symbol":"EURUSD","timeframe":"5m","direction":"buy","payload":{},"dedupe_key":"tv-1"
        }, content_type="application/json")
        self.assertEqual(r.status_code, 410)
        self.assertEqual(Signal.objects.count(), 0)
        self.assertEqual(Order.objects.count(), 0)
