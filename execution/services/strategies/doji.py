from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from typing import Literal, List, Optional

from execution.services.marketdata import Candle

Action = Literal["open", "skip"]
Direction = Literal["buy", "sell"]


@dataclass
class DojiDecision:
    """
    Simple doji detector.

    NOTE:
    - This is *not* an entry signal on its own.
    - It is meant for tagging candles so composite strategies
      (or analytics/backtests) can react to indecision zones.
    """
    action: Action
    direction: Optional[Direction] = None  # always None for now
    reason: str = ""
    score: float = 0.0  # reserved for future use


def detect_doji(
    candles: List[Candle],
    max_body_ratio: Decimal = Decimal("0.1"),
) -> DojiDecision:
    """
    Detect a basic doji on the last candle.

    Logic:
      - full_range = high - low
      - body = |close - open|
      - if body / full_range <= max_body_ratio -> doji
      - otherwise -> not a doji

    We ALWAYS return action="skip" because doji is informational.
    """
    if not candles:
        return DojiDecision(action="skip", reason="no_candles")

    c = candles[-1]
    o, h, l, cl = c["open"], c["high"], c["low"], c["close"]

    full_range = h - l
    if full_range <= 0:
        return DojiDecision(action="skip", reason="zero_range")

    body = abs(cl - o)
    body_ratio = body / full_range

    if body_ratio <= max_body_ratio:
        return DojiDecision(
            action="skip",
            reason="doji_detected",
        )

    return DojiDecision(action="skip", reason="no_doji")
