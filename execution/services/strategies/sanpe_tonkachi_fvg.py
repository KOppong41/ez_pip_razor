from __future__ import annotations

from decimal import Decimal
from typing import List, Optional, Literal

from execution.services.marketdata import Candle
from execution.services.engine import EngineContext, EngineDecision
from execution.services.strategies.three_soldiers import detect_three_soldiers
from execution.services.strategies.hammer import detect_hammer
from execution.services.smc import last_fvg, FVGZone  # type: ignore

Trend = Literal["up", "down", "flat"]


def _atr_like(candles: List[Candle], period: int = 14) -> Decimal:
    """Simple ATR-like (mean of high-low)."""
    if len(candles) < period:
        return Decimal("0")
    window = candles[-period:]
    total = sum((c["high"] - c["low"] for c in window), Decimal("0"))
    return total / Decimal(str(period))


def _detect_trend_pct(
    candles: List[Candle],
    lookback: int = 20,
    min_change_pct: Decimal = Decimal("0.001"),  # 0.1%
) -> Trend:
    """
    Very simple trend detector on closes (percentage-based).

    - up   if last close >= first close * (1 + min_change_pct)
    - down if last close <= first close * (1 - min_change_pct)
    - flat otherwise
    """
    if len(candles) < lookback + 1:
        return "flat"

    window = candles[-(lookback + 1):]
    first = window[0]["close"]
    last = window[-1]["close"]

    if first == 0:
        return "flat"

    change_pct = (last - first) / first

    if change_pct >= min_change_pct:
        return "up"
    if change_pct <= -min_change_pct:
        return "down"
    return "flat"


def _get_htf_trend(ctx: EngineContext) -> Trend:
    """
    Prefer HTF candles for trend; if missing, fall back to entry TF.
    """
    if ctx.htf_candles:
        return _detect_trend_pct(ctx.htf_candles)
    return _detect_trend_pct(ctx.entry_candles)


def run_sanpe_tonkachi_fvg(ctx: EngineContext) -> EngineDecision:
    """
    Sanpe + Tonkachi + FVG (bullish):

    Requirements:
      1) Bullish three white soldiers on entry TF (momentum).
      2) Recent bullish FVG behind price (smart money context).
      3) Bullish hammer confirmation on the latest candle.
      4) HTF trend not strongly down.
    """
    candles = ctx.entry_candles
    if len(candles) < 3:
        return EngineDecision(
            action="skip",
            direction=None,
            sl=None,
            tp=None,
            reason="not_enough_candles",
            strategy="sanpe_tonkachi_fvg",
            trend="flat",
        )

    atr_val = _atr_like(candles, period=14)
    trend = _get_htf_trend(ctx)

    # 1) Bullish three soldiers
    ts_decision = detect_three_soldiers(candles)
    if ts_decision.action != "open" or ts_decision.direction != "buy":
        return EngineDecision(
            action="skip",
            direction=None,
            sl=None,
            tp=None,
            reason="no_bullish_three_soldiers",
            strategy="sanpe_tonkachi_fvg",
            trend=trend,
        )

    # 2) Recent bullish FVG behind price
    fvg: Optional[FVGZone] = None  # type: ignore[assignment]
    try:
        # simplest possible signature; we don't rely on its attributes
        fvg = last_fvg(candles)
    except TypeError:
        # if your implementation expects direction, this fallback may help
        try:
            fvg = last_fvg(candles, "bullish")  # type: ignore[arg-type]
        except TypeError:
            fvg = None

    if fvg is None:
        return EngineDecision(
            action="skip",
            direction=None,
            sl=None,
            tp=None,
            reason="no_bullish_fvg",
            strategy="sanpe_tonkachi_fvg",
            trend=trend,
        )

    # 3) Hammer confirmation on the most recent candle
    hammer_decision = detect_hammer(candles)
    if hammer_decision.action != "open" or hammer_decision.direction != "buy":
        return EngineDecision(
            action="skip",
            direction=None,
            sl=None,
            tp=None,
            reason="no_bullish_hammer_confirmation",
            strategy="sanpe_tonkachi_fvg",
            trend=trend,
        )

    # 4) HTF guard-rail: avoid trading hard against a strong downtrend
    if trend == "down":
        return EngineDecision(
            action="skip",
            direction=None,
            sl=None,
            tp=None,
            reason="htf_trend_down",
            strategy="sanpe_tonkachi_fvg",
            trend=trend,
        )

    # Entry / SL / TP
    last = candles[-1]
    entry = last["close"]

    sl = hammer_decision.sl
    if sl is None:
        # Fallback SL just under the recent low + small ATR buffer
        sl = last["low"] - atr_val * Decimal("0.25")

    risk = entry - sl
    if risk <= 0:
        return EngineDecision(
            action="skip",
            direction=None,
            sl=None,
            tp=None,
            reason="invalid_risk",
            strategy="sanpe_tonkachi_fvg",
            trend=trend,
        )

    tp = entry + risk * Decimal("2")

    return EngineDecision(
        action="open",
        direction="buy",
        sl=sl,
        tp=tp,
        reason="sanpe_three_soldiers_with_fvg_and_hammer_confirmation",
        strategy="sanpe_tonkachi_fvg",
        trend=trend,
    )
