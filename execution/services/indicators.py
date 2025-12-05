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


def fractals(candles: List[dict], period: int = 2) -> List[dict]:
    """
    Simple Williams fractal detection. Returns a list the same length as candles,
    with entries:
        {"up": True/False, "down": True/False}
    """
    size = len(candles)
    markers = [{"up": False, "down": False} for _ in range(size)]
    if size < period * 2 + 1:
        return markers

    for idx in range(period, size - period):
        window = candles[idx - period : idx + period + 1]
        center = candles[idx]
        highs = [c["high"] for c in window]
        lows = [c["low"] for c in window]
        max_idx = highs.index(max(highs))
        min_idx = lows.index(min(lows))
        if max_idx == period:
            markers[idx]["up"] = True
        if min_idx == period:
            markers[idx]["down"] = True
    return markers
