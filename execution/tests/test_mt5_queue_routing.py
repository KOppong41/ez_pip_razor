from unittest.mock import patch

from celery import current_app
from django.contrib.auth import get_user_model
from django.test import TestCase

from bots.models import Bot
from brokers.models import BrokerAccount
from execution.models import Order


class MT5QueueRoutingTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_superuser(
            "queue-admin",
            "queue@example.com",
            "pw",
        )
        self.client.force_login(self.user)
        self.bot = Bot.objects.create(name="Queue bot", status="active")

    def _order(self, account, suffix):
        return Order.objects.create(
            bot=self.bot,
            broker_account=account,
            client_order_id=f"queue-{suffix}",
            symbol="EURUSD",
            side="buy",
            qty="0.10",
        )

    @patch("execution.mt5_tasks.cancel_mt5_order_task.apply_async")
    def test_mt5_cancel_is_queued_on_serial_execution_worker(self, apply_async):
        apply_async.return_value.id = "cancel-task-id"
        account = BrokerAccount.objects.create(
            name="MT5",
            broker="mt5",
            connector="mt5_local",
            account_ref="queue-mt5",
        )
        order = self._order(account, "mt5")

        response = self.client.post(f"/api/orders/{order.id}/cancel/")

        self.assertEqual(response.status_code, 202)
        self.assertEqual(response.json()["task_id"], "cancel-task-id")
        apply_async.assert_called_once_with(
            args=[order.id],
            queue="mt5_execution",
        )
        order.refresh_from_db()
        self.assertEqual(order.status, "new")

    def test_cancel_task_is_registered_with_celery(self):
        import execution.tasks  # noqa: F401

        self.assertIn(
            "execution.mt5_tasks.cancel_mt5_order_task",
            current_app.tasks,
        )

    @patch("execution.views.dispatch_cancel_order")
    def test_non_mt5_cancel_remains_synchronous(self, dispatch_cancel):
        account = BrokerAccount.objects.create(
            name="Paper",
            broker="paper",
            connector="paper",
            account_ref="queue-paper",
        )
        order = self._order(account, "paper")

        response = self.client.post(f"/api/orders/{order.id}/cancel/")

        self.assertEqual(response.status_code, 200)
        dispatch_cancel.assert_called_once()
