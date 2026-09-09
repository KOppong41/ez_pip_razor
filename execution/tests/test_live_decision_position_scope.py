from decimal import Decimal

from django.contrib.auth import get_user_model
from django.test import TestCase

from bots.models import Asset, Bot
from brokers.models import BrokerAccount
from execution.models import BrokerPosition
from execution.services.decision import (
    count_open_positions_for_bot,
    count_total_open_positions_for_bot,
    detect_position_conflict,
)


class LiveDecisionPositionScopeTests(TestCase):
    def setUp(self):
        owner = get_user_model().objects.create_user("position-scope")
        self.account = BrokerAccount.objects.create(
            owner=owner,
            name="MT5",
            broker="mt5",
            connector="mt5_local",
            account_ref="position-scope",
        )
        asset = Asset.objects.create(symbol="SCOPEUSD")
        self.bot = Bot.objects.create(
            owner=owner,
            name="Current bot",
            broker_account=self.account,
            asset=asset,
            status="active",
        )
        self.other_bot = Bot(
            owner=owner,
            name="Other bot",
            broker_account=self.account,
            asset=asset,
            status="stopped",
        )
        # The free test plan permits one bot; bulk insertion keeps this unit
        # test focused on persisted cross-bot exposure scoping.
        Bot.objects.bulk_create([self.other_bot])

    def _position(self, ticket, *, bot=None, ownership="ez_trade"):
        return BrokerPosition.objects.create(
            broker_account=self.account,
            bot=bot,
            broker_position_ticket=ticket,
            ownership=ownership,
            symbol="EURUSD",
            side="buy",
            volume=Decimal("0.1"),
            open_price=Decimal("1.1"),
        )

    def test_manual_external_and_other_bot_positions_do_not_block_current_bot(self):
        self._position(1, ownership="manual")
        self._position(2, ownership="external")
        self._position(3, bot=self.other_bot)

        self.assertEqual(count_total_open_positions_for_bot(self.bot), 0)
        self.assertEqual(count_open_positions_for_bot(self.bot, "EURUSD"), 0)
        self.assertIsNone(detect_position_conflict(self.bot, "EURUSD", "buy", 1.0))

        self._position(4, bot=self.bot)
        self.assertEqual(count_total_open_positions_for_bot(self.bot), 1)
        self.assertEqual(count_open_positions_for_bot(self.bot, "EURUSD"), 1)
        self.assertEqual(
            detect_position_conflict(self.bot, "EURUSD", "buy", 1.0).reason,
            "existing_position_same_direction",
        )
