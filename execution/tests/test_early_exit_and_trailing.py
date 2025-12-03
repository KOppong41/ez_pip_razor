from django.test import TestCase
from decimal import Decimal
from bots.models import Bot
from brokers.models import BrokerAccount
from execution.models import Signal, Decision, Order, Position
from execution.tasks import monitor_positions_task, trail_positions_task
from execution.services.prices import FIXED

class EarlyExitAndTrailingTest(TestCase):
    def setUp(self):
        self.bot = Bot.objects.create(name="B", status="active")
        self.ba = BrokerAccount.objects.create(name="Paper", broker="paper", account_ref="p1")
        # Long 0.10 @ 1.1000
        self.pos = Position.objects.create(broker_account=self.ba, symbol="EURUSD", qty=Decimal("0.10"), avg_price=Decimal("1.1000"), status="open")

    def test_early_exit_triggers_on_loss(self):
        # Push price below avg enough to exceed 2% notional loss:
        # 2% of notional= 2% * 0.10 * 1.1000 = 0.0022 -> need price drop >= 0.022 (this is big); adjust FIXED to simulate.
        FIXED["EURUSD"] = Decimal("1.0700")  # ~3% drop
        monitor_positions_task()
        # An order should be created to close (Paper connector fills immediately in our flow after send)
        self.assertTrue(Order.objects.exists())

    def test_trailing_updates_sl_on_profit(self):
        # Set price above avg to trigger trailing
        FIXED["EURUSD"] = Decimal("1.1010")
        moved_ids = trail_positions_task()
        self.pos.refresh_from_db()
        self.assertIn(self.pos.id, moved_ids)
        self.assertIsNotNone(self.pos.sl)
