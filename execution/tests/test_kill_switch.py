from types import SimpleNamespace
from unittest.mock import patch

from django.test import TestCase, override_settings
from django.contrib.auth import get_user_model
from django.utils import timezone

from bots.models import Asset, Bot
from brokers.models import BrokerAccount
from execution.models import AccountRiskDay, BrokerPosition, Order, RiskPolicy
from execution.tasks import _cancel_outstanding_entry_orders, kill_switch_monitor_task


@override_settings(ACCOUNT_RISK_OPENING_SNAPSHOT_GRACE_SECONDS=-1)
class KillSwitchRiskDayTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user("kill-risk-owner", password="pw")
        self.account = BrokerAccount.objects.create(
            owner=self.user,
            name="Kill-switch account",
            broker="mt5",
            connector="mt5_local",
            account_ref="kill-switch-risk-day",
            is_verified=True,
        )
        asset, _ = Asset.objects.get_or_create(symbol="KILLRISKUSD")
        self.bot = Bot.objects.create(
            owner=self.user,
            name="Kill risk bot",
            broker_account=self.account,
            asset=asset,
            status="active",
        )
        self.policy = RiskPolicy.objects.create(
            broker_account=self.account,
            entries_enabled=True,
            max_daily_loss_pct=1,
            max_account_drawdown_pct=100,
        )
        self.account_info = SimpleNamespace(
            trade_mode=2,
            balance=9850,
            equity=9850,
            margin=0,
            margin_free=9850,
            margin_level=0,
            currency="USD",
        )

    @patch("execution.tasks.MT5Connector")
    def test_reconstructed_daily_loss_triggers_account_kill_switch(self, connector_type):
        connector = connector_type.return_value
        connector.account_info_for_account.return_value = self.account_info
        connector.positions_for_account.return_value = ()
        connector.history_deals_for_account.return_value = (
            SimpleNamespace(
                profit=0,
                commission=0,
                swap=0,
                fee=0,
                entry=0,
                position_id=77,
            ),
            SimpleNamespace(
                profit=-150,
                commission=0,
                swap=0,
                fee=0,
                entry=1,
                position_id=77,
            ),
        )

        result = kill_switch_monitor_task.run()

        self.policy.refresh_from_db()
        self.assertFalse(self.policy.entries_enabled)
        self.assertTrue(self.policy.emergency_stop)
        self.assertEqual(
            result["triggered"],
            [{"broker_account_id": self.account.id, "reason": "maximum_daily_loss"}],
        )
        risk_day = AccountRiskDay.objects.get(broker_account=self.account)
        self.assertEqual(risk_day.starting_equity, 10000)
        self.assertEqual(risk_day.baseline_source, "mt5_history")

    @patch("execution.tasks.MT5Connector")
    def test_missing_live_baseline_does_not_invent_daily_loss(self, connector_type):
        connector = connector_type.return_value
        connector.account_info_for_account.return_value = self.account_info
        connector.history_deals_for_account.side_effect = RuntimeError("history unavailable")

        result = kill_switch_monitor_task.run()

        self.policy.refresh_from_db()
        self.assertTrue(self.policy.entries_enabled)
        self.assertFalse(self.policy.emergency_stop)
        self.assertEqual(result["triggered"], [])
        risk_day = AccountRiskDay.objects.get(broker_account=self.account)
        self.assertFalse(risk_day.baseline_locked)

    @patch("execution.tasks._queue_or_dispatch_order")
    @patch("execution.services.orchestrator.create_close_order_for_position")
    @patch("execution.tasks.MT5Connector")
    def test_flattening_requires_explicit_policy_opt_in(
        self,
        connector_type,
        create_close,
        queue_order,
    ):
        connector = connector_type.return_value
        connector.account_info_for_account.return_value = self.account_info
        connector.history_deals_for_account.return_value = ()
        BrokerPosition.objects.create(
            broker_account=self.account,
            bot=self.bot,
            broker_position_ticket=876,
            ownership="ez_trade",
            symbol="EURUSD",
            side="buy",
            volume="0.01",
            open_price="1.10",
        )
        self.policy.emergency_stop = True
        self.policy.emergency_close_owned_positions = False
        self.policy.save(update_fields=["emergency_stop", "emergency_close_owned_positions"])

        kill_switch_monitor_task.run()
        create_close.assert_not_called()

        self.policy.emergency_close_owned_positions = True
        self.policy.save(update_fields=["emergency_close_owned_positions"])
        close_order = object()
        create_close.return_value = (close_order, True)
        kill_switch_monitor_task.run()

        create_close.assert_called_once()
        queue_order.assert_called_once_with(close_order, emergency=True)


class KillSwitchCancellationTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user("kill-owner", password="pw")
        self.account = BrokerAccount.objects.create(
            owner=self.user,
            name="Kill cancellation account",
            broker="mt5",
            connector="mt5_local",
            account_ref="kill-cancel",
        )
        self.bot = Bot.objects.create(
            owner=self.user,
            name="Kill cancellation bot",
            broker_account=self.account,
            asset=Asset.objects.create(symbol="KILLEURUSD"),
            status="active",
        )

    def _order(self, client_id, **overrides):
        values = {
            "bot": self.bot,
            "owner": self.user,
            "broker_account": self.account,
            "client_order_id": client_id,
            "intent": "entry",
            "symbol": "EURUSD",
            "side": "buy",
            "qty": "0.01",
            "status": "new",
        }
        values.update(overrides)
        return Order.objects.create(**values)

    @patch("execution.tasks.dispatch_cancel_order")
    def test_kill_cancels_local_and_broker_submitted_entries(self, broker_cancel):
        local = self._order("kill-local")
        submitted = self._order(
            "kill-submitted",
            status="ack",
            submitted_at=timezone.now(),
            broker_order_ticket=123,
        )

        result = _cancel_outstanding_entry_orders(self.account)

        local.refresh_from_db()
        self.assertEqual(local.status, "canceled")
        self.assertEqual(result["canceled_local_order_ids"], [local.id])
        self.assertEqual(result["broker_cancel_order_ids"], [submitted.id])
        broker_cancel.assert_called_once_with(submitted)

    @patch("execution.tasks.dispatch_cancel_order", side_effect=RuntimeError("cancel failed"))
    def test_cancel_failure_is_reported(self, _broker_cancel):
        submitted = self._order(
            "kill-cancel-failure",
            status="ack",
            submitted_at=timezone.now(),
        )

        with self.assertLogs("execution.tasks", level="ERROR"):
            result = _cancel_outstanding_entry_orders(self.account)

        self.assertEqual(result["cancel_failures"][0]["order_id"], submitted.id)
        self.assertIn("cancel failed", result["cancel_failures"][0]["error"])
