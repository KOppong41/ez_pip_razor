from django.contrib.auth import get_user_model
from django.test import TestCase

from bots.models import Asset, Bot
from brokers.models import BrokerAccount
from execution.models import MT5ConnectionState


class BotControlsTest(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user(
            username="client",
            password="pass",
        )
        self.client.force_login(self.user)
        self.account = BrokerAccount.objects.create(
            owner=self.user,
            name="Client MT5",
            broker="mt5",
            account_ref="10001",
            mt5_login="10001",
            is_active=True,
        )
        MT5ConnectionState.objects.create(
            broker_account=self.account,
            connected=True,
            account_mode="demo",
        )
        self.asset, _ = Asset.objects.update_or_create(
            symbol="EURUSDm",
            defaults={
                "display_name": "EUR/USD",
                "min_qty": "0.10",
                "recommended_qty": "0.10",
                "is_active": True,
            },
        )
        self.bot = Bot.objects.create(
            owner=self.user,
            name="Ctl",
            status="stopped",
            default_qty="0.10",
            asset=self.asset,
            broker_account=self.account,
        )

    def test_start_pause_stop(self):
        for action, expected in (
            ("start", "active"),
            ("pause", "paused"),
            ("stop", "stopped"),
        ):
            response = self.client.post(
                f"/api/bots/{self.bot.id}/control/",
                data={"action": action},
                content_type="application/json",
            )
            self.assertEqual(response.status_code, 200)
            self.bot.refresh_from_db()
            self.assertEqual(self.bot.status, expected)

    def test_update_defaults(self):
        gold, _ = Asset.objects.update_or_create(
            symbol="XAUUSDm",
            defaults={
                "display_name": "Gold",
                "min_qty": "0.01",
                "recommended_qty": "0.01",
                "is_active": True,
            },
        )
        response = self.client.patch(
            f"/api/bots/{self.bot.id}/settings/",
            data={"default_qty": "0.25", "asset": gold.id},
            content_type="application/json",
        )
        self.assertEqual(response.status_code, 200)
        self.bot.refresh_from_db()
        self.assertEqual(str(self.bot.default_qty), "0.25000000")
        self.assertEqual(self.bot.asset_id, gold.id)
