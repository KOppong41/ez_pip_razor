from django.contrib.auth import get_user_model
from django.test import TestCase

from bots.models import Asset, Bot
from brokers.models import BrokerAccount
from execution.models import RiskPolicy


class RuntimeControlApiTest(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user(
            "runtime-user", password="pass"
        )
        self.account = BrokerAccount.objects.create(
            owner=self.user,
            name="Runtime MT5",
            broker="mt5",
            connector="mt5_local",
            account_ref="runtime-account",
        )
        self.policy = RiskPolicy.objects.create(
            broker_account=self.account, entries_enabled=True
        )
        self.asset = Asset.objects.create(
            symbol="RUNTIMEUSD", display_name="Runtime asset"
        )
        self.bot = Bot.objects.create(
            owner=self.user,
            broker_account=self.account,
            asset=self.asset,
            name="Runtime bot",
            status="active",
        )
        self.client.force_login(self.user)

    def test_logout_does_not_stop_the_bot_but_app_close_token_does(self):
        session = self.client.post("/api/personal/runtime/session/")
        self.assertEqual(session.status_code, 200)
        stop_token = session.json()["stop_token"]

        self.client.logout()
        self.bot.refresh_from_db()
        self.policy.refresh_from_db()
        self.assertEqual(self.bot.status, "active")
        self.assertTrue(self.policy.entries_enabled)

        stopped = self.client.post(
            "/api/personal/runtime/stop/",
            data={"stop_token": stop_token},
            content_type="application/json",
        )
        self.assertEqual(stopped.status_code, 200)
        self.bot.refresh_from_db()
        self.policy.refresh_from_db()
        self.assertEqual(self.bot.status, "stopped")
        self.assertFalse(self.policy.entries_enabled)

    def test_invalid_close_token_cannot_stop_a_bot(self):
        response = self.client.post(
            "/api/personal/runtime/stop/",
            data={"stop_token": "invalid"},
            content_type="application/json",
        )
        self.assertEqual(response.status_code, 400)
        self.bot.refresh_from_db()
        self.assertEqual(self.bot.status, "active")
