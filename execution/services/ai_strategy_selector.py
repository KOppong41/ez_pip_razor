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
    atr = _to_decimal(context.get("atr_price") or context.get("atr_points") or context.get("bar_range") or 0)
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
    available_order = list(dict.fromkeys(available))
    available_set = set(available_order)
    canon_symbol = canonical_symbol(symbol)

    vol_ratio = _volatility_ratio(context)
    spread = _to_decimal(context.get("spread_price") or context.get("spread_points") or 0)
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

    # Gold is selected from the observed regime. A static symbol preference
    # here would always consume the three available slots before momentum or
    # breakout strategies could participate.
    symbol_bias: list[str] = []
    if canon_symbol == "XAUUSD":
        regime = context.get("regime") if isinstance(context.get("regime"), Mapping) else {}
        trend_strength = abs(_to_decimal(regime.get("ema_slope_pct") or 0))
        atr_expansion = _to_decimal(regime.get("atr_ratio") or 0)
        structure = str(regime.get("structure") or "").lower()
        strong_trend = (
            bias in {"buy", "sell"}
            and (
                trend_strength >= Decimal("0.00015")
                or structure in {"higher_high", "lower_low"}
            )
        )
        high_volatility = vol_ratio >= Decimal("0.004") or atr_expansion >= Decimal("1.25")
        if high_volatility or strong_trend:
            candidates = ["momentum_ignition", "breakout_retest", "trend_pullback"]
        elif vol_ratio >= Decimal("0.002"):
            candidates = ["trend_pullback", "breakout_retest", "price_action_pinbar"]
        else:
            candidates = ["price_action_pinbar", "doji_breakout", "trend_pullback"]
        # Retain the other Gold setups below the preferred three so a limited
        # configured pool or the wide-spread filter has an ordered fallback.
        candidates += [
            name for name in (
                "trend_pullback", "breakout_retest", "momentum_ignition",
                "price_action_pinbar", "doji_breakout",
            ) if name not in candidates
        ]
    elif canon_symbol == "BTCUSD":
        symbol_bias = ["momentum_ignition", "breakout_retest", "trend_pullback", "price_action_pinbar"]
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
        selected = available_order

    return selected[:max_strategies]
