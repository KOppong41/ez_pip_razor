from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from typing import Optional


@dataclass(frozen=True)
class LotConstraints:
    min_lot: Decimal = Decimal("0")
    max_lot: Decimal = Decimal("0")
    lot_step: Decimal = Decimal("0")


def _pip_size(point: Optional[Decimal], digits: Optional[int] = None) -> Optional[Decimal]:
    if point is None:
        return None
    if digits is not None:
        return point * Decimal("10") if digits in (3, 5) else point
    # Preserve legacy behavior for callers that do not have symbol digits.
    return point * Decimal("10") if point < Decimal("1") else point


def distance_to_price(
    value: Decimal,
    unit: str,
    point: Optional[Decimal],
    *,
    market_price: Optional[Decimal] = None,
    atr: Optional[Decimal] = None,
    digits: Optional[int] = None,
) -> Decimal:
    """
    Convert a distance expressed in the declared unit into a price delta.

    Supported units:
    - price: passthrough
    - points: multiply by broker point
    - pips: multiply by symbol-aware pip size, fallback 0.0001
    - percent: percentage of the current market price
    - atr: multiple of the supplied ATR value
    """
    unit = (unit or "points").lower()
    if unit == "price":
        return value
    if unit == "points":
        return value * (point or Decimal("1"))
    if unit == "pips":
        pip = _pip_size(point, digits=digits) or Decimal("0.0001")
        return value * pip
    if unit == "percent":
        if market_price is None or market_price <= 0:
            raise ValueError("market_price is required for percent distances")
        return market_price * value / Decimal("100")
    if unit == "atr":
        if atr is None or atr <= 0:
            raise ValueError("atr is required for ATR distances")
        return atr * value
    raise ValueError(f"Unsupported distance unit: {unit}")


def snap_quantity(qty: Decimal, lot_constraints: LotConstraints) -> Decimal:
    """
    Snap quantity down to the nearest lot_step (if provided) and enforce min/max.
    Never rounds up (avoid hidden risk). Caller should drop the trade if result < min_lot.
    """
    min_lot = lot_constraints.min_lot or Decimal("0")
    max_lot = lot_constraints.max_lot or Decimal("0")
    step = lot_constraints.lot_step or Decimal("0")

    snapped = qty
    if step > 0:
        steps = (qty / step).to_integral_value(rounding="ROUND_FLOOR")
        snapped = step * steps

    if max_lot > 0 and snapped > max_lot:
        snapped = max_lot

    return snapped
