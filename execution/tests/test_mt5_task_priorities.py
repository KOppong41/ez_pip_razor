from unittest.mock import patch

from django.test import TestCase

from bots.models import Bot
from brokers.models import BrokerAccount
from execution.models import Order
from execution.mt5_tasks import enqueue_mt5_order, execute_mt5_order_task
from execution.task_priorities import (
    MT5_PRIORITY_EMERGENCY,
    MT5_PRIORITY_HIGH,
    MT5_PRIORITY_NORMAL,
)


class MT5TaskPriorityTests(TestCase):
    def setUp(self):
        account = BrokerAccount.objects.create(
            name="Priority MT5",
            broker="mt5",
            connector="mt5_local",
            account_ref="priority-mt5",
        )
        bot = Bot.objects.create(name="Priority bot", status="active")
        self.entry = Order.objects.create(
            bot=bot,
            broker_account=account,
            client_order_id="priority-entry",
            intent="entry",
            symbol="EURUSD",
            side="buy",
            qty="0.01",
        )
        self.exit = Order.objects.create(
            bot=bot,
            broker_account=account,
            client_order_id="priority-exit",
            intent="exit",
            symbol="EURUSD",
            side="sell",
            qty="0.01",
        )

    @patch("execution.mt5_tasks.execute_mt5_order_task.apply_async")
    def test_entry_uses_normal_priority_and_records_queue_time(self, apply_async):
        enqueue_mt5_order(self.entry)

        apply_async.assert_called_once_with(
            args=[self.entry.id],
            queue="mt5_execution",
            priority=MT5_PRIORITY_NORMAL,
        )
        self.entry.refresh_from_db()
        self.assertIsNotNone(self.entry.execution_queued_at)

    @patch("execution.mt5_tasks.execute_mt5_order_task.apply_async")
    def test_exit_uses_high_priority(self, apply_async):
        enqueue_mt5_order(self.exit)

        self.assertEqual(
            apply_async.call_args.kwargs["priority"],
            MT5_PRIORITY_HIGH,
        )

    @patch("execution.mt5_tasks.execute_mt5_order_task.apply_async")
    def test_emergency_exit_uses_emergency_priority(self, apply_async):
        enqueue_mt5_order(self.exit, emergency=True)

        self.assertEqual(
            apply_async.call_args.kwargs["priority"],
            MT5_PRIORITY_EMERGENCY,
        )

    @patch("execution.mt5_tasks.dispatch_place_order")
    def test_worker_records_start_time_before_dispatch(self, dispatch):
        execute_mt5_order_task.run(self.entry.id)

        dispatch.assert_called_once()
        self.entry.refresh_from_db()
        self.assertIsNotNone(self.entry.mt5_worker_started_at)
