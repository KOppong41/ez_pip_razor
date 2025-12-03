from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from typing import List, Literal, Optional

from execution.services.marketdata import Candle

Action = Literal["open", "skip"]
Direction = Literal["buy", "sell"]
Trend = Literal["up", "down", "flat"]


@dataclass
class ThreeSoldiersDecision:
    """
    Three-candle momentum/reversal pattern:

    - Bullish: "three white soldiers" after a downtrend, near recent swing low.
    - Bearish: "three black crows" after an uptrend, near recent swing high.
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

    We use this to require a meaningful prior move before a reversal pattern:
      - 'up'   if last close >= first close + min_change_atr * ATR
      - 'down' if last close <= first close - min_change_atr * ATR
      - 'flat' otherwise
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
    Three-soldiers/crows should appear near extremes, not in mid-range chop.
    """
    if atr_value <= 0:
        return False
    dist = _distance_to_extreme(candles, side=side, lookback=lookback)
    return dist <= atr_value * max_dist_atr


def _three_soldiers_quality_score(
    candles: List[Candle],
    atr_value: Decimal,
    trend: Trend,
    dist_to_extreme: Decimal,
    bullish: bool,
) -> float:
    """
    0–1 quality score considering:
      - average range vs ATR
      - body fraction (full-bodied candles)
      - step-like closes (higher highs / lower lows)
      - proximity to recent swing low/high
      - opposite prior trend (true reversal)
    """
    if atr_value <= 0 or len(candles) < 3:
        return 0.0

    c1, c2, c3 = candles[-3:]
    o1, o2, o3 = c1["open"], c2["open"], c3["open"]
    cl1, cl2, cl3 = c1["close"], c2["close"], c3["close"]
    h1, h2, h3 = c1["high"], c2["high"], c3["high"]
    l1, l2, l3 = c1["low"], c2["low"], c3["low"]

    rng1 = h1 - l1
    rng2 = h2 - l2
    rng3 = h3 - l3
    if rng1 <= 0 or rng2 <= 0 or rng3 <= 0:
        return 0.0

    # Average range vs ATR (cap at 3 ATR)
    avg_rng = (rng1 + rng2 + rng3) / Decimal("3")
    rng_norm = float(min(avg_rng / atr_value, Decimal("3.0")) / Decimal("3.0"))

    # Body fraction: we want full-bodied candles (>= ~0.6 of range)
    body1 = abs(cl1 - o1)
    body2 = abs(cl2 - o2)
    body3 = abs(cl3 - o3)
    if body1 <= 0 or body2 <= 0 or body3 <= 0:
        return 0.0

    body_frac1 = body1 / rng1
    body_frac2 = body2 / rng2
    body_frac3 = body3 / rng3
    body_frac = (body_frac1 + body_frac2 + body_frac3) / Decimal("3")
    body_norm = float(max(Decimal("0"), min(body_frac, Decimal("1.0"))))

    # Step-like closes: we want clear progression
    if bullish:
        step_ok = cl1 < cl2 < cl3
    else:
        step_ok = cl1 > cl2 > cl3
    step_norm = 1.0 if step_ok else 0.0

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

    # Reward true reversal context: bullish soldiers after downtrend, etc.
    trend_bonus = 0.1 if ((bullish and trend == "down") or (not bullish and trend == "up")) else 0.0

    score = (
        0.3 * rng_norm
        + 0.3 * body_norm
        + 0.2 * step_norm
        + 0.2 * prox_norm
        + trend_bonus
    )
    return max(0.0, min(1.0, score))


def detect_three_soldiers(candles: List[Candle]) -> ThreeSoldiersDecision:
    """
    Detect a *filtered* three-soldiers / three-crows setup on the last three candles.

    Bullish three white soldiers:
      - Prior downtrend.
      - Three consecutive bullish candles with higher closes.
      - Each opens within (or near) the body of the previous candle.
      - Pattern appears near recent swing low.

    Bearish three black crows:
      - Prior uptrend.
      - Three consecutive bearish candles with lower closes.
      - Each opens within (or near) the body of the previous candle.
      - Pattern appears near recent swing high.
    """
    if len(candles) < 3:
        return ThreeSoldiersDecision(action="skip", reason="not_enough_candles")

    c1, c2, c3 = candles[-3:]
    o1, o2, o3 = c1["open"], c2["open"], c3["open"]
    cl1, cl2, cl3 = c1["close"], c2["close"], c3["close"]
    h1, h2, h3 = c1["high"], c2["high"], c3["high"]
    l1, l2, l3 = c1["low"], c2["low"], c3["low"]

    atr_val = _atr_like(candles, period=14)
    if atr_val <= 0:
        return ThreeSoldiersDecision(action="skip", reason="atr_zero")

    # Basic volatility sanity: each candle should be meaningful but not absurd
    rng1 = h1 - l1
    rng2 = h2 - l2
    rng3 = h3 - l3
    for i, rng in enumerate((rng1, rng2, rng3), start=1):
        if rng <= atr_val * Decimal("0.5"):
            return ThreeSoldiersDecision(
                action="skip", reason=f"range_too_small_candle_{i}"
            )
        if rng >= atr_val * Decimal("3.0"):
            return ThreeSoldiersDecision(
                action="skip", reason=f"range_too_large_candle_{i}"
            )

    # --- Bullish three soldiers (reversal after downtrend, near swing low) ---
    # Conditions:
    #   - all three candles bullish
    #   - closes strictly higher each bar
    #   - opens inside/near previous body
    if cl1 > o1 and cl2 > o2 and cl3 > o3 and cl1 < cl2 < cl3:
        trend = _detect_trend(candles, lookback=20, atr_value=atr_val)
        if trend != "down":
            return ThreeSoldiersDecision(action="skip", reason="no_prior_downtrend")

        dist_ext = _distance_to_extreme(candles, side="low", lookback=20)
        if not _is_near_extreme(candles, side="low", lookback=20, atr_value=atr_val):
            return ThreeSoldiersDecision(action="skip", reason="not_near_swing_low")

        # Open of c2 & c3 should be within previous body (or close to it)
        body1_low, body1_high = min(o1, cl1), max(o1, cl1)
        body2_low, body2_high = min(o2, cl2), max(o2, cl2)

        if not (body1_low <= o2 <= body1_high * Decimal("1.01")):
            return ThreeSoldiersDecision(action="skip", reason="c2_open_not_in_body1")
        if not (body2_low <= o3 <= body2_high * Decimal("1.01")):
            return ThreeSoldiersDecision(action="skip", reason="c3_open_not_in_body2")

        entry = cl3
        sl_raw = min(l1, l2, l3)
        sl = sl_raw - atr_val * Decimal("0.25")
        risk = entry - sl
        if risk <= 0:
            return ThreeSoldiersDecision(action="skip", reason="invalid_risk_bull")

        tp = entry + risk * Decimal("2")
        score = _three_soldiers_quality_score(
            candles[-3:], atr_val, trend, dist_ext, bullish=True
        )
        return ThreeSoldiersDecision(
            action="open",
            direction="buy",
            sl=sl,
            tp=tp,
            reason="bullish_three_soldiers",
            score=score,
        )

    # --- Bearish three black crows (reversal after uptrend, near swing high) ---
    if cl1 < o1 and cl2 < o2 and cl3 < o3 and cl1 > cl2 > cl3:
        trend = _detect_trend(candles, lookback=20, atr_value=atr_val)
        if trend != "up":
            return ThreeSoldiersDecision(action="skip", reason="no_prior_uptrend")

        dist_ext = _distance_to_extreme(candles, side="high", lookback=20)
        if not _is_near_extreme(candles, side="high", lookback=20, atr_value=atr_val):
            return ThreeSoldiersDecision(action="skip", reason="not_near_swing_high")

        body1_low, body1_high = min(o1, cl1), max(o1, cl1)
        body2_low, body2_high = min(o2, cl2), max(o2, cl2)

        if not (body1_low * Decimal("0.99") <= o2 <= body1_high):
            return ThreeSoldiersDecision(action="skip", reason="c2_open_not_in_body1")
        if not (body2_low * Decimal("0.99") <= o3 <= body2_high):
            return ThreeSoldiersDecision(action="skip", reason="c3_open_not_in_body2")

        entry = cl3
        sl_raw = max(h1, h2, h3)
        sl = sl_raw + atr_val * Decimal("0.25")
        risk = sl - entry
        if risk <= 0:
            return ThreeSoldiersDecision(action="skip", reason="invalid_risk_bear")

        tp = entry - risk * Decimal("2")
        score = _three_soldiers_quality_score(
            candles[-3:], atr_val, trend, dist_ext, bullish=False
        )
        return ThreeSoldiersDecision(
            action="open",
            direction="sell",
            sl=sl,
            tp=tp,
            reason="bearish_three_soldiers",
            score=score,
        )

    return ThreeSoldiersDecision(action="skip", reason="no_three_soldiers")
