from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.test import TestCase

from bots.models import Asset, Bot
from brokers.models import BrokerAccount
from execution.models import Decision, Order, Signal
from execution.services.orchestrator import create_order_from_decision


class ExecutionOwnershipApiTests(TestCase):
    def setUp(self):
        user_model = get_user_model()
        self.alice = user_model.objects.create_user("owner-alice", password="pw")
        self.bob = user_model.objects.create_user("owner-bob", password="pw")
        ops, _ = Group.objects.get_or_create(name="Ops")
        ops.user_set.add(self.alice, self.bob)
        self.alice_account = BrokerAccount.objects.create(
            owner=self.alice,
            name="Alice paper",
            broker="paper",
            connector="paper",
            account_ref="alice-paper",
        )
        self.bob_account = BrokerAccount.objects.create(
            owner=self.bob,
            name="Bob paper",
            broker="paper",
            connector="paper",
            account_ref="bob-paper",
        )
        asset = Asset.objects.create(symbol="OWNEREURUSD")
        self.alice_bot = Bot.objects.create(
            owner=self.alice,
            name="Alice bot",
            status="active",
            broker_account=self.alice_account,
            asset=asset,
        )
        self.bob_bot = Bot.objects.create(
            owner=self.bob,
            name="Bob bot",
            status="active",
            broker_account=self.bob_account,
            asset=asset,
        )
        self.alice_signal = Signal.objects.create(
            owner=self.alice,
            bot=self.alice_bot,
            source="test",
            symbol="EURUSD",
            timeframe="5m",
            direction="buy",
            payload={},
            dedupe_key="alice-signal",
        )
        self.alice_decision = Decision.objects.create(
            owner=self.alice,
            bot=self.alice_bot,
            signal=self.alice_signal,
            action="open",
            params={"sl": "1.09", "tp": "1.12"},
        )
        self.client.force_login(self.alice)

    def test_from_decision_rejects_another_users_account(self):
        response = self.client.post(
            "/api/orders/from-decision/",
            data={
                "decision_id": self.alice_decision.id,
                "broker_account_id": self.bob_account.id,
                "qty": "0.01",
            },
            content_type="application/json",
        )
        self.assertEqual(response.status_code, 400)
        self.assertEqual(Order.objects.count(), 0)

    def test_service_layer_rejects_cross_account_creation(self):
        with self.assertRaisesRegex(ValueError, "not configured for this broker account"):
            create_order_from_decision(
                self.alice_decision,
                self.bob_account,
                "0.01",
            )
        self.assertEqual(Order.objects.count(), 0)

    def test_quick_create_rejects_another_users_bot(self):
        response = self.client.post(
            "/api/orders/quick-create/",
            data={
                "bot_id": self.bob_bot.id,
                "broker_account_id": self.alice_account.id,
                "symbol": "EURUSD",
                "side": "buy",
                "qty": "0.01",
            },
            content_type="application/json",
        )
        self.assertEqual(response.status_code, 400)
        self.assertEqual(Order.objects.count(), 0)

    def test_signal_owner_is_derived_from_authenticated_user(self):
        response = self.client.post(
            "/api/signals/",
            data={
                "owner": self.bob.id,
                "bot": self.alice_bot.id,
                "source": "test",
                "symbol": "EURUSD",
                "timeframe": "5m",
                "direction": "buy",
                "payload": {},
                "dedupe_key": "owner-derived",
            },
            content_type="application/json",
        )
        self.assertEqual(response.status_code, 201)
        self.assertEqual(Signal.objects.get(dedupe_key="owner-derived").owner, self.alice)

    def test_order_detail_action_hides_another_users_order(self):
        foreign_order = Order.objects.create(
            owner=self.bob,
            bot=self.bob_bot,
            broker_account=self.bob_account,
            client_order_id="bob-order",
            symbol="EURUSD",
            side="buy",
            qty="0.01",
        )
        response = self.client.post(f"/api/orders/{foreign_order.id}/send/")
        self.assertEqual(response.status_code, 404)
