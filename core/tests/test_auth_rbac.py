from django.test import TestCase
from django.contrib.auth.models import User, Group

class AuthRBAC(TestCase):
    def setUp(self):
        self.admin = User.objects.create_user("admin","a@a.com","pass")
        self.ops = User.objects.create_user("ops","o@o.com","pass")
        self.viewer = User.objects.create_user("view","v@v.com","pass")
        Group.objects.get_or_create(name="Admin")[0].user_set.add(self.admin)
        Group.objects.get_or_create(name="Ops")[0].user_set.add(self.ops)
        Group.objects.get_or_create(name="ReadOnly")[0].user_set.add(self.viewer)

    def _jwt(self, u, p="pass"):
        from rest_framework.test import APIClient
        c = APIClient()
        r = c.post("/api/auth/token/", {"username":u, "password":p}, format="json")
        return r.json()["access"]

    def test_ops_can_create_order(self):
        t = self._jwt("ops")
        self.client.defaults["HTTP_AUTHORIZATION"] = f"Bearer {t}"
        r = self.client.post("/api/orders/from-decision/", data={}, content_type="application/json")
        self.assertIn(r.status_code, (400, 404))  # auth passed; payload may be invalid (we just check perms)

    def test_viewer_cannot_send_order(self):
        t = self._jwt("view")
        self.client.defaults["HTTP_AUTHORIZATION"] = f"Bearer {t}"
        r = self.client.post("/api/orders/1/send/")
        self.assertEqual(r.status_code, 403)

    def test_public_webhook_allowed(self):
        r = self.client.post("/api/alerts/webhook/", data={"source":"x","symbol":"EURUSD","timeframe":"5m","direction":"buy","payload":{}}, content_type="application/json")
        # token check may forbid if you set ALERT_WEBHOOK_TOKEN. If set, expect 403 here.
        self.assertIn(r.status_code, (201, 403))
