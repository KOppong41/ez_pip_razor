from decimal import Decimal

from django.contrib.auth import get_user_model
from django.test import TestCase

from bots.models import Asset, Bot
from brokers.models import BrokerAccount
from execution.models import BrokerPosition, Position
from execution.services.decision import (
    count_open_positions_for_bot,
    detect_position_conflict,
)


class LivePositionAuthorityTests(TestCase):
    def setUp(self):
        user = get_user_model().objects.create_user("position-authority", password="pw")
        self.account = BrokerAccount.objects.create(
            owner=user,
            name="Authority MT5",
            broker="mt5",
            connector="mt5_local",
            account_ref="position-authority",
        )
        asset, _ = Asset.objects.get_or_create(symbol="AUTHORITYUSD")
        self.bot = Bot.objects.create(
            owner=user,
            name="Authority bot",
            broker_account=self.account,
            asset=asset,
            status="active",
        )

    def test_legacy_live_ledger_is_ignored_and_broker_ticket_state_is_counted(self):
        Position.objects.create(
            broker_account=self.account,
            symbol="EURUSD",
            qty=Decimal("1"),
            avg_price=Decimal("1.1"),
        )
        self.assertEqual(count_open_positions_for_bot(self.bot, "EURUSD"), 0)

        BrokerPosition.objects.create(
            broker_account=self.account,
            bot=self.bot,
            broker_position_ticket=4455,
            ownership="ez_trade",
            symbol="EURUSD",
            side="buy",
            volume=Decimal("0.01"),
            open_price=Decimal("1.1"),
        )
        self.assertEqual(count_open_positions_for_bot(self.bot, "EURUSD"), 1)
        conflict = detect_position_conflict(self.bot, "EURUSD", "buy", 1.0)
        self.assertEqual(conflict.action, "ignore")
        self.assertEqual(conflict.reason, "existing_position_same_direction")
