from decimal import Decimal
from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings

from brokers.models import BrokerAccount
from execution.services.accounts import get_account_balances


class BrokerAccountBalanceTests(TestCase):
    def setUp(self):
        User = get_user_model()
        self.user = User.objects.create_user(username="test", password="test")

    def test_paper_account_returns_configured_balance(self):
        acct = BrokerAccount.objects.create(
            name="Paper",
            broker="paper",
            account_ref="paper-1",
            owner=self.user,
        )
        data = get_account_balances(acct)
        self.assertEqual(data["balance"], Decimal("100000"))
        self.assertEqual(data["equity"], Decimal("100000"))
        self.assertEqual(data["margin"], Decimal("0"))

    @override_settings(PAPER_START_BALANCE=Decimal("50000"))
    def test_paper_account_uses_override_balance(self):
        acct = BrokerAccount.objects.create(
            name="PaperOverride",
            broker="paper",
            account_ref="paper-2",
            owner=self.user,
        )
        data = get_account_balances(acct)
        self.assertEqual(data["balance"], Decimal("50000"))
