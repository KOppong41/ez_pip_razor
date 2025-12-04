from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from typing import List

from execution.services.engine import EngineDecision
from execution.services.marketdata import Candle


@dataclass
class MomentumIgnitionConfig:
    impulse_lookback: int = 5
    # Gold ticks quickly; a 0.1% burst is still meaningful on M1 charts.
    min_impulse_pct: Decimal = Decimal("0.001")  # 0.1%
    # Allow shallower pullbacks plus a buffer so continuations trigger more often.
    pullback_ratio: Decimal = Decimal("0.65")     # pullback must be <= 65% of impulse
    rr: Decimal = Decimal("2.2")


def run_momentum_ignition(candles: List[Candle], cfg: MomentumIgnitionConfig | None = None) -> EngineDecision:
    cfg = cfg or MomentumIgnitionConfig()
    if len(candles) < cfg.impulse_lookback + 2:
        return EngineDecision(action="skip", reason="momentum_ignition_insufficient_candles", strategy="momentum_ignition")

    window = candles[-cfg.impulse_lookback - 1 :]
    start = window[0]["close"]
    end = window[-2]["close"]  # impulse candle
    impulse_change = (end - start) / start if start else Decimal("0")

    last = candles[-1]

    if impulse_change >= cfg.min_impulse_pct:
        # Bullish impulse, seek shallow pullback (last close not below 40% retrace of impulse)
        retrace = (end - last["close"]) if end else Decimal("0")
        max_retrace = (end - start) * cfg.pullback_ratio
        if retrace < 0 or retrace > max_retrace:
            return EngineDecision(action="skip", reason="momentum_ignition_no_bull_pullback", strategy="momentum_ignition")
        sl = last["low"]
        risk = end - sl
        tp = end + risk * cfg.rr if risk > 0 else None
        return EngineDecision(
            action="open",
            direction="buy",
            sl=sl,
            tp=tp,
            reason="momentum_ignition_bull",
            strategy="momentum_ignition",
            score=float(impulse_change),
        )

    if impulse_change <= -cfg.min_impulse_pct:
        # Bearish impulse
        retrace = (last["close"] - end)
        max_retrace = (start - end) * cfg.pullback_ratio
        if retrace < 0 or retrace > max_retrace:
            return EngineDecision(action="skip", reason="momentum_ignition_no_bear_pullback", strategy="momentum_ignition")
        sl = last["high"]
        risk = sl - end
        tp = end - risk * cfg.rr if risk > 0 else None
        return EngineDecision(
            action="open",
            direction="sell",
            sl=sl,
            tp=tp,
            reason="momentum_ignition_bear",
            strategy="momentum_ignition",
            score=float(abs(impulse_change)),
        )

    return EngineDecision(action="skip", reason="momentum_ignition_no_impulse", strategy="momentum_ignition")
