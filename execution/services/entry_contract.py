"""Price contracts shared by live submission and historical fills."""
from decimal import Decimal

from execution.services.trade_constraints import distance_to_price


def target_at_entry(side, entry, stop, target_rr, trigger=None):
    sign = Decimal("1") if side == "buy" else Decimal("-1")
    if trigger is not None and sign * (entry - Decimal(str(trigger))) < 0:
        raise ValueError("entry_trigger_lost")
    rr = Decimal(str(target_rr))
    risk = sign * (entry - stop)
    if not rr.is_finite() or rr <= 0 or risk <= 0:
        raise ValueError("invalid_entry_risk")
    return entry + sign * risk * rr


def gold_stop_reason(symbol_config, entry, stop, *, point, digits=None, atr=None):
    """Gold's stop envelope validates structure; it never relocates a stop."""
    if symbol_config is None or symbol_config.key.upper() not in {"XAUUSD", "GOLD"}:
        return None
    minimum = distance_to_price(symbol_config.sl_points_min, symbol_config.sl_points_unit,
                                point, market_price=entry, digits=digits, atr=atr)
    maximum = distance_to_price(symbol_config.sl_points_max, symbol_config.sl_points_unit,
                                point, market_price=entry, digits=digits, atr=atr)
    distance = abs(entry - stop)
    if distance < minimum:
        return "scalper:sl_below_min"
    if distance > maximum:
        return "scalper:sl_above_max"
    return None
