from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.utils import timezone

from bots.models import Asset, Bot
from brokers.models import BrokerAccount
from execution.models import Decision, ExecutionAttempt, Order, Signal
from execution.services.fanout import fanout_orders
from execution.tasks import _dispatch_scalper_candidate


class ScalperDispatchGuardTests(TestCase):
    def setUp(self):
        owner = get_user_model().objects.create_user("dispatch-guard", password="pw")
        account = BrokerAccount.objects.create(
            owner=owner,
            name="MT5 guard account",
            broker="mt5",
            connector="mt5_local",
            account_ref="dispatch-guard",
            is_active=True,
        )
        asset, _ = Asset.objects.get_or_create(symbol="ETHUSDm")
        self.bot = Bot.objects.create(
            owner=owner,
            name="Guard bot",
            status="active",
            auto_trade=True,
            broker_account=account,
            asset=asset,
            trading_schedule_enabled=False,
        )

    def decision(self, suffix, sl):
        signal = Signal.objects.create(
            owner=self.bot.owner,
            bot=self.bot,
            source="scalper_engine",
            symbol="ETHUSDm",
            timeframe="1m",
            direction="buy",
            payload={"spread_price": "1", "close": "100"},
            dedupe_key=f"dispatch-guard-{suffix}",
        )
        return Decision.objects.create(
            owner=self.bot.owner,
            bot=self.bot,
            signal=signal,
            action="open",
            reason="test",
            score=0.9,
            params={"entry": "100", "sl": sl, "tp": "105"},
        )

    @patch("execution.tasks._queue_or_dispatch_order")
    @patch("execution.tasks.get_broker_symbol_constraints")
    def test_guard_rejection_is_terminal_and_does_not_block_next_decision(
        self, constraints, queue
    ):
        constraints.return_value = SimpleNamespace(
            point=Decimal("0.01"), stops_level_points=Decimal("0")
        )

        rejected = _dispatch_scalper_candidate(self.decision("narrow", "99.5"), "momentum_ignition")

        first = Order.objects.get()
        self.assertEqual(rejected["rejection_reason"], "guard:sl_below_spread")
        self.assertEqual(first.status, "rejected")
        self.assertIn("guard:sl_below_spread", first.last_error)
        self.assertIsNone(first.execution_queued_at)
        queue.assert_not_called()

        accepted = _dispatch_scalper_candidate(self.decision("wide", "97.5"), "momentum_ignition")

        self.assertEqual(len(accepted["orders"]), 1)
        self.assertEqual(Order.objects.count(), 2)
        queue.assert_called_once()

    @patch("execution.tasks._queue_or_dispatch_order")
    def test_existing_queued_order_is_never_rejected_or_queued_twice(self, queue):
        decision = self.decision("queued", "99.5")
        order, _ = fanout_orders(decision, master_qty=None)[0]
        order.execution_queued_at = timezone.now()
        order.save(update_fields=["execution_queued_at"])

        result = _dispatch_scalper_candidate(decision, "momentum_ignition")

        order.refresh_from_db()
        self.assertEqual(result["rejection_reason"], "order_not_dispatchable")
        self.assertEqual(order.status, "new")
        self.assertEqual(Order.objects.count(), 1)
        queue.assert_not_called()

    @patch("execution.tasks._queue_or_dispatch_order")
    def test_ambiguous_submission_is_never_rejected_or_resubmitted(self, queue):
        decision = self.decision("ambiguous", "99.5")
        order, _ = fanout_orders(decision, master_qty=None)[0]
        ExecutionAttempt.objects.create(
            order=order,
            attempt_no=1,
            status="ambiguous",
            requested_qty=order.qty,
        )

        result = _dispatch_scalper_candidate(decision, "momentum_ignition")

        order.refresh_from_db()
        self.assertEqual(result["rejection_reason"], "order_not_dispatchable")
        self.assertEqual(order.status, "new")
        queue.assert_not_called()
