from django.test import TestCase, override_settings
from django.urls import reverse
from execution.models import Signal
import hashlib, json

@override_settings(ALERT_WEBHOOK_SECRET=None, EXECUTION_ALERT_SECRET=None, ALERT_WEBHOOK_TOKEN=None)
class AlertWebhookTest(TestCase):
    def test_alert_webhook_creates_signal(self):
        url = reverse("alert-webhook")
        payload = {
            "source": "tradingview",
            "symbol": "EURUSD",
            "timeframe": "5m",
            "direction": "buy",
            "payload": {"entry": 1.2345, "bar": {"time": 9999999999999}}
        }
        r = self.client.post(url, data=payload, content_type="application/json")
        self.assertEqual(r.status_code, 410)
        self.assertEqual(Signal.objects.count(), 1)

    def test_alert_webhook_idempotency_same_payload(self):
        url = reverse("alert-webhook")
        payload = {
            "source": "tradingview",
            "symbol": "EURUSD",
            "timeframe": "5m",
            "direction": "buy",
            "payload": {"entry": 1.2345, "bar": {"time": 9999999999999}}
        }
        r1 = self.client.post(url, data=payload, content_type="application/json")
        r2 = self.client.post(url, data=payload, content_type="application/json")
        self.assertEqual(r1.status_code, 410)
        self.assertEqual(r2.status_code, 410)     # deduped by hash but endpoint disabled
        self.assertEqual(Signal.objects.count(), 1)

    def test_alert_webhook_dedupe_key_override(self):
        url = reverse("alert-webhook")
        payload = {
            "source": "tradingview",
            "symbol": "EURUSD",
            "timeframe": "5m",
            "direction": "buy",
            "payload": {"entry": 1.2345, "bar": {"time": 9999999999999}},
            "dedupe_key": "fixed-123"
        }
        self.client.post(url, data=payload, content_type="application/json")
        r = self.client.post(url, data=payload, content_type="application/json")
        self.assertEqual(r.status_code, 410)
        self.assertEqual(Signal.objects.count(), 1)
