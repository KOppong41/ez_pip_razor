from django.test import TestCase
from django.urls import reverse
from bots.models import Bot

class MetricsAuditTest(TestCase):
    def test_metrics_endpoint_works(self):
        r = self.client.get("/api/metrics")
        self.assertEqual(r.status_code, 200)
        self.assertIn(b"# HELP", r.content)
        self.assertIn(b"open_positions", r.content)

    def test_audit_writes_on_bot_control(self):
        b = Bot.objects.create(name="M", status="stopped")
        r = self.client.post(f"/api/bots/{b.id}/control/", data={"action":"start"})
        self.assertEqual(r.status_code, 200)
        # check audit exists
        from core.models import Audit
        self.assertTrue(Audit.objects.filter(action="bot.control", entity="Bot", entity_id=str(b.id)).exists())
