from django.test import TestCase
from django.urls import reverse
from bots.models import Bot
from execution.models import Signal

class SignalRoutingTest(TestCase):
    def setUp(self):
        self.bot = Bot.objects.create(
            name="Router", status="active", default_timeframe="5m",
            default_qty="0.12", allowed_symbols=["EURUSD"], allowed_timeframes=["5m"]
        )

    def test_alert_binds_to_active_bot(self):
        url = reverse("alert-webhook")
        payload = {"source":"tv","symbol":"EURUSD","timeframe":"5m","direction":"buy","payload":{}}
        r = self.client.post(url, data=payload, content_type="application/json")
        self.assertEqual(r.status_code, 201)
        sig = Signal.objects.first()
        self.assertIsNotNone(sig.bot)
        self.assertEqual(sig.bot_id, self.bot.id)

    def test_no_active_bot_means_null(self):
        self.bot.status = "stopped"; self.bot.save()
        url = reverse("alert-webhook")
        payload = {"source":"tv","symbol":"EURUSD","timeframe":"5m","direction":"buy","payload":{}}
        r = self.client.post(url, data=payload, content_type="application/json")
        self.assertEqual(r.status_code, 201)
        sig = Signal.objects.first()
        self.assertIsNone(sig.bot)
