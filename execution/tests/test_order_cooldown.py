from django.test import TestCase, override_settings
from django.contrib.auth import get_user_model

from bots.models import Asset, Bot
from brokers.models import BrokerAccount
from execution.models import Signal, Decision, Order
from execution.services.fanout import fanout_orders


@override_settings(DECISION_ORDER_COOLDOWN_SEC=1)  # config cooldown lower than timeframe-derived 5m
class OrderCooldownTests(TestCase):
    def setUp(self):
        User = get_user_model()
        self.user = User.objects.create_user(username="cool", password="pw")
        self.acct = BrokerAccount.objects.create(
            name="Paper",
            broker="paper",
            connector="paper",
            account_ref="p1",
            owner=self.user,
        )
        self.asset, _ = Asset.objects.get_or_create(symbol="EURUSDm")
        self.bot = Bot.objects.create(
            name="Bot",
            owner=self.user,
            status="active",
            auto_trade=True,
            trading_schedule_enabled=False,
            broker_account=self.acct,
            asset=self.asset,
            allowed_symbols=["EURUSDm"],
        )
        self.signal = Signal.objects.create(
            bot=self.bot,
            source="test",
            symbol="EURUSDm",
            timeframe="5m",
            direction="buy",
            payload={},
            dedupe_key="cd-1",
        )

    def test_second_order_skipped_within_cooldown(self):
        decision = Decision.objects.create(
            bot=self.bot,
            signal=self.signal,
            action="open",
            reason="test",
            score=1.0,
            params={"sl": "1.0", "tp": "2.0"},
        )

        a = fanout_orders(decision, master_qty=None)
        self.assertEqual(len(a), 1)
        self.assertEqual(Order.objects.count(), 1)

        b = fanout_orders(decision, master_qty=None)
        self.assertEqual(len(b), 1)
        self.assertFalse(b[0][1])
        self.assertEqual(b[0][0].id, a[0][0].id)
        self.assertEqual(Order.objects.count(), 1)
