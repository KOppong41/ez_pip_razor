from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from typing import List, Literal, Optional

from execution.services.marketdata import Candle

Action = Literal["open", "skip"]
Direction = Literal["buy", "sell"]
Trend = Literal["up", "down", "flat"]


@dataclass
class MarubozuDecision:
    """
    Result from the marubozu strategy for a given candle series.

    Independent of Django models so it can be:
      - reused in backtests
      - converted into a Signal/Decision later.
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
    ATR-normalised trend check on closes.

    Used to make marubozu a *trend-following* pattern:
      - up   if last close >= first close + min_change_atr * ATR
      - down if last close <= first close - min_change_atr * ATR
      - flat otherwise
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
    """
    Check if latest candle is within `max_dist_atr * ATR` of swing high/low.
    For marubozu we want continuation/breakout *near* extremes, not mid-range.
    """
    if atr_value <= 0:
        return False
    dist = _distance_to_extreme(candles, side=side, lookback=lookback)
    return dist <= atr_value * max_dist_atr


def _marubozu_quality_score(
    candle: Candle,
    atr_value: Decimal,
    trend: Trend,
    dist_to_extreme: Decimal,
) -> float:
    """
    0–1 quality score for a marubozu candle combining:
      - range vs ATR
      - body fraction of range (body / (high-low))
      - proximity to recent swing high/low
      - whether we are moving with a clear trend
    """
    if atr_value <= 0:
        return 0.0

    o = candle["open"]
    c = candle["close"]
    h = candle["high"]
    l = candle["low"]

    rng = h - l
    body = abs(c - o)
    if rng <= 0 or body <= 0:
        return 0.0

    # Range vs ATR (cap at 3 ATR)
    rng_norm = float(min(rng / atr_value, Decimal("3.0")) / Decimal("3.0"))

    # Body fraction: we want a full-bodied candle (e.g. >= 0.7 of range)
    body_frac = body / rng
    body_norm = float(max(Decimal("0"), min(body_frac, Decimal("1.0"))))

    # Proximity to extreme (0 if >= 1.5 ATR away)
    prox_norm = 0.0
    if atr_value > 0:
        prox_norm = float(
            max(
                Decimal("0"),
                Decimal("1.0") - (dist_to_extreme / (atr_value * Decimal("1.5"))),
            )
        )
        prox_norm = max(0.0, min(1.0, prox_norm))

    trend_bonus = 0.1 if trend in ("up", "down") else 0.0

    score = 0.35 * rng_norm + 0.35 * body_norm + 0.3 * prox_norm + trend_bonus
    return max(0.0, min(1.0, score))


def detect_marubozu(candles: List[Candle]) -> MarubozuDecision:
    """
    Detect a *filtered* marubozu setup on the last candle.

    This version treats marubozu as a *trend-following momentum* signal:
      - requires meaningful prior trend
      - requires strong full-bodied candle with small wicks
      - requires location near recent swing high (bull) or low (bear)
      - uses ATR for volatility sanity and SL/TP sizing
    """
    if not candles:
        return MarubozuDecision(action="skip", reason="no_candles")

    candle = candles[-1]
    o = candle["open"]
    c = candle["close"]
    h = candle["high"]
    l = candle["low"]

    atr_val = _atr_like(candles, period=14)
    if atr_val <= 0:
        return MarubozuDecision(action="skip", reason="atr_zero")

    rng = h - l
    if rng <= 0:
        return MarubozuDecision(action="skip", reason="zero_range")

    # Volatility sanity: avoid micro candles and extreme spikes
    if rng <= atr_val * Decimal("0.5"):
        return MarubozuDecision(action="skip", reason="range_too_small")
    if rng >= atr_val * Decimal("3.0"):
        return MarubozuDecision(action="skip", reason="range_too_large")

    body = abs(c - o)
    if body <= 0:
        return MarubozuDecision(action="skip", reason="zero_body")

    # Marubozu shape:
    #  - body is large fraction of range
    #  - both wicks are small
    body_frac = body / rng
    upper_wick = h - max(o, c)
    lower_wick = min(o, c) - l

    if body_frac < Decimal("0.7"):
        return MarubozuDecision(action="skip", reason="body_not_dominant")

    if upper_wick > rng * Decimal("0.15") or lower_wick > rng * Decimal("0.15"):
        return MarubozuDecision(action="skip", reason="wicks_too_large")

    # Trend & location
    trend = _detect_trend(candles, lookback=20, atr_value=atr_val)

    # --- Bullish marubozu: uptrend, strong green candle near swing high ---
    if c > o:
        if trend != "up":
            return MarubozuDecision(action="skip", reason="no_uptrend")

        dist_ext = _distance_to_extreme(candles, side="high", lookback=20)
        if not _is_near_extreme(candles, side="high", lookback=20, atr_value=atr_val):
            return MarubozuDecision(action="skip", reason="not_near_swing_high")

        entry = c
        sl = l - atr_val * Decimal("0.25")
        risk = entry - sl
        if risk <= 0:
            return MarubozuDecision(action="skip", reason="invalid_risk_bull")

        tp = entry + risk * Decimal("2")
        score = _marubozu_quality_score(candle, atr_val, trend, dist_ext)
        return MarubozuDecision(
            action="open",
            direction="buy",
            sl=sl,
            tp=tp,
            reason="bullish_marubozu",
            score=score,
        )

    # --- Bearish marubozu: downtrend, strong red candle near swing low ---
    if c < o:
        if trend != "down":
            return MarubozuDecision(action="skip", reason="no_downtrend")

        dist_ext = _distance_to_extreme(candles, side="low", lookback=20)
        if not _is_near_extreme(candles, side="low", lookback=20, atr_value=atr_val):
            return MarubozuDecision(action="skip", reason="not_near_swing_low")

        entry = c
        sl = h + atr_val * Decimal("0.25")
        risk = sl - entry
        if risk <= 0:
            return MarubozuDecision(action="skip", reason="invalid_risk_bear")

        tp = entry - risk * Decimal("2")
        score = _marubozu_quality_score(candle, atr_val, trend, dist_ext)
        return MarubozuDecision(
            action="open",
            direction="sell",
            sl=sl,
            tp=tp,
            reason="bearish_marubozu",
            score=score,
        )

    # If we got here, candle is essentially doji-like (open ~ close)
    return MarubozuDecision(action="skip", reason="doji_like")
