from django.contrib.auth import get_user_model
from django.test import TestCase

from bots.models import Asset, Bot
from brokers.models import BrokerAccount


class ClientBotApiTest(TestCase):
    def setUp(self):
        User = get_user_model()
        self.user = User.objects.create_user("alice", password="pass")
        self.other = User.objects.create_user("bob", password="pass")
        self.asset, _ = Asset.objects.update_or_create(
            symbol="EURUSDm",
            defaults={
                "display_name": "EUR/USD",
                "min_qty": "0.01",
                "recommended_qty": "0.02",
                "is_active": True,
            },
        )
        self.account = BrokerAccount.objects.create(
            owner=self.user,
            name="Alice MT5",
            broker="mt5",
            account_ref="alice-1",
            mt5_login="10001",
            is_active=True,
        )
        self.other_account = BrokerAccount.objects.create(
            owner=self.other,
            name="Bob MT5",
            broker="mt5",
            account_ref="bob-1",
            mt5_login="20001",
            is_active=True,
        )
        self.other_bot = Bot.objects.create(
            owner=self.other,
            name="Bob Bot",
            asset=self.asset,
            broker_account=self.other_account,
            default_qty="0.01",
        )
        self.client.force_login(self.user)

    def test_client_can_create_bot_and_owner_is_forced(self):
        response = self.client.post(
            "/api/bots/",
            data={
                "name": "Alice Bot",
                "asset": self.asset.id,
                "broker_account": self.account.id,
                "engine_mode": "harami",
                "default_timeframe": "5m",
                "default_qty": "0.02",
                "auto_trade": True,
                "status": "active",
            },
            content_type="application/json",
        )
        self.assertEqual(response.status_code, 201, response.json())
        bot = Bot.objects.get(name="Alice Bot")
        self.assertEqual(bot.owner, self.user)
        self.assertEqual(bot.status, "stopped")

    def test_client_cannot_see_or_control_another_users_bot(self):
        response = self.client.get("/api/bots/")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), [])
        response = self.client.post(
            f"/api/bots/{self.other_bot.id}/control/",
            data={"action": "stop"},
            content_type="application/json",
        )
        self.assertEqual(response.status_code, 404)

    def test_client_cannot_assign_another_users_account(self):
        response = self.client.post(
            "/api/bots/",
            data={
                "name": "Cross Tenant Bot",
                "asset": self.asset.id,
                "broker_account": self.other_account.id,
                "default_qty": "0.02",
            },
            content_type="application/json",
        )
        self.assertEqual(response.status_code, 400)
        self.assertIn("broker_account", response.json())

    def test_options_only_include_the_clients_accounts(self):
        response = self.client.get("/api/bots/options/")
        self.assertEqual(response.status_code, 200)
        account_ids = {row["id"] for row in response.json()["accounts"]}
        self.assertEqual(account_ids, {self.account.id})
        asset_ids = {row["id"] for row in response.json()["assets"]}
        self.assertIn(self.asset.id, asset_ids)
