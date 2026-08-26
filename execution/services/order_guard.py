from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from typing import Optional

from execution.services.trade_constraints import distance_to_price


@dataclass(frozen=True)
class GuardInputs:
    sl_distance: Decimal
    sl_unit: str
    spread: Optional[Decimal] = None
    spread_unit: str = "points"
    point: Optional[Decimal] = None
    min_stop_points: Optional[Decimal] = None
    k_spread: Decimal = Decimal("2")  # require sl >= k * spread


@dataclass(frozen=True)
class GuardResult:
    ok: bool
    reason: str = "ok"


def apply_order_guards(guard: GuardInputs) -> GuardResult:
    """
    Apply unit-aware spread and stop-level checks before placing an order.

    Returns GuardResult(ok=True) if accepted, else ok=False with reason.
    """
    sl_price = distance_to_price(guard.sl_distance, guard.sl_unit, guard.point)

    # Spread guard
    if guard.spread is not None:
        spread_price = distance_to_price(guard.spread, guard.spread_unit, guard.point)
        if spread_price > 0 and sl_price < spread_price * guard.k_spread:
            return GuardResult(False, reason="guard:sl_below_spread")

    # Broker stop-level guard (stops_level is in MT5 points)
    if guard.min_stop_points is not None and guard.min_stop_points > 0:
        min_stop_price = distance_to_price(guard.min_stop_points, "points", guard.point)
        if sl_price < min_stop_price:
            return GuardResult(False, reason="guard:sl_below_broker_stop")

    return GuardResult(True)
