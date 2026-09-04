from types import SimpleNamespace

from django.test import SimpleTestCase

from execution.services.brokers import _resolve_connector


class ConnectorGateTests(SimpleTestCase):
    def test_unknown_explicit_connector_never_falls_back_to_mt5(self):
        order = SimpleNamespace(
            broker_account=SimpleNamespace(connector="ctrader_api", broker="mt5")
        )
        connector, key = _resolve_connector(order)
        self.assertIsNone(connector)
        self.assertEqual(key, "ctrader_api")
