from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from django.test import SimpleTestCase

from telegrambot.views import _queue_or_dispatch_order


class TelegramDispatchRoutingTests(SimpleTestCase):
    @patch("execution.mt5_tasks.enqueue_mt5_order")
    @patch("telegrambot.views.dispatch_place_order")
    def test_mt5_order_is_queued_on_dedicated_worker(self, dispatch, enqueue):
        account = MagicMock()
        account.requires_mt5_connector.return_value = True
        order = SimpleNamespace(broker_account=account)

        _queue_or_dispatch_order(order)

        enqueue.assert_called_once_with(order)
        dispatch.assert_not_called()

    @patch("execution.mt5_tasks.enqueue_mt5_order")
    @patch("telegrambot.views.dispatch_place_order")
    def test_non_mt5_order_remains_synchronous(self, dispatch, enqueue):
        account = MagicMock()
        account.requires_mt5_connector.return_value = False
        order = SimpleNamespace(broker_account=account)

        _queue_or_dispatch_order(order)

        dispatch.assert_called_once_with(order)
        enqueue.assert_not_called()
