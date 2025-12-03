from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from typing import List, Tuple

from execution.services.engine import EngineDecision
from execution.services.marketdata import Candle


@dataclass
class BreakoutRetestConfig:
    lookback: int = 40  # bars to define the range
    min_range_pct: Decimal = Decimal("0.001")  # 0.1% min range width
    retest_tolerance: Decimal = Decimal("0.0008")  # distance to level to count as retest
    rr: Decimal = Decimal("2")


def _range_levels(candles: List[Candle], lookback: int) -> Tuple[Decimal, Decimal]:
    window = candles[-lookback:]
    highs = [c["high"] for c in window]
    lows = [c["low"] for c in window]
    return max(highs), min(lows)


def run_breakout_retest(candles: List[Candle], cfg: BreakoutRetestConfig | None = None) -> EngineDecision:
    cfg = cfg or BreakoutRetestConfig()
    if len(candles) < cfg.lookback + 2:
        return EngineDecision(action="skip", reason="breakout_retest_insufficient_candles", strategy="breakout_retest")

    range_high, range_low = _range_levels(candles[:-1], cfg.lookback)  # exclude last bar for breakout detection
    range_width = range_high - range_low
    if range_width <= range_low * cfg.min_range_pct:
        return EngineDecision(action="skip", reason="breakout_retest_too_tight", strategy="breakout_retest")

    prev = candles[-2]
    last = candles[-1]

    # Detect breakout on previous bar
    broke_up = prev["close"] > range_high
    broke_down = prev["close"] < range_low

    if not broke_up and not broke_down:
        return EngineDecision(action="skip", reason="breakout_retest_no_break", strategy="breakout_retest")

    if broke_up:
        # Retest current bar into old range high
        near_level = abs(last["low"] - range_high) <= range_high * cfg.retest_tolerance
        if not near_level or last["close"] < range_high:
            return EngineDecision(action="skip", reason="breakout_retest_no_retest_up", strategy="breakout_retest")
        sl = range_low  # conservative under range
        risk = last["close"] - sl
        tp = last["close"] + risk * cfg.rr if risk > 0 else None
        return EngineDecision(
            action="open",
            direction="buy",
            sl=sl,
            tp=tp,
            reason="breakout_retest_up",
            strategy="breakout_retest",
            score=float(range_width),
        )

    if broke_down:
        near_level = abs(last["high"] - range_low) <= range_low * cfg.retest_tolerance
        if not near_level or last["close"] > range_low:
            return EngineDecision(action="skip", reason="breakout_retest_no_retest_down", strategy="breakout_retest")
        sl = range_high
        risk = sl - last["close"]
        tp = last["close"] - risk * cfg.rr if risk > 0 else None
        return EngineDecision(
            action="open",
            direction="sell",
            sl=sl,
            tp=tp,
            reason="breakout_retest_down",
            strategy="breakout_retest",
            score=float(range_width),
        )

    return EngineDecision(action="skip", reason="breakout_retest_no_setup", strategy="breakout_retest")
