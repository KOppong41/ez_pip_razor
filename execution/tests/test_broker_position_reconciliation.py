from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import Mock

from django.test import TestCase
from django.contrib.auth import get_user_model

from bots.models import Asset, Bot
from brokers.models import BrokerAccount
from execution.models import BrokerPosition, Execution, Order, Position
from execution.tasks import _reconcile_missing_owned_position


class BrokerPositionReconciliationTests(TestCase):
    def test_imports_broker_stop_exit_and_flattens_local_position(self):
        user = get_user_model().objects.create_user("reconcile-owner", password="pw")
        account = BrokerAccount.objects.create(
            owner=user,
            name="MT5 demo",
            broker="mt5",
            connector="mt5_local",
            account_ref="reconcile-demo",
            is_verified=True,
        )
        asset, _ = Asset.objects.get_or_create(symbol="XAUUSDm")
        bot = Bot.objects.create(
            owner=user,
            name="Gold bot",
            status="active",
            auto_trade=True,
            broker_account=account,
            asset=asset,
        )
        entry = Order.objects.create(
            owner=user,
            bot=bot,
            broker_account=account,
            client_order_id="entry-reconcile-test",
            symbol="XAUUSDm",
            side="buy",
            qty=Decimal("0.01"),
            filled_qty=Decimal("0.01"),
            remaining_qty=Decimal("0"),
            intent="entry",
            status="filled",
            broker_position_ticket=333,
        )
        broker_position = BrokerPosition.objects.create(
            broker_account=account,
            bot=bot,
            originating_order=entry,
            broker_position_ticket=333,
            ownership="ez_trade",
            symbol="XAUUSDm",
            side="buy",
            volume=Decimal("0.01"),
            open_price=Decimal("4617.206"),
            status="open",
        )
        connector = Mock()
        connector.history_deals_for_position_account.return_value = (
            SimpleNamespace(
                ticket=444,
                order=445,
                position_id=333,
                entry=1,
                volume=0.01,
                price=4615.326,
                profit=-1.88,
                commission=0,
                swap=0,
                time=1,
                time_msc=1000,
            ),
        )

        imported = _reconcile_missing_owned_position(connector, broker_position)

        self.assertEqual(imported, [444])
        broker_position.refresh_from_db()
        self.assertEqual(broker_position.status, "closed")
        self.assertEqual(broker_position.volume, Decimal("0"))
        self.assertFalse(Position.objects.filter(broker_account=account).exists())
        close_order = Order.objects.get(intent="exit", broker_position_ticket=333)
        self.assertEqual(close_order.status, "filled")
        self.assertEqual(close_order.broker_deal_ticket, 444)
        self.assertTrue(
            Execution.objects.filter(
                order=close_order,
                broker_deal_ticket=444,
                profit=Decimal("-1.88"),
            ).exists()
        )
