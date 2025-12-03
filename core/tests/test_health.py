from django.test import TestCase
from django.urls import reverse
from core.models import WorkerHeartbeat

class HealthTest(TestCase):
    def test_health_ok(self):
        WorkerHeartbeat.objects.create(name="default")
        url = reverse("health")
        res = self.client.get(url)
        self.assertEqual(res.status_code, 200)
        self.assertEqual(res.json().get("status"), "ok")
        self.assertTrue(res.json().get("db"))
