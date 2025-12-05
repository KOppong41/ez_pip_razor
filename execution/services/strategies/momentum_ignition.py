from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from typing import List, Tuple

from execution.services.engine import EngineDecision
from execution.services.marketdata import Candle


@dataclass
class MomentumIgnitionConfig:
    impulse_lookback: int = 5
    # Gold ticks quickly; a 0.1% burst is still meaningful on M1 charts.
    min_impulse_pct: Decimal = Decimal("0.0007")  # 0.07%
    # Allow shallower pullbacks plus a buffer so continuations trigger more often.
    pullback_ratio: Decimal = Decimal("0.75")     # pullback must be <= 75% of impulse
    min_tick_volume: int = 80
    session_hours: Tuple[Tuple[int, int], ...] = ((5, 21),)  # extended UTC windows
    rr: Decimal = Decimal("2.2")


def run_momentum_ignition(candles: List[Candle], cfg: MomentumIgnitionConfig | None = None) -> EngineDecision:
    cfg = cfg or MomentumIgnitionConfig()
    if len(candles) < cfg.impulse_lookback + 2:
        return EngineDecision(
            action="skip",
            reason="momentum_ignition_insufficient_candles",
            strategy="momentum_ignition",
            metadata={"reason": "insufficient_candles", "needed": cfg.impulse_lookback + 2, "got": len(candles)},
        )

    window = candles[-(cfg.impulse_lookback + 1):]
    impulse_section = window[:-1]
    impulse_high = max(c["high"] for c in impulse_section)
    impulse_low = min(c["low"] for c in impulse_section)
    start = impulse_section[0]["close"]
    end = impulse_section[-1]["close"]
    impulse_change = (end - start) / start if start else Decimal("0")
    last = candles[-1]

    last_time = last.get("time")
    if last_time is not None:
        hour = last_time.hour
        in_session = any(start_h <= hour < end_h for start_h, end_h in cfg.session_hours)
        if not in_session:
            return EngineDecision(
                action="skip",
                reason="momentum_ignition_off_session",
                strategy="momentum_ignition",
                metadata={"reason": "session", "hour": hour},
            )

    prev = candles[-2]
    if prev["tick_volume"] < cfg.min_tick_volume:
        return EngineDecision(
            action="skip",
            reason="momentum_ignition_low_volume",
            strategy="momentum_ignition",
            metadata={"reason": "low_volume", "volume": int(prev["tick_volume"])},
        )

    impulse_range = impulse_high - impulse_low
    if impulse_change >= cfg.min_impulse_pct:
        # Bullish impulse, seek shallow pullback (last close not below 40% retrace of impulse)
        retrace = (impulse_high - last["close"])
        max_retrace = impulse_range * cfg.pullback_ratio
        if retrace < 0 or retrace > max_retrace:
            return EngineDecision(
                action="skip",
                reason="momentum_ignition_no_bull_pullback",
                strategy="momentum_ignition",
                metadata={"reason": "pullback", "retrace": float(retrace), "max": float(max_retrace)},
            )
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
            metadata={
                "confidence": float(min(Decimal("1"), impulse_change / cfg.min_impulse_pct)),
                "impulse_pct": float(impulse_change),
                "pullback_pct": float(retrace / impulse_range) if impulse_range else 0.0,
                "impulse_volume": int(prev["tick_volume"]),
            },
        )

    if impulse_change <= -cfg.min_impulse_pct:
        # Bearish impulse
        retrace = (last["close"] - impulse_low)
        max_retrace = impulse_range * cfg.pullback_ratio
        if retrace < 0 or retrace > max_retrace:
            return EngineDecision(
                action="skip",
                reason="momentum_ignition_no_bear_pullback",
                strategy="momentum_ignition",
                metadata={"reason": "pullback", "retrace": float(retrace), "max": float(max_retrace)},
            )
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
            metadata={
                "confidence": float(min(Decimal("1"), abs(impulse_change) / cfg.min_impulse_pct)),
                "impulse_pct": float(abs(impulse_change)),
                "pullback_pct": float(retrace / impulse_range) if impulse_range else 0.0,
                "impulse_volume": int(prev["tick_volume"]),
            },
        )

    return EngineDecision(
        action="skip",
        reason="momentum_ignition_no_impulse",
        strategy="momentum_ignition",
        metadata={"reason": "no_impulse", "impulse_change": float(impulse_change)},
    )
