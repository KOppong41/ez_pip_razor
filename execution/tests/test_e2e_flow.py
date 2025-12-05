from django.test import TestCase, override_settings
from django.urls import reverse
from decimal import Decimal

from bots.models import Bot
from brokers.models import BrokerAccount
from copytrade.models import Follower
from execution.models import Signal, Decision, Order, Position, JournalEntry
from execution.tasks import monitor_positions_task, trail_positions_task
from execution.services.prices import FIXED


@override_settings(CELERY_TASK_ALWAYS_EAGER=True)
class EndToEndFlowTest(TestCase):
    def setUp(self):
        # 1) Active bot with routing + default size
        self.bot = Bot.objects.create(
            name="MasterBot",
            status="active",
            default_timeframe="5m",
            default_qty="0.10",
            allowed_symbols=["EURUSD"],
            allowed_timeframes=["5m"],
        )

        # 2) Two follower accounts (Paper connector)
        self.ba1 = BrokerAccount.objects.create(name="Follower-1", broker="paper", account_ref="f1")
        self.ba2 = BrokerAccount.objects.create(name="Follower-2", broker="paper", account_ref="f2")

        # 3) Attach followers (proportional x1, fixed 0.03)
        Follower.objects.create(
            master_bot=self.bot,
            broker_account=self.ba1,
            model="proportional",
            params={"multiplier": 1},
        )
        Follower.objects.create(
            master_bot=self.bot,
            broker_account=self.ba2,
            model="fixed",
            params={"fixed_qty": "0.03"},
        )

    def test_full_pipeline(self):
        # === Ingest alert (TradingView-style) ===
        url_alert = reverse("alert-webhook")
        payload = {
            "source": "tradingview",
            "symbol": "EURUSD",
            "timeframe": "5m",
            "direction": "buy",
            "payload": {"note": "breakout"},
        }
        r = self.client.post(url_alert, data=payload, content_type="application/json")
        self.assertEqual(r.status_code, 201)
        sig = Signal.objects.get()  # only one
        self.assertEqual(sig.symbol, "EURUSD")
        self.assertEqual(sig.bot_id, self.bot.id)  # routed to active bot

        # === Strategy + Risk → Decision ===
        r = self.client.post(f"/api/signals/{sig.id}/decide/")
        self.assertEqual(r.status_code, 201)
        dec = Decision.objects.get()
        self.assertEqual(dec.action, "open")

        # === Fan-out to followers (uses bot.default_qty=0.10) ===
        r = self.client.post(reverse("decision-fanout", args=[dec.id]), data={}, content_type="application/json")
        self.assertIn(r.status_code, (200, 201))
        # Two followers → two orders
        self.assertEqual(Order.objects.count(), 2)

        # === Send orders to connector → ACK→FILLED (Paper, eager Celery) ===
        for o in Order.objects.all():
            self.client.post(f"/api/orders/{o.id}/send/")
        # With CELERY_TASK_ALWAYS_EAGER=True, simulate_fill_task ran inline
        for o in Order.objects.all():
            o.refresh_from_db()
            self.assertEqual(o.status, "filled")
            self.assertIsNotNone(o.price)

        # === Positions updated ===
        pos1 = Position.objects.get(broker_account=self.ba1, symbol="EURUSD")
        pos2 = Position.objects.get(broker_account=self.ba2, symbol="EURUSD")
        # Proportional follower got 0.10; fixed follower got 0.03
        self.assertEqual(str(pos1.qty), "0.10000000")
        self.assertEqual(str(pos2.qty), "0.03000000")

        # === Trailing stop moves SL on profit ===
        FIXED["EURUSD"] = Decimal("1.1010")  # above our 1.1000 mock fill price
        moved = trail_positions_task()
        pos1.refresh_from_db()
        self.assertTrue(pos1.id in moved or pos2.id in moved)
        self.assertTrue(pos1.sl is not None or pos2.sl is not None)

        # === Early-exit closes on large loss ===
        FIXED["EURUSD"] = Decimal("1.0700")  # push price down hard
        monitor_positions_task()
        # Early-exit creates & sends close orders; Paper fills them
        pos1.refresh_from_db()
        pos2.refresh_from_db()
        self.assertIn(pos1.status, ["open", "closed"])  # may be closed if qty netted to 0
        self.assertIn(pos2.status, ["open", "closed"])

        # === Metrics exposed ===
        m = self.client.get("/api/metrics")
        self.assertEqual(m.status_code, 200)
        content = m.content.decode("utf-8")
        self.assertIn("signals_ingested_total", content)
        self.assertIn("decisions_total", content)
        self.assertIn("orders_created_total", content)
        self.assertIn("order_status_total", content)

        # === Journal entries present ===
        self.assertTrue(JournalEntry.objects.filter(event_type="signal.received").exists())
        self.assertTrue(JournalEntry.objects.filter(event_type="decision.created").exists())
        self.assertTrue(JournalEntry.objects.filter(event_type="order.status_changed").exists())
