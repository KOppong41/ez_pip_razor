from types import SimpleNamespace
from unittest.mock import patch

from django.test import TestCase, override_settings

from brokers.models import BrokerAccount
from execution.models import AccountRiskDay, RiskPolicy
from execution.tasks import kill_switch_monitor_task


@override_settings(ACCOUNT_RISK_OPENING_SNAPSHOT_GRACE_SECONDS=-1)
class KillSwitchRiskDayTests(TestCase):
    def setUp(self):
        self.account = BrokerAccount.objects.create(
            name="Kill-switch account",
            broker="mt5",
            connector="mt5_local",
            account_ref="kill-switch-risk-day",
            is_verified=True,
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
