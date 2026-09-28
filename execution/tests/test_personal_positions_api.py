from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import TestCase

from brokers.models import BrokerAccount
from execution.models import BrokerPosition


class PersonalPositionsApiTest(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user("positions-user", password="pass")
        self.other_user = get_user_model().objects.create_user("other-positions-user")
        self.account = BrokerAccount.objects.create(
            owner=self.user, name="MT5", broker="mt5", connector="mt5_local",
            account_ref="positions-1",
        )
        self.other_account = BrokerAccount.objects.create(
            owner=self.other_user, name="Other MT5", broker="mt5", connector="mt5_local",
            account_ref="positions-2",
        )
        self.client.force_login(self.user)

    def position(self, ticket, status, account=None):
        return BrokerPosition.objects.create(
            broker_account=account or self.account,
            broker_position_ticket=ticket, ownership="ez_trade", symbol="EURUSD",
            side="buy", volume="0.01", open_price="1.1", status=status,
        )

    def clear(self, **extra):
        return self.client.post(
            "/api/personal/positions/", {"action": "clear_closed", **extra},
            content_type="application/json",
        )

    def test_clear_preserves_records_and_only_hides_closed_positions_for_account(self):
        closed = self.position(1, "closed")
        opened = self.position(2, "open")
        missing = self.position(3, "missing")
        other = self.position(4, "closed", self.other_account)
        response = self.clear()
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), {"cleared": 1})
        closed.refresh_from_db()
        self.assertIsNotNone(closed.cleared_from_positions_at)
        self.assertEqual(closed.status, "closed")
        for row in (opened, missing, other):
            row.refresh_from_db()
            self.assertIsNone(row.cleared_from_positions_at)
        self.assertEqual(BrokerPosition.objects.count(), 4)
        response = self.client.get("/api/personal/positions/")
        self.assertEqual({row["id"] for row in response.json()}, {opened.id, missing.id})
        self.assertEqual(self.clear().json(), {"cleared": 0})

    def test_reopened_or_missing_ticket_is_visible_even_if_previously_cleared(self):
        position = self.position(1, "closed")
        self.clear()
        for state in ("open", "missing"):
            BrokerPosition.objects.filter(pk=position.pk).update(status=state)
            rows = self.client.get("/api/personal/positions/").json()
            self.assertEqual([row["id"] for row in rows], [position.id])

    def test_newly_closed_positions_are_visible_after_earlier_clear(self):
        self.position(1, "closed")
        self.clear()
        new = self.position(2, "closed")
        rows = self.client.get("/api/personal/positions/").json()
        self.assertEqual([row["id"] for row in rows], [new.id])

    def test_cannot_clear_another_users_account(self):
        other = self.position(1, "closed", self.other_account)
        response = self.clear(broker_account_id=self.other_account.id)
        self.assertEqual(response.status_code, 400)
        other.refresh_from_db()
        self.assertIsNone(other.cleared_from_positions_at)

    @patch("brokers.models.get_broker_account_limit", return_value=2)
    def test_multiple_accounts_require_explicit_selection(self, account_limit):
        second = BrokerAccount.objects.create(
            owner=self.user, name="Second MT5", broker="mt5", connector="mt5_local",
            account_ref="positions-3",
        )
        first_position = self.position(1, "closed")
        self.position(2, "closed", second)
        self.assertEqual(self.clear().status_code, 400)
        self.assertEqual(self.clear(broker_account_id=second.id).json(), {"cleared": 1})
        first_position.refresh_from_db()
        self.assertIsNone(first_position.cleared_from_positions_at)

    def test_unknown_action_and_unauthenticated_request_do_not_clear(self):
        position = self.position(1, "closed")
        response = self.client.post(
            "/api/personal/positions/", {"action": "clear_all"},
            content_type="application/json",
        )
        self.assertEqual(response.status_code, 400)
        self.client.logout()
        self.assertIn(self.clear().status_code, (401, 403))
        position.refresh_from_db()
        self.assertIsNone(position.cleared_from_positions_at)
