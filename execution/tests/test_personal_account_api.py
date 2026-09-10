from datetime import timedelta
from decimal import Decimal
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings
from django.utils import timezone

from brokers.models import BrokerAccount
from execution.models import BrokerPosition, RiskPolicy


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

    def test_positions_lists_current_open_positions_first_and_newest_first(self):
        now = timezone.now()
        rows = [
            BrokerPosition.objects.create(
                broker_account=self.account,
                broker_position_ticket=300,
                ownership="ez_trade",
                symbol="EURUSDm",
                side="buy",
                volume="0.10",
                open_price="1.1000",
                status="closed",
                opened_at=now,
                last_reconciled_at=now,
            ),
            BrokerPosition.objects.create(
                broker_account=self.account,
                broker_position_ticket=100,
                ownership="ez_trade",
                symbol="XAUUSDm",
                side="buy",
                volume="0.01",
                open_price="3000",
                status="open",
                opened_at=now - timedelta(hours=2),
                last_reconciled_at=now - timedelta(minutes=5),
            ),
            BrokerPosition.objects.create(
                broker_account=self.account,
                broker_position_ticket=200,
                ownership="ez_trade",
                symbol="BTCUSDm",
                side="sell",
                volume="0.01",
                open_price="100000",
                status="open",
                opened_at=now - timedelta(hours=1),
                last_reconciled_at=now,
            ),
        ]

        response = self.client.get("/api/personal/positions/")

        self.assertEqual(response.status_code, 200, response.json())
        self.assertEqual(
            [item["id"] for item in response.json()],
            [rows[2].id, rows[1].id, rows[0].id],
        )
