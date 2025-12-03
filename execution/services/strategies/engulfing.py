from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from typing import Literal, List, Optional

from execution.services.marketdata import Candle

Action = Literal["open", "skip"]
Direction = Literal["buy", "sell"]
Trend = Literal["up", "down", "flat"]


@dataclass
class EngulfingDecision:
    """
    Result from the engulfing strategy for a given candle series.
    This is independent of Django models so we can:
      - reuse it in backtests
      - convert it into a Signal/Decision later.
    """
    action: Action
    direction: Optional[Direction] = None
    sl: Optional[Decimal] = None
    tp: Optional[Decimal] = None
    reason: str = ""
    score: float = 0.0  # optional quality score, not used everywhere yet


def _is_bullish(c: Candle) -> bool:
    return c["close"] > c["open"]


def _is_bearish(c: Candle) -> bool:
    return c["close"] < c["open"]


def _body(c: Candle) -> Decimal:
    return abs(c["close"] - c["open"])


def _range(c: Candle) -> Decimal:
    return c["high"] - c["low"]


def _atr_like(candles: List[Candle], period: int = 14) -> Decimal:
    """
    Very rough ATR-like volatility estimate: mean of (high - low)
    over the last `period` candles. This is just to size SL/TP and
    filter out ultra-small or ultra-huge bars.
    """
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
    Simple ATR-normalised trend check:
      - compare last close vs close `lookback` bars ago
      - if move >= min_change_atr * ATR -> trend up/down
      - else -> flat
    """
    n = len(candles)
    if n < lookback + 1 or atr_value <= 0:
        return "flat"

    window = candles[-(lookback + 1) :]
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
    """
    Distance from latest candle's low/high to recent swing low/high.
    Used for "location" filter (we want patterns near extremes, not mid-range).
    """
    n = len(candles)
    if n == 0:
        return Decimal("0")
    window = candles[-min(lookback, n) :]
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
    max_dist_atr: Decimal = Decimal("0.5"),
) -> bool:
    """
    Check if latest candle is within `max_dist_atr * ATR` of the recent
    swing high/low. This keeps us from trading patterns in the middle of
    the range.
    """
    if atr_value <= 0:
        return False
    dist = _distance_to_extreme(candles, side=side, lookback=lookback)
    return dist <= atr_value * max_dist_atr


def _engulfing_quality_score(
    c1: Candle,
    c2: Candle,
    atr_value: Decimal,
    trend: Trend,
    dist_to_extreme: Decimal,
) -> float:
    """
    Simple 0–1 quality score combining:
      - relative body size vs ATR
      - body2/body1 ratio
      - proximity to recent extreme
      - whether we are with/against a strong prior trend

    This is intentionally soft; callers can ignore it or use it
    with a threshold if desired.
    """
    if atr_value <= 0:
        return 0.0

    body1 = _body(c1)
    body2 = _body(c2)
    rng2 = _range(c2)

    if body1 <= 0 or body2 <= 0 or rng2 <= 0:
        return 0.0

    # 0–1: engulfing candle range vs ATR (cap at 2 ATR)
    rng_norm = float(min(rng2 / atr_value, Decimal("2.0")) / Decimal("2.0"))

    # 0–1: body2/body1 ratio (cap at 3x)
    ratio = body2 / body1
    ratio_norm = float(min(ratio, Decimal("3.0")) / Decimal("3.0"))

    # 0–1: closer to extreme is better (0 if >= 1.5 ATR away)
    prox_norm = 0.0
    if dist_to_extreme is not None and atr_value > 0:
        prox_norm = float(
            max(
                Decimal("0"),
                Decimal("1.0") - (dist_to_extreme / (atr_value * Decimal("1.5"))),
            )
        )
        prox_norm = max(0.0, min(1.0, prox_norm))

    # trend bonus: if we are reversing a clear trend, small bump
    trend_bonus = 0.1 if trend in ("up", "down") else 0.0

    score = 0.4 * rng_norm + 0.3 * ratio_norm + 0.3 * prox_norm + trend_bonus
    # clamp to [0, 1]
    return max(0.0, min(1.0, score))


def detect_engulfing(candles: List[Candle]) -> EngulfingDecision:
    """
    Detect a *filtered* engulfing setup on the last two candles.

    Upgrades vs a naive implementation:
      - requires meaningful prior trend
      - requires pattern near recent swing high/low
      - requires reasonable volatility (no micro/no huge spikes)
      - returns SL/TP sized from an ATR-like estimate
    """
    if len(candles) < 2:
        return EngulfingDecision(action="skip", reason="not_enough_candles")

    c1 = candles[-2]
    c2 = candles[-1]

    atr_val = _atr_like(candles, period=14)
    if atr_val <= 0:
        return EngulfingDecision(action="skip", reason="atr_zero")

    # Basic volatility filter on the engulfing candle
    rng2 = _range(c2)
    if rng2 <= atr_val * Decimal("0.5"):
        return EngulfingDecision(action="skip", reason="range_too_small")
    if rng2 >= atr_val * Decimal("2.5"):
        return EngulfingDecision(action="skip", reason="range_too_large")

    # Trend check over a longer window
    trend = _detect_trend(candles, lookback=20, atr_value=atr_val)

    # --- Bullish engulfing: downtrend, red then strong green engulfing near lows ---
    if _is_bearish(c1) and _is_bullish(c2):
        # body of candle 2 fully engulfs candle 1 body
        if c2["open"] <= c1["close"] and c2["close"] >= c1["open"]:
            if trend != "down":
                return EngulfingDecision(action="skip", reason="no_downtrend")

            # Location filter: near recent swing low
            dist_ext = _distance_to_extreme(candles, side="low", lookback=20)
            if not _is_near_extreme(
                candles, side="low", lookback=20, atr_value=atr_val
            ):
                return EngulfingDecision(action="skip", reason="not_near_swing_low")

            entry = c2["close"]
            pattern_low = min(c1["low"], c2["low"])
            sl = pattern_low - atr_val * Decimal("0.25")
            risk = entry - sl
            if risk <= 0:
                return EngulfingDecision(action="skip", reason="invalid_risk_bull")

            tp = entry + risk * Decimal("2")
            score = _engulfing_quality_score(c1, c2, atr_val, trend, dist_ext)
            return EngulfingDecision(
                action="open",
                direction="buy",
                sl=sl,
                tp=tp,
                reason="bullish_engulfing",
                score=score,
            )

    # --- Bearish engulfing: uptrend, green then strong red engulfing near highs ---
    if _is_bullish(c1) and _is_bearish(c2):
        if c2["open"] >= c1["close"] and c2["close"] <= c1["open"]:
            if trend != "up":
                return EngulfingDecision(action="skip", reason="no_uptrend")

            dist_ext = _distance_to_extreme(candles, side="high", lookback=20)
            if not _is_near_extreme(
                candles, side="high", lookback=20, atr_value=atr_val
            ):
                return EngulfingDecision(action="skip", reason="not_near_swing_high")

            entry = c2["close"]
            pattern_high = max(c1["high"], c2["high"])
            sl = pattern_high + atr_val * Decimal("0.25")
            risk = sl - entry
            if risk <= 0:
                return EngulfingDecision(action="skip", reason="invalid_risk_bear")

            tp = entry - risk * Decimal("2")
            score = _engulfing_quality_score(c1, c2, atr_val, trend, dist_ext)
            return EngulfingDecision(
                action="open",
                direction="sell",
                sl=sl,
                tp=tp,
                reason="bearish_engulfing",
                score=score,
            )

    return EngulfingDecision(action="skip", reason="no_engulfing")
