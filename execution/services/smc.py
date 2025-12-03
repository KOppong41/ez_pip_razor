from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from typing import List, Literal, Optional

from execution.services.marketdata import Candle

SweepDirection = Literal["bullish", "bearish"]
FVGDirection = Literal["bullish", "bearish"]


@dataclass
class LiquiditySweepResult:
    """
    Simple liquidity sweep:

    - price first moves beyond a level (grabs stops)
    - then closes back inside / beyond in the opposite direction
    """
    direction: SweepDirection
    level: Decimal
    broken_index: int       # candle index where level is violated
    confirm_index: int      # candle index where it closes back
    valid: bool = True
    reason: str = ""


@dataclass
class FVGZone:
    """
    Fair value gap zone (very simplified ICT-style):

    For bullish:
      - previous high < next low  -> gap between them
    For bearish:
      - previous low > next high  -> gap between them
    """
    direction: FVGDirection
    start_index: int
    end_index: int
    lower: Decimal
    upper: Decimal


def detect_liquidity_sweep(
    candles: List[Candle],
    level: Decimal,
    direction: SweepDirection,
    lookback: int = 20,
) -> Optional[LiquiditySweepResult]:
    """
    Detect a basic liquidity sweep around 'level' on the last 'lookback' candles.

    For bullish sweep (sell-side stops):
      - price trades below level,
      - later a candle closes back above level.

    For bearish sweep (buy-side stops):
      - price trades above level,
      - later a candle closes back below level.
    """
    n = len(candles)
    if n == 0:
        return None

    start = max(0, n - lookback)
    broken_idx = None
    confirm_idx = None

    if direction == "bullish":
        # look for low < level then close > level
        for i in range(start, n):
            c = candles[i]
            if broken_idx is None and c["low"] < level:
                broken_idx = i
            elif broken_idx is not None and c["close"] > level:
                confirm_idx = i
                break
    else:
        # bearish: high > level then close < level
        for i in range(start, n):
            c = candles[i]
            if broken_idx is None and c["high"] > level:
                broken_idx = i
            elif broken_idx is not None and c["close"] < level:
                confirm_idx = i
                break

    if broken_idx is None or confirm_idx is None:
        return None

    return LiquiditySweepResult(
        direction=direction,
        level=level,
        broken_index=broken_idx,
        confirm_index=confirm_idx,
        valid=True,
        reason="sweep_detected",
    )


def find_fair_value_gaps(
    candles: List[Candle],
    min_size_ratio: Decimal = Decimal("0.0005"),
) -> List[FVGZone]:
    """
    Find basic fair value gaps over the whole series.

    We use the 3-candle pattern:
      - i-1, i, i+1

    Bullish FVG:
      high[i-1] < low[i+1] (no overlap) -> gap: [high[i-1], low[i+1]]

    Bearish FVG:
      low[i-1] > high[i+1] -> gap: [high[i+1], low[i-1]]
    """
    zones: List[FVGZone] = []
    n = len(candles)
    if n < 3:
        return zones

    for i in range(1, n - 1):
        prev = candles[i - 1]
        nxt = candles[i + 1]

        prev_high = prev["high"]
        prev_low = prev["low"]
        next_high = nxt["high"]
        next_low = nxt["low"]

        # Bullish FVG (gap up)
        if prev_high < next_low:
            gap_size = next_low - prev_high
            if gap_size > 0 and (gap_size / prev_high) >= min_size_ratio:
                zones.append(
                    FVGZone(
                        direction="bullish",
                        start_index=i - 1,
                        end_index=i + 1,
                        lower=prev_high,
                        upper=next_low,
                    )
                )

        # Bearish FVG (gap down)
        if prev_low > next_high:
            gap_size = prev_low - next_high
            if gap_size > 0 and (gap_size / prev_low) >= min_size_ratio:
                zones.append(
                    FVGZone(
                        direction="bearish",
                        start_index=i - 1,
                        end_index=i + 1,
                        lower=next_high,
                        upper=prev_low,
                    )
                )

    return zones


def last_fvg(
    candles: List[Candle],
    direction: Optional[FVGDirection] = None,
    min_size_ratio: Decimal = Decimal("0.0005"),
) -> Optional[FVGZone]:
    """
    Convenience helper: get the last FVG (optionally filtered by direction).
    """
    zones = find_fair_value_gaps(candles, min_size_ratio=min_size_ratio)
    if not zones:
        return None

    if direction is None:
        return zones[-1]

    for z in reversed(zones):
        if z.direction == direction:
            return z
    return None
