from __future__ import annotations

from decimal import Decimal
from typing import Iterable, Mapping, Any, List

from execution.utils.symbols import canonical_symbol


def _to_decimal(val: Any) -> Decimal:
    try:
        return Decimal(str(val))
    except Exception:
        return Decimal("0")


def _volatility_ratio(context: Mapping[str, Any]) -> Decimal:
    """
    Approximate volatility as ATR or bar range divided by last price.
    Falls back to zero if data is missing.
    """
    atr = _to_decimal(context.get("atr_points") or context.get("bar_range") or 0)
    last = _to_decimal(context.get("last_close") or 0)
    if atr <= 0 or last <= 0:
        return Decimal("0")
    return (atr / last).quantize(Decimal("0.0000001"))


def select_ai_strategies(
    *,
    engine_mode: str,
    available: Iterable[str],
    symbol: str | None = None,
    context: Mapping[str, Any] | None = None,
    max_strategies: int = 3,
) -> List[str]:
    """
    Heuristic AI strategy picker shared by all engines.
    - For higher volatility or strong trend: favour breakout/trend/momentum.
    - For quieter markets: favour precise price-action reversals.
    - Filters to the provided `available` strategies so it works for any engine.
    """
    context = context or {}
    available_set = {s for s in available}
    canon_symbol = canonical_symbol(symbol)

    vol_ratio = _volatility_ratio(context)
    spread = _to_decimal(context.get("spread_points") or 0)
    bias = (context.get("htf_bias") or "").lower()

    # Session hint: bias toward breakouts during London/NY, more selective in Asia/quiet.
    session = (context.get("session") or "").lower()
    session_trending = session in {"london", "new_york", "us"}
    session_quiet = session in {"asia", "overnight"}

    # Wide spreads relative to price? Stay selective.
    wide_spread = False
    if spread > 0 and _to_decimal(context.get("last_close") or 0) > 0:
        spread_ratio = (spread / _to_decimal(context.get("last_close"))).quantize(Decimal("0.0000001"))
        wide_spread = spread_ratio > Decimal("0.001")  # ~10 bps spread cap

    # Base candidate pools (ordered by preference)
    high_vol_pool = ["trend_pullback", "breakout_retest", "momentum_ignition"]
    mid_vol_pool = ["trend_pullback", "breakout_retest", "price_action_pinbar"]
    low_vol_pool = ["price_action_pinbar", "harami", "engulfing", "hammer", "shooting_star", "range_reversion"]

    candidates: list[str]
    if vol_ratio >= Decimal("0.004") or (session_trending and bias in {"buy", "sell"}):
        candidates = high_vol_pool
    elif vol_ratio >= Decimal("0.002"):
        candidates = mid_vol_pool
    else:
        candidates = low_vol_pool

    # Symbol-specific preferences layered on top of volatility heuristics.
    symbol_bias: list[str] = []
    if canon_symbol == "BTCUSD":
        symbol_bias = ["momentum_ignition", "breakout_retest", "trend_pullback", "price_action_pinbar"]
    elif canon_symbol == "XAUUSD":
        symbol_bias = ["price_action_pinbar", "trend_pullback", "doji_breakout", "breakout_retest"]
    elif canon_symbol in {"EURUSD", "GBPUSD"}:
        symbol_bias = ["trend_pullback", "doji_breakout", "price_action_pinbar", "range_reversion"]

    if symbol_bias:
        ordered: list[str] = []
        seen: set[str] = set()
        for name in symbol_bias + candidates:
            if name not in seen:
                ordered.append(name)
                seen.add(name)
        candidates = ordered

    if wide_spread:
        # When spreads are wide, avoid breakout/momentum-heavy sets; keep precise setups.
        candidates = [s for s in candidates if s in {"price_action_pinbar", "harami", "engulfing", "range_reversion"}]

    # Engine-specific availability filtering
    selected = [s for s in candidates if s in available_set]

    # Fallback: if nothing matched (e.g., scalper with limited registry), pick any available up to max_strategies.
    if not selected:
        selected = list(available_set)

    return selected[:max_strategies]
