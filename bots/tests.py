from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.test import TestCase

from bots.models import Asset, Bot


class BotSubscriptionLimitTest(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user(username="bot-limit-owner")
        self.asset_one = Asset.objects.create(symbol="LIMITONE")
        self.asset_two = Asset.objects.create(symbol="LIMITTWO")
        Bot.objects.bulk_create(
            [
                Bot(
                    name="Existing one",
                    owner=self.user,
                    bot_id="LIMIT00001",
                    status="active",
                    asset=self.asset_one,
                ),
                Bot(
                    name="Existing two",
                    owner=self.user,
                    bot_id="LIMIT00002",
                    status="active",
                    asset=self.asset_two,
                ),
            ]
        )

    def test_existing_bot_can_persist_engine_state_over_current_plan_limit(self):
        bot = Bot.objects.get(bot_id="LIMIT00001")
        bot.allocation_start_pnl = 0
        bot.save(update_fields=["allocation_start_pnl"])

        self.assertEqual(Bot.objects.get(pk=bot.pk).allocation_start_pnl, 0)

    def test_plan_limit_still_blocks_new_bot(self):
        with self.assertRaisesRegex(ValidationError, "Bot limit reached"):
            Bot.objects.create(
                name="New bot",
                owner=self.user,
                status="active",
                asset=Asset.objects.create(symbol="LIMITTHREE"),
            )
