from datetime import timedelta
from io import StringIO

from django.contrib.auth import get_user_model
from django.core.management import call_command
from django.test import TestCase
from django.utils import timezone

from bots.models import Asset, Bot
from brokers.models import BrokerAccount
from execution.models import Order
from execution.services.brokers import dispatch_place_order


class CancelStuckOrdersTests(TestCase):
    def setUp(self):
        owner = get_user_model().objects.create_user("stuck-owner", password="pw")
        account = BrokerAccount.objects.create(
            owner=owner, name="Paper", broker="paper", connector="paper",
            account_ref="stuck-paper",
        )
        bot = Bot.objects.create(
            owner=owner, name="Stuck bot", status="active", broker_account=account,
            asset=Asset.objects.create(symbol="STUCKEURUSD"),
        )

        def order(suffix, status="new", **fields):
            return Order.objects.create(
                owner=owner, bot=bot, broker_account=account,
                client_order_id=f"stuck-{suffix}", symbol="EURUSD", side="buy",
                qty="0.01", status=status, **fields,
            )

        self.safe = order("safe")
        self.queued = order("queued", execution_queued_at=timezone.now())
        self.ack = order("ack", status="ack", submitted_at=timezone.now())
        Order.objects.filter(pk__in=[self.safe.pk, self.queued.pk, self.ack.pk]).update(
            created_at=timezone.now() - timedelta(minutes=10)
        )

    def test_dry_run_and_apply_leave_uncertain_orders_untouched(self):
        output = StringIO()
        call_command("cancel_stuck_orders", stdout=output)
        self.assertIn("Dry run: 1", output.getvalue())
        self.safe.refresh_from_db()
        self.assertEqual(self.safe.status, "new")

        call_command("cancel_stuck_orders", apply=True, stdout=StringIO())
        self.safe.refresh_from_db()
        self.queued.refresh_from_db()
        self.ack.refresh_from_db()
        self.assertEqual(self.safe.status, "canceled")
        self.assertEqual(self.queued.status, "new")
        self.assertEqual(self.ack.status, "ack")

    def test_canceled_order_cannot_be_dispatched(self):
        Order.objects.filter(pk=self.safe.pk).update(status="canceled")
        with self.assertRaisesRegex(ValueError, "no longer dispatchable"):
            dispatch_place_order(self.safe)
