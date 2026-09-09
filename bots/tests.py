from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.test import TestCase

from bots.models import Asset, Bot
from brokers.models import BrokerAccount
from execution.models import RiskPolicy


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


class BotRiskScopeValidationTest(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user(username="bot-risk-owner")
        self.account = BrokerAccount.objects.create(
            owner=self.user,
            name="Risk account",
            broker="mt5",
            account_ref="bot-risk-account",
        )
        self.asset = Asset.objects.create(symbol="RISKVALIDATION")
        RiskPolicy.objects.create(
            broker_account=self.account,
            max_order_lot_size="0.10",
            max_total_open_positions=2,
            max_aggregate_open_lots="0.20",
        )

    def test_bot_configuration_cannot_exceed_account_hard_limits(self):
        with self.assertRaises(ValidationError) as caught:
            Bot.objects.create(
                owner=self.user,
                name="Too permissive",
                asset=self.asset,
                broker_account=self.account,
                max_bot_lot_size="0.20",
                risk_max_concurrent_positions=3,
            )

        self.assertIn("max_bot_lot_size", caught.exception.message_dict)
        with self.assertRaises(ValidationError) as caught:
            Bot.objects.create(
                owner=self.user,
                name="Too many positions",
                asset=self.asset,
                broker_account=self.account,
                max_bot_lot_size="0.10",
                risk_max_concurrent_positions=3,
            )
        self.assertIn("risk_max_concurrent_positions", caught.exception.message_dict)

    def test_fixed_default_lot_must_fit_bot_max_and_magic_is_stable(self):
        bot = Bot.objects.create(
            owner=self.user,
            name="Fixed",
            asset=self.asset,
            broker_account=self.account,
            position_sizing_mode="fixed",
            default_qty="0.05",
            max_bot_lot_size="0.05",
            risk_max_concurrent_positions=1,
        )
        magic = bot.mt5_magic_number
        self.assertEqual(magic, 500_000_000 + bot.pk)

        bot.name = "Fixed renamed"
        bot.save(update_fields=["name"])
        bot.refresh_from_db()
        self.assertEqual(bot.mt5_magic_number, magic)
