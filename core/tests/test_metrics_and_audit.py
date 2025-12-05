from django.test import TestCase
from django.urls import reverse
from bots.models import Bot

class MetricsAuditTest(TestCase):
    def test_metrics_endpoint_works(self):
        r = self.client.get("/api/metrics")
        self.assertEqual(r.status_code, 200)
        self.assertIn(b"# HELP", r.content)
        self.assertIn(b"open_positions", r.content)
