from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from typing import List, Literal, Optional

from execution.services.marketdata import Candle

Direction = Literal["buy", "sell"]
Action = Literal["open", "skip"]
Trend = Literal["up", "down", "flat"]


@dataclass
class HaramiDecision:
    """
    Result from the harami strategy for a given candle series.

    This is intentionally independent of Django models so it can be:
    - used in backtests
    - converted into a Signal/Decision later.
    """
    action: Action
    direction: Optional[Direction] = None
    sl: Optional[Decimal] = None
    tp: Optional[Decimal] = None
    reason: str = ""
    score: float = 0.0  # optional quality score for filtering/ranking


def _is_bullish(c: Candle) -> bool:
    return c["close"] > c["open"]


def _is_bearish(c: Candle) -> bool:
    return c["close"] < c["open"]


def _body_bounds(c: Candle) -> tuple[Decimal, Decimal]:
    """Return (body_low, body_high) for the candle body (open/close only)."""
    o = c["open"]
    cl = c["close"]
    return (min(o, cl), max(o, cl))


def _body(c: Candle) -> Decimal:
    return abs(c["close"] - c["open"])


def _range(c: Candle) -> Decimal:
    return c["high"] - c["low"]


def _atr_like(candles: List[Candle], period: int = 14) -> Decimal:
    """Rough ATR-like volatility estimate using mean of (high - low)."""
    if len(candles) < period:
        return Decimal("0")
    window = candles[-period:]
    total = sum((c["high"] - c["low"] for c in window), Decimal("0"))
    return total / Decimal(str(period))


def _detect_trend(
    candles: List[Candle],
    lookback: int,
    atr_value: Decimal,
    min_change_atr: Decimal = Decimal("1.0"),
) -> Trend:
    """ATR-normalised trend check on closes."""
    n = len(candles)
    if n < lookback + 1 or atr_value <= 0:
        return "flat"

    window = candles[-(lookback + 1):]
    first = window[0]["close"]
    last = window[-1]["close"]
    change = last - first

    threshold = min_change_atr * atr_value
    if change >= threshold:
        return "up"
    if change <= -threshold:
        return "down"
    return "flat"


def _distance_to_extreme(
    candles: List[Candle],
    side: Literal["low", "high"],
    lookback: int,
) -> Decimal:
    """Distance from latest candle's low/high to recent swing low/high."""
    n = len(candles)
    if n == 0:
        return Decimal("0")
    window = candles[-min(lookback, n):]
    last = window[-1]

    if side == "low":
        swing = min(c["low"] for c in window)
        return abs(last["low"] - swing)
    else:
        swing = max(c["high"] for c in window)
        return abs(swing - last["high"])


def _is_near_extreme(
    candles: List[Candle],
    side: Literal["low", "high"],
    lookback: int,
    atr_value: Decimal,
    max_dist_atr: Decimal = Decimal("0.75"),
) -> bool:
    """Check if latest candle is within `max_dist_atr * ATR` of swing high/low."""
    if atr_value <= 0:
        return False
    dist = _distance_to_extreme(candles, side=side, lookback=lookback)
    return dist <= atr_value * max_dist_atr


def _harami_quality_score(
    c1: Candle,
    c2: Candle,
    atr_value: Decimal,
    trend: Trend,
    dist_to_extreme: Decimal,
) -> float:
    """Compute a soft 0–1 quality score for a harami pattern."""
    if atr_value <= 0:
        return 0.0

    body1 = _body(c1)
    body2 = _body(c2)
    if body1 <= 0 or body2 <= 0:
        return 0.0

    # 0–1: size of first (impulse) candle vs ATR (cap at 3 ATR)
    size_norm = float(min(body1 / atr_value, Decimal("3.0")) / Decimal("3.0"))

    # 0–1: we want the second body to be smaller (inside candle)
    ratio = body2 / body1
    # ratio ~0.3–0.6 is ideal; penalty if too large
    ratio_inv_norm = float(
        max(Decimal("0"), Decimal("1.2") - ratio) / Decimal("1.2")
    )
    ratio_inv_norm = max(0.0, min(1.0, ratio_inv_norm))

    # 0–1: proximity to extreme (0 if >= 1.5 ATR away)
    prox_norm = 0.0
    if dist_to_extreme is not None and atr_value > 0:
        prox_norm = float(
            max(
                Decimal("0"),
                Decimal("1.0") - (dist_to_extreme / (atr_value * Decimal("1.5"))),
            )
        )
        prox_norm = max(0.0, min(1.0, prox_norm))

    trend_bonus = 0.1 if trend in ("up", "down") else 0.0

    score = 0.4 * size_norm + 0.3 * ratio_inv_norm + 0.3 * prox_norm + trend_bonus
    return max(0.0, min(1.0, score))


def detect_harami(candles: List[Candle], min_quality_score: Decimal = Decimal("0.5")) -> HaramiDecision:
    """Detect a filtered Harami setup on the last two candles.
    
    Args:
        candles: Historical price candles.
        min_quality_score: Minimum quality score (0-1) to trigger entry. Default 0.5.
    """
    if len(candles) < 2:
        return HaramiDecision(action="skip", reason="not_enough_candles")

    c1 = candles[-2]  # large impulse candle
    c2 = candles[-1]  # inside candle

    body1_low, body1_high = _body_bounds(c1)
    body2_low, body2_high = _body_bounds(c2)

    atr_val = _atr_like(candles, period=14)
    if atr_val <= 0:
        return HaramiDecision(action="skip", reason="atr_zero")

    # Basic volatility sanity on the first candle (impulse)
    rng1 = _range(c1)
    if rng1 <= atr_val * Decimal("0.5"):
        return HaramiDecision(action="skip", reason="impulse_range_too_small")
    if rng1 >= atr_val * Decimal("3.0"):
        return HaramiDecision(action="skip", reason="impulse_range_too_large")

    trend = _detect_trend(candles, lookback=20, atr_value=atr_val)

    # --- Bullish harami: downtrend, big bearish then small bullish inside it ---
    if _is_bearish(c1) and _is_bullish(c2):
        if trend != "down":
            return HaramiDecision(action="skip", reason="no_downtrend")

        # body2 completely inside body1
        if body2_low >= body1_low and body2_high <= body1_high:
            # Location filter: pattern near recent swing low
            dist_ext = _distance_to_extreme(candles, side="low", lookback=20)
            if not _is_near_extreme(
                candles, side="low", lookback=20, atr_value=atr_val
            ):
                return HaramiDecision(action="skip", reason="not_near_swing_low")

            entry = c2["close"]
            pattern_low = min(c1["low"], c2["low"])
            sl = pattern_low - atr_val * Decimal("1.0")
            risk = entry - sl
            if risk <= 0:
                return HaramiDecision(action="skip", reason="invalid_risk_bull")
            tp = entry + risk * Decimal("3")
            score = _harami_quality_score(c1, c2, atr_val, trend, dist_ext)
            
            # Reject low-quality patterns
            if score < float(min_quality_score):
                return HaramiDecision(
                    action="skip",
                    reason="harami_quality_too_low",
                    score=score,
                )
            
            return HaramiDecision(
                action="open",
                direction="buy",
                sl=sl,
                tp=tp,
                reason="bullish_harami",
                score=score,
            )

    # --- Bearish harami: uptrend, big bullish then small bearish inside it ---
    if _is_bullish(c1) and _is_bearish(c2):
        if trend != "up":
            return HaramiDecision(action="skip", reason="no_uptrend")

        if body2_low >= body1_low and body2_high <= body1_high:
            dist_ext = _distance_to_extreme(candles, side="high", lookback=20)
            if not _is_near_extreme(
                candles, side="high", lookback=20, atr_value=atr_val
            ):
                return HaramiDecision(action="skip", reason="not_near_swing_high")

            entry = c2["close"]
            pattern_high = max(c1["high"], c2["high"])
            sl = pattern_high + atr_val * Decimal("1.0")
            risk = sl - entry
            if risk <= 0:
                return HaramiDecision(action="skip", reason="invalid_risk_bear")
            tp = entry - risk * Decimal("3")
            score = _harami_quality_score(c1, c2, atr_val, trend, dist_ext)
            
            # Reject low-quality patterns
            if score < float(min_quality_score):
                return HaramiDecision(
                    action="skip",
                    reason="harami_quality_too_low",
                    score=score,
                )
            
            return HaramiDecision(
                action="open",
                direction="sell",
                sl=sl,
                tp=tp,
                reason="bearish_harami",
                score=score,
            )

    return HaramiDecision(action="skip", reason="no_harami")
