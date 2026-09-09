from decimal import Decimal

from django.contrib.auth import get_user_model
from django.test import TestCase

from bots.models import Asset, Bot
from brokers.models import BrokerAccount
from execution.models import BrokerPosition, Execution, ExecutionAttempt, Order
from execution.services.orchestrator import create_close_order_for_position
from execution.tasks import _tp1_order_completed


class CloseOrderRetryTests(TestCase):
    def setUp(self):
        user = get_user_model().objects.create_user("close-retry", password="pw")
        self.account = BrokerAccount.objects.create(
            owner=user,
            name="MT5 demo",
            broker="mt5",
            connector="mt5_local",
            account_ref="close-retry",
            is_verified=True,
        )
        asset = Asset.objects.create(symbol="RETRYEURUSD")
        self.bot = Bot.objects.create(
            owner=user,
            name="Retry bot",
            broker_account=self.account,
            asset=asset,
        )
        entry = Order.objects.create(
            owner=user,
            bot=self.bot,
            broker_account=self.account,
            client_order_id="retry-entry",
            symbol="RETRYEURUSD",
            side="buy",
            qty=Decimal("0.10"),
            filled_qty=Decimal("0.10"),
            intent="entry",
            status="filled",
            broker_position_ticket=1234,
        )
        self.position = BrokerPosition.objects.create(
            broker_account=self.account,
            bot=self.bot,
            originating_order=entry,
            broker_position_ticket=1234,
            ownership="ez_trade",
            symbol="RETRYEURUSD",
            side="buy",
            volume=Decimal("0.10"),
            open_price=Decimal("1.1"),
        )

    def test_definitive_zero_fill_rejection_gets_new_attempt_identity(self):
        first, created = create_close_order_for_position(self.position, self.account)
        self.assertTrue(created)
        first.status = "rejected"
        first.save(update_fields=["status"])

        retry, created = create_close_order_for_position(self.position, self.account)
        same_retry, created_again = create_close_order_for_position(
            self.position,
            self.account,
        )

        self.assertTrue(created)
        self.assertFalse(created_again)
        self.assertNotEqual(retry.pk, first.pk)
        self.assertEqual(same_retry.pk, retry.pk)
        self.assertTrue(retry.client_order_id.endswith("|retry:1"))

    def test_ambiguous_rejection_keeps_original_identity(self):
        first, _ = create_close_order_for_position(self.position, self.account)
        first.status = "error"
        first.save(update_fields=["status"])
        ExecutionAttempt.objects.create(
            order=first,
            attempt_no=1,
            status="ambiguous",
            requested_qty=first.qty,
            remaining_qty=first.qty,
        )

        existing, created = create_close_order_for_position(
            self.position,
            self.account,
        )

        self.assertFalse(created)
        self.assertEqual(existing.pk, first.pk)
        self.assertEqual(Order.objects.filter(intent="exit").count(), 1)

    def test_broker_confirmed_cancellation_can_be_retried(self):
        first, _ = create_close_order_for_position(self.position, self.account)
        first.status = "canceled"
        first.save(update_fields=["status"])
        ExecutionAttempt.objects.create(
            order=first,
            attempt_no=1,
            status="accepted",
            requested_qty=first.qty,
            remaining_qty=first.qty,
        )

        retry, created = create_close_order_for_position(
            self.position,
            self.account,
        )

        self.assertTrue(created)
        self.assertNotEqual(retry.pk, first.pk)
        self.assertTrue(retry.client_order_id.endswith("|retry:1"))

    def test_tp1_requires_terminal_status_and_actual_execution(self):
        tp1, _ = create_close_order_for_position(
            self.position,
            self.account,
            close_qty=Decimal("0.05"),
            stage="tp1",
        )
        tp1.status = "filled"
        tp1.filled_qty = tp1.qty
        tp1.remaining_qty = Decimal("0")
        tp1.save(update_fields=["status", "filled_qty", "remaining_qty"])
        self.assertFalse(_tp1_order_completed(tp1))

        Execution.objects.create(
            order=tp1,
            qty=tp1.qty,
            price=Decimal("1.101"),
            broker_deal_ticket=9001,
            broker_position_ticket=self.position.broker_position_ticket,
        )

        self.assertTrue(_tp1_order_completed(tp1))
