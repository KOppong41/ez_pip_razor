from types import SimpleNamespace
from unittest.mock import patch

from django.test import SimpleTestCase

from execution.services.brokers import _resolve_connector, validate_order_conditions


class ConnectorGateTests(SimpleTestCase):
    def test_unknown_explicit_connector_never_falls_back_to_mt5(self):
        order = SimpleNamespace(
            broker_account=SimpleNamespace(connector="ctrader_api", broker="mt5")
        )
        connector, key = _resolve_connector(order)
        self.assertIsNone(connector)
        self.assertEqual(key, "ctrader_api")

    @patch("execution.services.brokers.is_liquid_session", return_value=False)
    def test_tp1_exit_bypasses_entry_session_and_protection_gates(self, _session):
        order = SimpleNamespace(
            intent="exit",
            client_order_id="close:tp1|abc",
            broker_account=SimpleNamespace(broker="mt5"),
            sl=None,
            tp=None,
        )

        self.assertEqual(validate_order_conditions(order), (True, "ok"))
