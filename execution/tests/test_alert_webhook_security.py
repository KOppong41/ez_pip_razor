import hashlib
import hmac
import json
import time

from django.test import TestCase, override_settings
from django.urls import reverse

from execution.models import Signal


class AlertWebhookSignatureTests(TestCase):
    def _payload(self):
        return {
            "source": "tradingview",
            "symbol": "EURUSD",
            "timeframe": "5m",
            "direction": "buy",
            "payload": {"bar": {"time": int(time.time() * 1000)}},
        }

    def _sign(self, secret, body: str):
        digest = hmac.new(secret.encode("utf-8"), body.encode("utf-8"), hashlib.sha256).hexdigest()
        return f"sha256={digest}"

    @override_settings(ALERT_WEBHOOK_SECRET="topsecret", ALERT_WEBHOOK_TOKEN=None)
    def test_valid_signature_allows_processing(self):
        url = reverse("alert-webhook")
        body = json.dumps(self._payload(), sort_keys=True, separators=(",", ":"))
        sig = self._sign("topsecret", body)

        r = self.client.post(
            url,
            data=body,
            content_type="application/json",
            HTTP_X_ALERT_SIGNATURE=sig,
        )
        self.assertEqual(r.status_code, 410)  # still disabled externally but signature accepted
        self.assertEqual(Signal.objects.count(), 1)

    @override_settings(ALERT_WEBHOOK_SECRET="topsecret", ALERT_WEBHOOK_TOKEN=None)
    def test_invalid_signature_is_rejected(self):
        url = reverse("alert-webhook")
        body = json.dumps(self._payload(), sort_keys=True, separators=(",", ":"))

        r = self.client.post(
            url,
            data=body,
            content_type="application/json",
            HTTP_X_ALERT_SIGNATURE="sha256=deadbeef",
        )
        self.assertEqual(r.status_code, 403)
        self.assertEqual(Signal.objects.count(), 0)
