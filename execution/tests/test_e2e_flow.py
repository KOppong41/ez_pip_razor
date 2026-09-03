from django.test import TestCase

from bots.models import Bot
from brokers.models import BrokerAccount
from execution.models import Decision, Execution, JournalEntry, Order, Signal
from execution.services.decision import make_decision_from_signal
from execution.services.fanout import fanout_orders
from execution.tasks import simulate_fill_task


class InternalEngineEndToEndTest(TestCase):
    def setUp(self):
        self.account = BrokerAccount.objects.create(
            name="Paper",
            broker="paper",
            connector="paper",
            account_ref="internal-e2e",
        )
        self.bot = Bot.objects.create(
            name="Internal engine",
            status="active",
            trading_schedule_enabled=False,
            broker_account=self.account,
            default_qty="0.10",
            allowed_symbols=["EURUSD"],
            allowed_timeframes=["5m"],
        )

    def test_signal_decision_order_execution_mapping(self):
        signal = Signal.objects.create(
            bot=self.bot,
            source="engine_v1",
            symbol="EURUSD",
            timeframe="5m",
            direction="buy",
            payload={"score": 1.0, "sl": "1.0900", "tp": "1.1200"},
            dedupe_key="internal-candle-e2e",
        )
        decision = make_decision_from_signal(signal)
        self.assertEqual(decision.action, "open")

        orders = fanout_orders(decision, master_qty=None)
        self.assertEqual(len(orders), 1)
        order = orders[0][0]
        self.assertEqual(order.decision_id, decision.id)
        self.assertEqual(order.intent, "entry")

        simulate_fill_task(order.id)
        order.refresh_from_db()
        self.assertEqual(order.status, "filled")
        self.assertEqual(Execution.objects.filter(order=order).count(), 1)
        self.assertTrue(JournalEntry.objects.filter(order=order).exists())

        # Reprocessing the exact signal and decision is idempotent.
        self.assertEqual(make_decision_from_signal(signal).id, decision.id)
        replay = fanout_orders(decision, master_qty=None)
        self.assertEqual(replay, [])
        self.assertEqual(Order.objects.count(), 1)
