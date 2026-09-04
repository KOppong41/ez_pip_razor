from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import patch

from django.test import TestCase, override_settings

from brokers.models import BrokerAccount
from execution.connectors.base import ConnectorError
from execution.services.brokers import (
    BrokerSymbolConstraints,
    _get_broker_constraints_cached,
    get_broker_symbol_constraints,
    missing_entry_constraint_fields,
)


@override_settings(BROKER_CONSTRAINT_CACHE_SECONDS=60)
class BrokerConstraintResolutionTests(TestCase):
    def setUp(self):
        _get_broker_constraints_cached.cache_clear()
        self.account = BrokerAccount.objects.create(
            name="MT5 constraints",
            broker="mt5",
            connector="mt5_local",
            account_ref="constraints-test",
        )
        self.info = SimpleNamespace(
            point=0.001,
            trade_tick_size=0.001,
            volume_min=0.01,
            volume_max=200.0,
            volume_step=0.01,
            trade_stops_level=0,
            trade_freeze_level=0,
        )

    def tearDown(self):
        _get_broker_constraints_cached.cache_clear()

    @patch("execution.connectors.mt5.is_mt5_available", return_value=True)
    @patch("execution.services.brokers.MT5Connector.symbol_info_for_account")
    def test_preserves_symbol_case_and_official_mt5_fields(
        self,
        symbol_info,
        _available,
    ):
        symbol_info.return_value = self.info

        constraints = get_broker_symbol_constraints(self.account, "XAUUSDm")

        symbol_info.assert_called_once()
        self.assertEqual(symbol_info.call_args.args[1], "XAUUSDm")
        self.assertEqual(constraints.point, Decimal("0.001"))
        self.assertEqual(constraints.min_lot, Decimal("0.01"))
        self.assertEqual(constraints.max_lot, Decimal("200.0"))
        self.assertEqual(constraints.lot_step, Decimal("0.01"))
        self.assertEqual(constraints.stops_level_points, Decimal("0"))
        self.assertEqual(constraints.freeze_level_points, Decimal("0"))
        self.assertEqual(missing_entry_constraint_fields(constraints), ())

    @patch("execution.connectors.mt5.is_mt5_available", return_value=True)
    @patch("execution.services.brokers.MT5Connector.symbol_info_for_account")
    def test_transient_failure_is_not_negatively_cached(
        self,
        symbol_info,
        _available,
    ):
        symbol_info.side_effect = [ConnectorError("temporary"), self.info]

        with self.assertLogs("execution.services.brokers", level="WARNING"):
            failed = get_broker_symbol_constraints(self.account, "XAUUSDm")
        recovered = get_broker_symbol_constraints(self.account, "XAUUSDm")

        self.assertIn("point", missing_entry_constraint_fields(failed))
        self.assertEqual(recovered.point, Decimal("0.001"))
        self.assertEqual(symbol_info.call_count, 2)

    def test_zero_stop_level_is_valid_but_missing_point_is_not(self):
        constraints = BrokerSymbolConstraints(
            min_lot=Decimal("0.01"),
            max_lot=Decimal("10"),
            lot_step=Decimal("0.01"),
            point=None,
            stops_level_points=Decimal("0"),
        )

        self.assertEqual(missing_entry_constraint_fields(constraints), ("point",))
