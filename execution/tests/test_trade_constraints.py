from decimal import Decimal

from execution.services.trade_constraints import LotConstraints, distance_to_price, snap_quantity


def test_distance_to_price_fx_pips_vs_points():
    point = Decimal("0.0001")
    # 1 pip = 10 points on 5-digit majors
    one_pip_price = distance_to_price(Decimal("1"), "pips", point)
    ten_points_price = distance_to_price(Decimal("10"), "points", point)
    assert one_pip_price == ten_points_price == Decimal("0.0010")


def test_distance_to_price_price_unit_passthrough():
    assert distance_to_price(Decimal("5"), "price", Decimal("0.1")) == Decimal("5")


def test_snap_quantity_floors_to_step_and_max():
    constraints = LotConstraints(min_lot=Decimal("0.05"), max_lot=Decimal("1.0"), lot_step=Decimal("0.01"))
    snapped = snap_quantity(Decimal("0.078"), constraints)
    # Floor to 0.07, not 0.08 (no rounding up)
    assert snapped == Decimal("0.07")


def test_snap_quantity_enforces_max():
    constraints = LotConstraints(min_lot=Decimal("0.1"), max_lot=Decimal("0.5"), lot_step=Decimal("0.1"))
    snapped = snap_quantity(Decimal("0.83"), constraints)
    assert snapped == Decimal("0.5")

