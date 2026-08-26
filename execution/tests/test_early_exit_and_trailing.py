from decimal import Decimal

from django.test import TestCase

from brokers.models import BrokerAccount
from execution.models import BrokerPosition
from execution.services.orchestrator import create_close_order_for_position


class ManualPositionProtectionTest(TestCase):
    def test_manual_position_cannot_be_closed_by_automation(self):
        account = BrokerAccount.objects.create(
            name="MT5 demo",
            broker="mt5",
            connector="mt5_local",
            account_ref="manual-protection",
        )
        position = BrokerPosition.objects.create(
            broker_account=account,
            broker_position_ticket=987654,
            ownership="manual",
            symbol="EURUSD",
            side="buy",
            volume=Decimal("0.10"),
            open_price=Decimal("1.1000"),
        )
        with self.assertRaisesRegex(ValueError, "Manual or unknown"):
            create_close_order_for_position(position, account)
