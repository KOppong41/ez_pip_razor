from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from typing import List, Literal, Optional

from execution.services.marketdata import Candle

Side = Literal["resistance", "support"]
TripleKind = Literal["triple_top", "triple_bottom"]


@dataclass
class SwingPoint:
    """
    Simple swing high/low.
    index = index in candles list
    price = high/low at the swing
    kind = 'high' or 'low'
    """
    index: int
    price: Decimal
    kind: Literal["high", "low"]


@dataclass
class TripleStructure:
    """
    Triple top / triple bottom (Sanzan / Sansen-style).
    """
    kind: TripleKind
    level: Decimal          # average of the 3 swing prices
    swing_indexes: List[int]


def detect_swings(
    candles: List[Candle],
    window: int = 2,
) -> List[SwingPoint]:
    """
    Very simple swing detector:

    - swing high: high[i] > highs in [i-window, i+window]
    - swing low:  low[i]  < lows  in [i-window, i+window]
    """
    swings: List[SwingPoint] = []
    n = len(candles)
    if n == 0:
        return swings

    for i in range(window, n - window):
        c = candles[i]
        h = c["high"]
        l = c["low"]

        highs_left = [candles[j]["high"] for j in range(i - window, i)]
        highs_right = [candles[j]["high"] for j in range(i + 1, i + 1 + window)]

        lows_left = [candles[j]["low"] for j in range(i - window, i)]
        lows_right = [candles[j]["low"] for j in range(i + 1, i + 1 + window)]

        if h > max(highs_left + highs_right):
            swings.append(SwingPoint(index=i, price=h, kind="high"))

        if l < min(lows_left + lows_right):
            swings.append(SwingPoint(index=i, price=l, kind="low"))

    return swings


def _group_triples(
    swings: List[SwingPoint],
    side: Side,
    level_tolerance: Decimal = Decimal("0.001"),
) -> Optional[TripleStructure]:
    """
    Try to find a triple top (resistance) or triple bottom (support)
    among the swing highs/lows.

    level_tolerance is a relative tolerance on price similarity,
    e.g. 0.001 = 0.1% around the average.
    """
    if not swings:
        return None

    # Filter swings by type
    if side == "resistance":
        points = [s for s in swings if s.kind == "high"]
        triple_kind: TripleKind = "triple_top"
    else:
        points = [s for s in swings if s.kind == "low"]
        triple_kind = "triple_bottom"

    if len(points) < 3:
        return None

    # Take the 3 most recent points that are close in price
    # (simple heuristic)
    points_sorted = sorted(points, key=lambda s: s.index)
    last_points = points_sorted[-6:]  # search last up to 6

    best: Optional[TripleStructure] = None

    for i in range(len(last_points) - 2):
        p1, p2, p3 = last_points[i : i + 3]
        level_avg = (p1.price + p2.price + p3.price) / 3
        if level_avg == 0:
            continue

        tol = level_tolerance * level_avg
        # All three prices must be within [avg - tol, avg + tol]
        if (
            abs(p1.price - level_avg) <= tol
            and abs(p2.price - level_avg) <= tol
            and abs(p3.price - level_avg) <= tol
        ):
            best = TripleStructure(
                kind=triple_kind,
                level=level_avg,
                swing_indexes=[p1.index, p2.index, p3.index],
            )

    return best


def detect_triple_top(
    candles: List[Candle],
    window: int = 2,
    level_tolerance: Decimal = Decimal("0.001"),
) -> Optional[TripleStructure]:
    """
    Detect Sanzan-like triple top (resistance) structure.
    """
    swings = detect_swings(candles, window=window)
    return _group_triples(swings, side="resistance", level_tolerance=level_tolerance)


def detect_triple_bottom(
    candles: List[Candle],
    window: int = 2,
    level_tolerance: Decimal = Decimal("0.001"),
) -> Optional[TripleStructure]:
    """
    Detect Sansen-like triple bottom (support) structure.
    """
    swings = detect_swings(candles, window=window)
    return _group_triples(swings, side="support", level_tolerance=level_tolerance)
