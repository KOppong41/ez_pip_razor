from decimal import Decimal

from execution.services.order_guard import GuardInputs, apply_order_guards


def test_guard_rejects_when_sl_below_spread():
    guard = apply_order_guards(
        GuardInputs(
            sl_distance=Decimal("0.0005"),
            sl_unit="price",
            spread=Decimal("0.0004"),
            spread_unit="price",
            point=Decimal("0.0001"),
            min_stop_points=None,
            k_spread=Decimal("2"),
        )
    )
    assert not guard.ok
    assert guard.reason == "guard:sl_below_spread"


def test_guard_rejects_when_sl_below_broker_stop():
    guard = apply_order_guards(
        GuardInputs(
            sl_distance=Decimal("0.0005"),
            sl_unit="price",
            spread=None,
            spread_unit="price",
            point=Decimal("0.0001"),
            min_stop_points=Decimal("10"),  # 10 points = 0.0010 with point=0.0001
        )
    )
    assert not guard.ok
    assert guard.reason == "guard:sl_below_broker_stop"


def test_guard_accepts_when_all_conditions_met():
    guard = apply_order_guards(
        GuardInputs(
            sl_distance=Decimal("0.0020"),
            sl_unit="price",
            spread=Decimal("0.0005"),
            spread_unit="price",
            point=Decimal("0.0001"),
            min_stop_points=Decimal("5"),
        )
    )
    assert guard.ok
    assert guard.reason == "ok"
