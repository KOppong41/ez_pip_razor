from decimal import Decimal

from django.test import SimpleTestCase

from execution.services.trade_constraints import LotConstraints, distance_to_price, snap_quantity


class TradeConstraintTest(SimpleTestCase):
    def test_distance_to_price_fx_pips_vs_points(self):
        point = Decimal("0.0001")
        one_pip_price = distance_to_price(Decimal("1"), "pips", point)
        ten_points_price = distance_to_price(Decimal("10"), "points", point)
        self.assertEqual(one_pip_price, ten_points_price)
        self.assertEqual(one_pip_price, Decimal("0.0010"))

    def test_distance_to_price_price_unit_passthrough(self):
        self.assertEqual(distance_to_price(Decimal("5"), "price", Decimal("0.1")), Decimal("5"))

    def test_distance_to_price_uses_symbol_digits_for_points_and_pips(self):
        eur_point = Decimal("0.00001")
        xau_point = Decimal("0.001")
        self.assertEqual(distance_to_price(Decimal("50"), "points", eur_point, digits=5), Decimal("0.00050"))
        self.assertEqual(distance_to_price(Decimal("50"), "points", xau_point, digits=3), Decimal("0.050"))
        self.assertEqual(distance_to_price(Decimal("1"), "pips", eur_point, digits=5), Decimal("0.00010"))
        self.assertEqual(distance_to_price(Decimal("1"), "pips", Decimal("0.0001"), digits=4), Decimal("0.0001"))

    def test_distance_to_price_supports_percent_and_atr_units(self):
        self.assertEqual(
            distance_to_price(
                Decimal("0.5"),
                "percent",
                Decimal("0.01"),
                market_price=Decimal("2000"),
            ),
            Decimal("10.0"),
        )
        self.assertEqual(
            distance_to_price(
                Decimal("1.5"),
                "atr",
                Decimal("0.01"),
                atr=Decimal("4"),
            ),
            Decimal("6.0"),
        )

    def test_snap_quantity_floors_to_step_and_max(self):
        constraints = LotConstraints(min_lot=Decimal("0.05"), max_lot=Decimal("1.0"), lot_step=Decimal("0.01"))
        self.assertEqual(snap_quantity(Decimal("0.078"), constraints), Decimal("0.07"))

    def test_snap_quantity_enforces_max(self):
        constraints = LotConstraints(min_lot=Decimal("0.1"), max_lot=Decimal("0.5"), lot_step=Decimal("0.1"))
        self.assertEqual(snap_quantity(Decimal("0.83"), constraints), Decimal("0.5"))
