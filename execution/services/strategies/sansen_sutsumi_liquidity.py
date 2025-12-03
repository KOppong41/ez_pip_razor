from __future__ import annotations

from decimal import Decimal
from typing import List, Optional, Literal

from execution.services.marketdata import Candle
from execution.services.engine import EngineContext, EngineDecision
from execution.services.strategies.engulfing import detect_engulfing
from execution.services.structure import detect_triple_bottom, TripleStructure  # type: ignore
from execution.services.smc import detect_liquidity_sweep  # type: ignore

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
    Very simple trend detector on closes (percentage-based). Used primarily
    on HTF candles to make sure we are not fighting a strong HTF downtrend.
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
    if ctx.htf_candles:
        return _detect_trend_pct(ctx.htf_candles)
    return _detect_trend_pct(ctx.entry_candles)


def run_sansen_sutsumi_liquidity(ctx: EngineContext) -> EngineDecision:
    """
    Sansen + Tsutsumi + Liquidity sweep (bullish):

    Requirements:
      1) Triple bottom structure (Sansen) on entry TF.
      2) Liquidity sweep through the lows (stop-hunt).
      3) Bullish engulfing pattern (Tsutsumi) to confirm reversal.
      4) HTF not in a strong downtrend.
    """
    candles = ctx.entry_candles
    if len(candles) < 10:
        return EngineDecision(
            action="skip",
            direction=None,
            sl=None,
            tp=None,
            reason="not_enough_candles",
            strategy="sansen_sutsumi_liquidity",
            trend="flat",
        )

    atr_val = _atr_like(candles, period=14)
    trend = _get_htf_trend(ctx)

    # 1) Triple bottom structure
    triple: Optional[TripleStructure] = None  # type: ignore[assignment]
    try:
        triple = detect_triple_bottom(candles)
    except TypeError:
        # if your implementation needs parameters, adjust here
        triple = detect_triple_bottom(candles)  # type: ignore[call-arg]

    if not triple:
        return EngineDecision(
            action="skip",
            direction=None,
            sl=None,
            tp=None,
            reason="no_triple_bottom",
            strategy="sansen_sutsumi_liquidity",
            trend=trend,
        )

    # 2) Liquidity sweep through those lows
    swept = False
    try:
        swept = bool(detect_liquidity_sweep(candles))
    except TypeError:
        try:
            swept = bool(detect_liquidity_sweep(candles, "bullish"))  # type: ignore[arg-type]
        except TypeError:
            swept = False

    if not swept:
        return EngineDecision(
            action="skip",
            direction=None,
            sl=None,
            tp=None,
            reason="no_liquidity_sweep",
            strategy="sansen_sutsumi_liquidity",
            trend=trend,
        )

    # 3) Bullish engulfing confirmation on the last two candles
    engulf_decision = detect_engulfing(candles)
    if engulf_decision.action != "open" or engulf_decision.direction != "buy":
        return EngineDecision(
            action="skip",
            direction=None,
            sl=None,
            tp=None,
            reason="no_bullish_engulfing",
            strategy="sansen_sutsumi_liquidity",
            trend=trend,
        )

    # 4) HTF guard-rail: avoid counter-trend in strong HTF downtrend
    if trend == "down":
        return EngineDecision(
            action="skip",
            direction=None,
            sl=None,
            tp=None,
            reason="htf_trend_strong_down",
            strategy="sansen_sutsumi_liquidity",
            trend=trend,
        )

    # Entry / SL / TP
    last = candles[-1]
    entry = last["close"]

    sl = engulf_decision.sl
    if sl is None:
        # Fallback: under recent lows with ATR buffer
        sl = last["low"] - atr_val * Decimal("0.25")

    risk = entry - sl
    if risk <= 0:
        return EngineDecision(
            action="skip",
            direction=None,
            sl=None,
            tp=None,
            reason="invalid_risk",
            strategy="sansen_sutsumi_liquidity",
            trend=trend,
        )

    tp = engulf_decision.tp
    if tp is None:
        tp = entry + risk * Decimal("2")

    return EngineDecision(
        action="open",
        direction="buy",
        sl=sl,
        tp=tp,
        reason="sansen_triple_bottom_with_liquidity_sweep_and_bullish_engulfing",
        strategy="sansen_sutsumi_liquidity",
        trend=trend,
    )
