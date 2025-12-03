from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from typing import List

from execution.services.engine import EngineDecision
from execution.services.marketdata import Candle


@dataclass
class RangeReversionConfig:
    lookback: int = 50
    band_factor: Decimal = Decimal("0.25")  # how close to extremes to trigger
    min_range_pct: Decimal = Decimal("0.0008")
    rr: Decimal = Decimal("1.8")


def run_range_reversion(candles: List[Candle], cfg: RangeReversionConfig | None = None) -> EngineDecision:
    cfg = cfg or RangeReversionConfig()
    if len(candles) < cfg.lookback + 1:
        return EngineDecision(action="skip", reason="range_reversion_insufficient_candles", strategy="range_reversion")

    window = candles[-cfg.lookback:]
    highs = [c["high"] for c in window]
    lows = [c["low"] for c in window]
    range_high = max(highs)
    range_low = min(lows)
    width = range_high - range_low

    if range_low == 0 or width <= range_low * cfg.min_range_pct:
        return EngineDecision(action="skip", reason="range_reversion_no_range", strategy="range_reversion")

    last = candles[-1]
    last_close = last["close"]

    upper_band = range_high - width * cfg.band_factor
    lower_band = range_low + width * cfg.band_factor

    # Fade extremes back to mid
    if last_close >= upper_band:
        sl = range_high
        risk = sl - last_close
        tp = last_close - risk * cfg.rr if risk > 0 else None
        return EngineDecision(
            action="open",
            direction="sell",
            sl=sl,
            tp=tp,
            reason="range_reversion_upper",
            strategy="range_reversion",
            score=float(width),
        )

    if last_close <= lower_band:
        sl = range_low
        risk = last_close - sl
        tp = last_close + risk * cfg.rr if risk > 0 else None
        return EngineDecision(
            action="open",
            direction="buy",
            sl=sl,
            tp=tp,
            reason="range_reversion_lower",
            strategy="range_reversion",
            score=float(width),
        )

    return EngineDecision(action="skip", reason="range_reversion_mid_range", strategy="range_reversion")
