from django.test import TestCase
from bots.models import Bot
from brokers.models import BrokerAccount
from execution.models import Signal, Decision, Order
from copytrade.models import Follower

class FanoutTest(TestCase):
    def setUp(self):
        self.bot = Bot.objects.create(name="Master", status="active")
        self.ba1 = BrokerAccount.objects.create(name="F1", broker="paper", account_ref="a1")
        self.ba2 = BrokerAccount.objects.create(name="F2", broker="paper", account_ref="a2")
        self.sig = Signal.objects.create(source="test", symbol="EURUSD", timeframe="5m", direction="buy",
                                         payload={}, dedupe_key="d1")
        self.dec = Decision.objects.create(bot=self.bot, signal=self.sig, action="open", reason="ok", score=0.1, params={})
        Follower.objects.create(master_bot=self.bot, broker_account=self.ba1, model="proportional", params={"multiplier": 1})
        Follower.objects.create(master_bot=self.bot, broker_account=self.ba2, model="fixed", params={"fixed_qty": "0.02"})

    def test_fanout_creates_orders(self):
        r = self.client.post(f"/api/decisions/{self.dec.id}/fanout/", data={"qty": "0.05"}, content_type="application/json")
        self.assertIn(r.status_code, (200, 201))
        # Two followers -> two orders
        self.assertEqual(Order.objects.count(), 2)
