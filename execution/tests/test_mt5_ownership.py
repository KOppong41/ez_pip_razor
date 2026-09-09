from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import TestCase

from bots.models import Asset, Bot
from brokers.models import BrokerAccount
from execution.connectors.mt5 import MT5Connector
from execution.models import BrokerPosition, Order


class MT5OwnershipResolutionTests(TestCase):
    def setUp(self):
        # GitHub Actions runs on Linux without the Windows-only MetaTrader5
        # package. Keep every ownership test representative of that environment.
        self.mt5_package = patch("execution.connectors.mt5._mt5_module", None)
        self.mt5_package.start()
        self.addCleanup(self.mt5_package.stop)
        self.user = get_user_model().objects.create_user("mt5-owner-resolution", password="pw")
        self.account = BrokerAccount.objects.create(
            owner=self.user,
            name="Ownership account",
            broker="mt5",
            connector="mt5_local",
            account_ref="ownership-resolution",
            is_verified=True,
        )
        asset = Asset.objects.create(symbol="OWNUSD")
        self.bot = Bot.objects.create(
            owner=self.user,
            name="Ownership bot",
            status="active",
            broker_account=self.account,
            asset=asset,
        )
        self.entry = Order.objects.create(
            bot=self.bot,
            owner=self.user,
            broker_account=self.account,
            client_order_id="ownership-entry-client-order-id-123456789",
            intent="entry",
            symbol="OWNUSD",
            side="buy",
            qty=Decimal("0.01"),
            status="filled",
            broker_position_ticket=701,
        )
        self.connector = MT5Connector()

    @staticmethod
    def raw_position(*, ticket, magic, comment="", symbol="OWNUSD"):
        return SimpleNamespace(
            ticket=ticket,
            identifier=ticket,
            type=0,
            symbol=symbol,
            volume=0.01,
            price_open=1.1,
            price_current=1.11,
            sl=1.0,
            tp=1.2,
            profit=1,
            swap=0,
            magic=magic,
            comment=comment,
            time=0,
        )

    def test_per_bot_magic_maps_unknown_ticket_to_exact_bot(self):
        self.bot.refresh_from_db()
        raw = self.raw_position(ticket=702, magic=self.bot.mt5_magic_number)

        self.connector._sync_broker_exposure_snapshot(self.account, (raw,))

        position = BrokerPosition.objects.get(broker_position_ticket=702)
        self.assertEqual(position.ownership, "ez_trade")
        self.assertEqual(position.bot, self.bot)
        self.assertIsNone(position.originating_order)

    def test_ticket_preserves_legacy_global_magic_and_originating_order(self):
        raw = self.raw_position(
            ticket=701,
            magic=20250813,
            comment=self.connector._order_comment(self.entry),
        )

        self.connector._sync_broker_exposure_snapshot(self.account, (raw,))

        position = BrokerPosition.objects.get(broker_position_ticket=701)
        self.assertEqual(position.ownership, "ez_trade")
        self.assertEqual(position.bot, self.bot)
        self.assertEqual(position.originating_order, self.entry)

    def test_comment_without_ticket_or_known_magic_is_only_supplemental(self):
        raw = self.raw_position(
            ticket=703,
            magic=999999,
            comment=self.connector._order_comment(self.entry),
        )

        self.connector._sync_broker_exposure_snapshot(self.account, (raw,))

        position = BrokerPosition.objects.get(broker_position_ticket=703)
        self.assertEqual(position.ownership, "external")
        self.assertIsNone(position.bot)
        self.assertIsNone(position.originating_order)

    def test_same_symbol_is_not_ownership_evidence(self):
        raw = self.raw_position(ticket=704, magic=0, symbol=self.entry.symbol)

        self.connector._sync_broker_exposure_snapshot(self.account, (raw,))

        position = BrokerPosition.objects.get(broker_position_ticket=704)
        self.assertEqual(position.ownership, "manual")
        self.assertIsNone(position.bot)

    def test_partial_close_sync_does_not_replace_originating_entry(self):
        position = BrokerPosition.objects.create(
            broker_account=self.account,
            bot=self.bot,
            originating_order=self.entry,
            broker_position_ticket=705,
            ownership="ez_trade",
            symbol="OWNUSD",
            side="buy",
            volume=Decimal("0.02"),
            open_price=Decimal("1.1"),
        )
        close_order = Order.objects.create(
            bot=self.bot,
            owner=self.user,
            broker_account=self.account,
            client_order_id="ownership-partial-close",
            intent="exit",
            symbol="OWNUSD",
            side="sell",
            qty=Decimal("0.01"),
            status="part_filled",
            broker_position_ticket=705,
        )
        self.bot.refresh_from_db()
        raw = self.raw_position(ticket=705, magic=self.bot.mt5_magic_number)

        self.connector._sync_broker_position(close_order, raw, ownership="ez_trade")

        position.refresh_from_db()
        self.assertEqual(position.bot, self.bot)
        self.assertEqual(position.originating_order, self.entry)
