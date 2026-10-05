from django.test import TestCase
from django.urls import reverse
from decimal import Decimal
from bots.models import Asset, Bot
from brokers.models import BrokerAccount
from execution.models import Signal, Decision, Order, Execution, Position, RiskPolicy
from time import sleep
from unittest.mock import patch
from django.contrib.auth import get_user_model

class PaperConnectorFlowTest(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_superuser("paper-admin", "paper@example.com", "pw")
        self.client.force_login(self.user)
        self.ba = BrokerAccount.objects.create(owner=self.user, name="Paper", broker="paper", connector="paper", account_ref="p1")
        self.bot = Bot.objects.create(owner=self.user, name="BotP", status="active", broker_account=self.ba, asset=Asset.objects.create(symbol="PAPEREURUSD"))
        self.sig = Signal.objects.create(owner=self.user, bot=self.bot, source="test", symbol="EURUSD", timeframe="5m",
                                         direction="buy", payload={}, dedupe_key="dedupe-xyz")
        self.dec = Decision.objects.create(owner=self.user, bot=self.bot, signal=self.sig, action="open", reason="ok", score=0.1, params={"sl": "1.0", "tp": "1.2"})
        # create order
        r = self.client.post("/api/orders/from-decision/", data={
            "decision_id": self.dec.id, "broker_account_id": self.ba.id, "qty": "0.05"
        }, content_type="application/json")
        self.order_id = r.json()["id"]

    @patch("execution.connectors.paper.current_app.send_task")
    def test_send_and_fill(self, send_task):
        # send to connector -> should ACK then fill via async task
        self.client.post(f"/api/orders/{self.order_id}/send/")
        send_task.assert_called_once_with(
            "execution.tasks.simulate_fill_task",
            args=[self.order_id],
        )
        # Run task synchronously by calling it directly (no need to sleep if using eager)
        from execution.tasks import simulate_fill_task
        simulate_fill_task(self.order_id)

        r = self.client.get(f"/api/orders/?id={self.order_id}")
        order = Order.objects.get(id=self.order_id)
        self.assertEqual(order.status, "filled")
        self.assertEqual(str(order.price), "1.10000000")
        self.assertEqual(Execution.objects.filter(order=order).count(), 1)

        pos = Position.objects.get(broker_account=self.ba, symbol="EURUSD")
        self.assertEqual(str(pos.qty), "0.05000000")
        self.assertEqual(str(pos.avg_price), "1.10000000")

    def test_cancel(self):
        self.client.post(f"/api/orders/{self.order_id}/cancel/")
        order = Order.objects.get(id=self.order_id)
        self.assertEqual(order.status, "canceled")

    @patch("execution.connectors.paper.current_app.send_task")
    def test_emergency_stop_rejects_paper_entry_before_simulation(self, send_task):
        RiskPolicy.objects.update_or_create(
            broker_account=self.ba, defaults={"emergency_stop": True}
        )
        from execution.connectors.paper import PaperConnector

        order = Order.objects.get(pk=self.order_id)
        with self.assertRaisesRegex(ValueError, "ACCOUNT_EMERGENCY_STOP_ACTIVE"):
            PaperConnector().place_order(order)
        order.refresh_from_db()
        self.assertEqual(order.status, "rejected")
        send_task.assert_not_called()

    @patch("execution.connectors.paper.current_app.send_task")
    def test_pending_entry_reserves_paper_daily_slot(self, send_task):
        self.bot.max_trades_per_day = 1
        self.bot.save(update_fields=["max_trades_per_day"])
        Order.objects.create(
            owner=self.user, bot=self.bot, broker_account=self.ba,
            client_order_id="earlier-pending-entry", symbol="EURUSD",
            side="buy", qty=Decimal("0.01"), intent="entry", status="ack",
        )
        from execution.connectors.paper import PaperConnector

        order = Order.objects.get(pk=self.order_id)
        with self.assertRaisesRegex(ValueError, "BOT_DAILY_TRADE_LIMIT"):
            PaperConnector().place_order(order)
        order.refresh_from_db()
        self.assertEqual(order.status, "rejected")
        send_task.assert_not_called()
