from unittest.mock import patch
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings

from brokers.models import BrokerAccount
from execution.models import RiskPolicy


class PersonalAccountApiTest(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user(
            "account-user", password="pass"
        )
        self.account = BrokerAccount.objects.create(
            owner=self.user,
            name="Local MT5",
            broker="mt5",
            connector="mt5_local",
            account_ref="account-1",
            mt5_login="30001",
            mt5_server="Broker-Demo",
        )
        self.client.force_login(self.user)

    def test_connection_requires_a_decryptable_password(self):
        response = self.client.post(
            "/api/personal/accounts/test/",
            data={"broker_account_id": self.account.id},
            content_type="application/json",
        )

        self.assertEqual(response.status_code, 400)
        self.assertIn("enter the password again", response.json()["detail"])

    @override_settings(BROKER_CREDS_KEY="test-broker-credential-key")
    @patch("execution.personal_api.test_mt5_account_task.apply_async")
    def test_connection_queues_serial_account_test(self, account_test):
        account_test.return_value.id = "account-test-task"
        self.account.set_mt5_password("not-returned")
        self.account.save(update_fields=["mt5_password_enc"])

        response = self.client.post(
            "/api/personal/accounts/test/",
            data={"broker_account_id": self.account.id},
            content_type="application/json",
        )

        self.assertEqual(response.status_code, 202)
        self.assertEqual(response.json()["task_id"], "account-test-task")
        self.assertIn("queued_at", response.json())
        account_test.assert_called_once_with(
            args=[self.account.id], queue="mt5_execution", priority=9
        )

    def test_risk_api_exposes_only_account_wide_controls(self):
        response = self.client.get("/api/personal/risk/")

        self.assertEqual(response.status_code, 200, response.json())
        payload = response.json()
        self.assertTrue(
            {
                "max_daily_loss_pct",
                "max_account_drawdown_pct",
                "stop_after_daily_profit_pct",
                "max_order_lot_size",
                "max_total_open_positions",
                "max_positions_per_symbol",
                "max_aggregate_open_lots",
            }.issubset(payload)
        )
        for removed in (
            "risk_per_trade_pct",
            "max_entry_trades_per_day",
            "max_spread_points",
            "deviation_points",
            "live_trading_confirmed",
            "emergency_close_owned_positions",
        ):
            self.assertNotIn(removed, payload)

    def test_risk_api_updates_account_exposure_limits(self):
        response = self.client.patch(
            "/api/personal/risk/",
            data={
                "max_order_lot_size": "0.40",
                "max_total_open_positions": 6,
                "max_positions_per_symbol": 2,
                "max_aggregate_open_lots": "1.25",
            },
            content_type="application/json",
        )

        self.assertEqual(response.status_code, 200, response.json())
        policy = RiskPolicy.objects.get(broker_account=self.account)
        self.assertEqual(policy.max_order_lot_size, Decimal("0.40"))
        self.assertEqual(policy.max_total_open_positions, 6)
        self.assertEqual(policy.max_positions_per_symbol, 2)
        self.assertEqual(policy.max_aggregate_open_lots, Decimal("1.25"))

    def test_risk_api_rejects_negative_hard_limit(self):
        response = self.client.patch(
            "/api/personal/risk/",
            data={"max_aggregate_open_lots": "-0.01"},
            content_type="application/json",
        )

        self.assertEqual(response.status_code, 400)
        self.assertIn("max_aggregate_open_lots", response.json())
