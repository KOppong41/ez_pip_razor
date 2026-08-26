from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings

from brokers.models import BrokerAccount


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
    @patch("execution.personal_api.refresh_mt5_markets_task.apply_async")
    @patch("execution.personal_api.check_mt5_account_task.apply_async")
    def test_connection_queues_health_and_market_tasks(self, health, markets):
        health.return_value.id = "health-task"
        markets.return_value.id = "markets-task"
        self.account.set_mt5_password("not-returned")
        self.account.save(update_fields=["mt5_password_enc"])

        response = self.client.post(
            "/api/personal/accounts/test/",
            data={"broker_account_id": self.account.id},
            content_type="application/json",
        )

        self.assertEqual(response.status_code, 202)
        self.assertEqual(response.json()["health_task_id"], "health-task")
        self.assertIn("queued_at", response.json())
        health.assert_called_once_with(
            args=[self.account.id], queue="mt5_execution"
        )
        markets.assert_called_once_with(
            args=[self.account.id], queue="mt5_execution"
        )
