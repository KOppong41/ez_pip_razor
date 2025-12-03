from __future__ import annotations

from decimal import Decimal
from typing import List

# Candles expected shape: {"open","high","low","close",...}


def sma(candles: List[dict], period: int = 20) -> Decimal:
    """
    Simple moving average on closes. Returns 0 if not enough data.
    """
    if not candles or len(candles) < period:
        return Decimal("0")
    closes = [Decimal(str(c["close"])) for c in candles[-period:]]
    return sum(closes, Decimal("0")) / Decimal(str(period))


def atr(candles: List[dict], period: int = 14) -> Decimal:
    """
    Basic ATR-like measure using high-low ranges. Returns 0 if not enough data.
    """
    if not candles or len(candles) < period:
        return Decimal("0")
    window = candles[-period:]
    total = sum((Decimal(str(c["high"])) - Decimal(str(c["low"]))) for c in window)
    return total / Decimal(str(period))
