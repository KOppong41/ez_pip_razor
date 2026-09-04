from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from decimal import Decimal, InvalidOperation

from django.utils import timezone


@dataclass(frozen=True)
class ScalperPositionPlan:
    new_sl: Decimal | None = None
    close: bool = False
    reason: str = ""


def _decimal(value) -> Decimal | None:
    if value is None:
        return None
    try:
        return Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError):
        return None


def plan_scalper_position(position, market_price, *, now: datetime | None = None):
    """Plan broker-side management for one EZ Trade-owned scalper position.

    ``None`` means the position was not opened from a scalper decision and the
    caller may apply its generic trailing policy instead.
    """
    if not getattr(position, "is_manageable", False):
        return None
    order = getattr(position, "originating_order", None)
    decision = getattr(order, "decision", None) if order else None
    params = (getattr(decision, "params", None) or {}) if decision else {}
    scalper = params.get("scalper")
    if not isinstance(scalper, dict):
        return None

    market = _decimal(market_price)
    entry = _decimal(params.get("entry")) or _decimal(getattr(position, "open_price", None))
    initial_sl = _decimal(params.get("sl")) or _decimal(getattr(order, "sl", None))
    if market is None or entry is None or initial_sl is None:
        return ScalperPositionPlan(reason="invalid_scalper_metadata")
    risk = abs(entry - initial_sl)
    if risk <= 0:
        return ScalperPositionPlan(reason="invalid_initial_risk")

    side = (getattr(position, "side", "") or "").lower()
    if side not in {"buy", "sell"}:
        return ScalperPositionPlan(reason="invalid_position_side")
    reward = market - entry if side == "buy" else entry - market

    try:
        limit_min = int(scalper.get("time_in_trade_limit_min") or 0)
    except (TypeError, ValueError):
        limit_min = 0
    opened_at = (
        getattr(position, "opened_at", None)
        or getattr(decision, "decided_at", None)
        or getattr(order, "created_at", None)
    )
    current_time = now or timezone.now()
    if limit_min > 0 and opened_at and current_time - opened_at >= timedelta(minutes=limit_min):
        if abs(reward / risk) <= Decimal("0.3"):
            return ScalperPositionPlan(close=True, reason="scalper_stale_near_breakeven")

    if reward <= 0:
        return ScalperPositionPlan(reason="scalper_not_profitable")

    candidates: list[Decimal] = []
    be_trigger_value = _decimal(scalper.get("be_trigger_r"))
    be_trigger = be_trigger_value if be_trigger_value is not None else Decimal("1.0")
    if be_trigger > 0 and reward >= risk * be_trigger:
        buffer_value = _decimal(scalper.get("be_buffer_r"))
        buffer_r = buffer_value if buffer_value is not None else Decimal("0.2")
        candidates.append(entry + risk * buffer_r if side == "buy" else entry - risk * buffer_r)

    trail_trigger_value = _decimal(scalper.get("trail_trigger_r"))
    trail_trigger = trail_trigger_value if trail_trigger_value is not None else Decimal("1.5")
    if trail_trigger > 0 and reward >= risk * trail_trigger:
        mode = str(scalper.get("trail_mode") or "swing").lower()
        distance_r = Decimal("0.50") if mode == "swing" else Decimal("0.35")
        candidates.append(market - risk * distance_r if side == "buy" else market + risk * distance_r)

    if not candidates:
        return ScalperPositionPlan(reason="scalper_trigger_not_reached")
    candidate = max(candidates) if side == "buy" else min(candidates)
    current_sl = _decimal(getattr(position, "sl", None))
    improves = current_sl is None or (side == "buy" and candidate > current_sl) or (
        side == "sell" and candidate < current_sl
    )
    if not improves:
        return ScalperPositionPlan(reason="scalper_sl_already_better")
    return ScalperPositionPlan(new_sl=candidate, reason="scalper_protection_advanced")
