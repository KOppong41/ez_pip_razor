from django.test import TestCase
from bots.models import Bot, Asset

class BotControlsTest(TestCase):
    def setUp(self):
        self.asset = Asset.objects.create(symbol="EURUSDm", display_name="EUR/USD", min_qty="0.10", recommended_qty="0.10")
        self.bot = Bot.objects.create(name="Ctl", status="stopped", default_qty="0.10", asset=self.asset)

    def test_start_pause_stop(self):
        r = self.client.post(f"/api/bots/{self.bot.id}/control/", data={"action":"start"})
        self.bot.refresh_from_db()
        self.assertEqual(self.bot.status, "active")
        r = self.client.post(f"/api/bots/{self.bot.id}/control/", data={"action":"pause"})
        self.bot.refresh_from_db()
        self.assertEqual(self.bot.status, "paused")
        r = self.client.post(f"/api/bots/{self.bot.id}/control/", data={"action":"stop"})
        self.bot.refresh_from_db()
        self.assertEqual(self.bot.status, "stopped")

    def test_update_defaults(self):
        gold = Asset.objects.create(symbol="XAUUSDm", display_name="Gold", min_qty="0.01", recommended_qty="0.01")
        r = self.client.patch(
            f"/api/bots/{self.bot.id}/settings/",
            data={"default_qty": "0.25", "asset": gold.id},
            content_type="application/json",
        )
        self.bot.refresh_from_db()
        self.assertEqual(str(self.bot.default_qty), "0.25000000")
        self.assertEqual(self.bot.asset_id, gold.id)
