from datetime import datetime, timezone

from django.contrib.auth import get_user_model
from django.test import TestCase

from bots.models import Asset, Bot
from brokers.models import BrokerAccount
from execution.models import JournalEntry


class PersonalLogsApiTests(TestCase):
    def setUp(self):
        user_model = get_user_model()
        self.owner = user_model.objects.create_user("journal-owner", password="pw")
        other = user_model.objects.create_user("other-journal-owner", password="pw")
        account = BrokerAccount.objects.create(
            owner=self.owner,
            name="Journal paper",
            broker="paper",
            connector="paper",
            account_ref="journal-paper",
        )
        asset = Asset.objects.create(symbol="JOURNALETH")
        self.eth_bot = Bot.objects.create(
            owner=self.owner, name="ETH bot", status="active",
            broker_account=account, asset=asset,
        )
        old = JournalEntry.objects.create(
            owner=self.owner, bot=self.eth_bot, symbol="ETHUSDm",
            event_type="older_event", message="Older ETH event",
        )
        newer = JournalEntry.objects.create(
            owner=self.owner, bot=self.eth_bot, symbol="XAUUSDm",
            event_type="newer_event", message="Newer gold event",
        )
        JournalEntry.objects.filter(pk=old.pk).update(
            created_at=datetime(2026, 8, 26, 12, tzinfo=timezone.utc)
        )
        JournalEntry.objects.filter(pk=newer.pk).update(
            created_at=datetime(2026, 8, 27, 12, tzinfo=timezone.utc)
        )
        JournalEntry.objects.create(
            owner=other, symbol="PRIVATE", event_type="private_event",
        )
        self.client.force_login(self.owner)

    def test_filters_and_options_are_scoped_to_owner(self):
        response = self.client.get("/api/personal/logs/", {"include_options": "1"})
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertEqual(len(data["rows"]), 2)
        self.assertTrue({"ETHUSDm", "XAUUSDm", "JOURNALETH"}.issubset(data["options"]["assets"]))
        self.assertEqual(
            {item["bot_id"] for item in data["options"]["bots"]},
            {self.eth_bot.id},
        )

        response = self.client.get("/api/personal/logs/", {
            "include_options": "1",
            "bot_id": self.eth_bot.id,
            "symbol": "ETHUSDm",
            "from": "2026-08-26T00:00:00Z",
            "to": "2026-08-27T00:00:00Z",
        })
        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            [row["event_type"] for row in response.json()["rows"]],
            ["older_event"],
        )
        self.assertEqual(len(response.json()["options"]["bots"]), 1)

        response = self.client.get("/api/personal/logs/", {
            "from": "2026-08-27T00:00:00Z",
            "to": "2026-08-28T00:00:00Z",
        })
        self.assertEqual([row["event_type"] for row in response.json()], ["newer_event"])

    def test_rejects_invalid_filter_values(self):
        for parameters in ({"bot_id": "invalid"}, {"from": "bad"}, {"to": "2026-08-27"}):
            with self.subTest(parameters=parameters):
                self.assertEqual(
                    self.client.get("/api/personal/logs/", parameters).status_code,
                    400,
                )

    def test_options_still_list_configured_bots_and_assets_without_events(self):
        JournalEntry.objects.all().delete()
        response = self.client.get("/api/personal/logs/", {"include_options": "1"})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["rows"], [])
        self.assertEqual(
            response.json()["options"]["bots"],
            [{"bot_id": self.eth_bot.id, "bot__name": "ETH bot"}],
        )
        self.assertIn("JOURNALETH", response.json()["options"]["assets"])
