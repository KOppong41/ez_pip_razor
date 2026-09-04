from decimal import Decimal

from django.test import SimpleTestCase

from execution.connectors.mt5 import execution_quality_metadata


class ExecutionQualityTests(SimpleTestCase):
    def test_buy_adverse_slippage_is_measured_in_broker_points(self):
        quality = execution_quality_metadata(
            requested_price="1.10000",
            fill_price="1.10015",
            side="buy",
            point="0.00001",
            max_deviation_points=10,
        )
        self.assertEqual(Decimal(quality["adverse_slippage_points"]), Decimal("15"))

    def test_favorable_sell_fill_has_zero_adverse_slippage(self):
        quality = execution_quality_metadata(
            requested_price="1.10000",
            fill_price="1.10010",
            side="sell",
            point="0.00001",
            max_deviation_points=20,
        )
        self.assertEqual(Decimal(quality["signed_slippage_points"]), Decimal("-10"))
        self.assertEqual(Decimal(quality["adverse_slippage_points"]), Decimal("0"))
