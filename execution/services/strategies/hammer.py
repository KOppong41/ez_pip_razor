from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from typing import List, Literal, Optional

from execution.services.marketdata import Candle

Action = Literal["open", "skip"]
Direction = Literal["buy", "sell"]
Trend = Literal["up", "down", "flat"]


@dataclass
class HammerDecision:
    """
    Result from the hammer strategy for a given candle series.

    This is independent of Django models so we can:
      - reuse it in backtests
      - convert it into a Signal/Decision later.
    """
    action: Action
    direction: Optional[Direction] = None
    sl: Optional[Decimal] = None
    tp: Optional[Decimal] = None
    reason: str = ""
    score: float = 0.0  # optional quality score for filtering/ranking


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
    min_change_atr: Decimal = Decimal("0.5"),
) -> Trend:
    """
    ATR-normalised trend check on closes for context before the hammer.
    """
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


def _hammer_quality_score(
    candle: Candle,
    atr_value: Decimal,
    trend: Trend,
    dist_to_extreme: Decimal,
) -> float:
    """
    0–1 quality score for a hammer pattern combining:
      - range vs ATR
      - ratio of lower wick to body
      - proximity to recent swing low
      - whether we are following a meaningful downtrend
    """
    if atr_value <= 0:
        return 0.0

    o = candle["open"]
    c = candle["close"]
    h = candle["high"]
    l = candle["low"]

    body = abs(c - o)
    rng = h - l
    if rng <= 0 or body <= 0:
        return 0.0

    # Range vs ATR (cap at 3 ATR)
    rng_norm = float(min(rng / atr_value, Decimal("3.0")) / Decimal("3.0"))

    # Wick/body ratio – we want long lower wick (ideally >= 2x body)
    lower_wick = min(o, c) - l
    if lower_wick <= 0:
        wick_ratio_norm = 0.0
    else:
        ratio = lower_wick / body
        wick_ratio_norm = float(min(ratio, Decimal("4.0")) / Decimal("4.0"))

    # Proximity to swing low (0 if >= 1.5 ATR away)
    prox_norm = 0.0
    if atr_value > 0:
        prox_norm = float(
            max(
                Decimal("0"),
                Decimal("1.0") - (dist_to_extreme / (atr_value * Decimal("1.5"))),
            )
        )
        prox_norm = max(0.0, min(1.0, prox_norm))

    trend_bonus = 0.1 if trend == "down" else 0.0

    score = 0.35 * rng_norm + 0.35 * wick_ratio_norm + 0.3 * prox_norm + trend_bonus
    return max(0.0, min(1.0, score))


def detect_hammer(candles: List[Candle]) -> HammerDecision:
    """
    Detect a *filtered* bullish hammer on the last candle.

    Upgrades vs naive implementation:
      - requires meaningful prior downtrend
      - requires pattern near recent swing low
      - requires long lower wick and small body near top of range
      - uses ATR for volatility sanity and SL/TP sizing
    """
    if not candles:
        return HammerDecision(action="skip", reason="no_candles")

    candle = candles[-1]
    o = candle["open"]
    c = candle["close"]
    h = candle["high"]
    l = candle["low"]

    atr_val = _atr_like(candles, period=14)
    if atr_val <= 0:
        return HammerDecision(action="skip", reason="atr_zero")

    rng = h - l
    if rng <= 0:
        return HammerDecision(action="skip", reason="zero_range")

    # Volatility sanity: avoid micro / huge spike hammers
    if rng <= atr_val * Decimal("0.5"):
        return HammerDecision(action="skip", reason="range_too_small")
    if rng >= atr_val * Decimal("3.0"):
        return HammerDecision(action="skip", reason="range_too_large")

    # Hammer shape:
    body = abs(c - o)
    if body <= 0:
        return HammerDecision(action="skip", reason="zero_body")

    upper_wick = h - max(o, c)
    lower_wick = min(o, c) - l

    # Require small upper wick, long lower wick
    if lower_wick <= body * Decimal("2"):
        return HammerDecision(action="skip", reason="lower_wick_not_long_enough")
    if upper_wick > body:
        return HammerDecision(action="skip", reason="upper_wick_too_large")

    # Prefer close in upper half of the range
    mid_range = l + rng / 2
    if c < mid_range:
        return HammerDecision(action="skip", reason="close_not_high_enough")

    # Downtrend + location near swing low
    trend = _detect_trend(candles, lookback=20, atr_value=atr_val)
    if trend != "down":
        return HammerDecision(action="skip", reason="no_downtrend")

    dist_ext = _distance_to_extreme(candles, side="low", lookback=20)
    if not _is_near_extreme(candles, side="low", lookback=20, atr_value=atr_val):
        return HammerDecision(action="skip", reason="not_near_swing_low")

    # Entry/SL/TP
    entry = c
    sl = l - atr_val * Decimal("0.25")
    risk = entry - sl
    if risk <= 0:
        return HammerDecision(action="skip", reason="invalid_risk")

    tp = entry + risk * Decimal("2")
    score = _hammer_quality_score(candle, atr_val, trend, dist_ext)

    return HammerDecision(
        action="open",
        direction="buy",
        sl=sl,
        tp=tp,
        reason="bullish_hammer",
        score=score,
    )
