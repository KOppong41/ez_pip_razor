from decimal import Decimal

from django.contrib.auth import get_user_model
from django.test import TestCase

from bots.models import Asset, Bot
from brokers.models import BrokerAccount
from execution.models import Execution, Order, Position
from execution.services.reconcile import reconcile_orders_and_positions


class ReconcileIdempotencyTests(TestCase):
    def test_filled_paper_order_is_recorded_once(self):
        owner = get_user_model().objects.create_user("reconcile-owner", password="pw")
        account = BrokerAccount.objects.create(
            owner=owner, name="Paper", broker="paper", connector="paper",
            account_ref="reconcile-paper",
        )
        bot = Bot.objects.create(
            owner=owner, name="Reconcile bot", status="active",
            broker_account=account, asset=Asset.objects.create(symbol="RECONCILEUSD"),
        )
        order = Order.objects.create(
            owner=owner, bot=bot, broker_account=account,
            client_order_id="reconcile-entry", symbol="RECONCILEUSD",
            side="buy", intent="entry", qty=Decimal("0.1"),
            price=Decimal("100"), status="filled",
        )

        first = reconcile_orders_and_positions(apply=True)
        second = reconcile_orders_and_positions(apply=True)

        self.assertEqual(first["executions_created"], 1)
        self.assertEqual(second["executions_created"], 0)
        self.assertEqual(Execution.objects.filter(order=order).count(), 1)
        self.assertEqual(Position.objects.get(broker_account=account, symbol="RECONCILEUSD").qty, Decimal("0.1"))
