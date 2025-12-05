from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from typing import Literal, Optional, List
import logging

from execution.services.marketdata import Candle


Action = Literal["open", "skip", "close"]
Direction = Literal["buy", "sell"]
Trend = Literal["up", "down", "flat"]

logger = logging.getLogger(__name__)


@dataclass
class EngineDecision:
    """
    Unified decision object returned by the internal engine.
    """
    action: Action
    direction: Optional[Direction] = None
    sl: Optional[Decimal] = None
    tp: Optional[Decimal] = None
    reason: str = ""
    strategy: str = ""   
    trend: Trend = "flat"
    score: float = 0.0
    metadata: Optional[dict] = None   


@dataclass
class EngineContext:
    """
    Input for the engine.

    For now:
      - symbol / timeframe: entry timeframe
      - entry_candles: candles on entry TF
      - htf_candles: optional higher TF candles (e.g. 30m when entry is 5m)
      - allowed_strategies: optional whitelist of strategy names
    """
    symbol: str
    timeframe: str
    entry_candles: List[Candle]
    htf_candles: Optional[List[Candle]] = None
    allowed_strategies: Optional[List[str]] = None


# Strategy imports placed after dataclass definitions to avoid circular imports
from execution.services.strategies.harami import detect_harami
from execution.services.strategies.engulfing import detect_engulfing
from execution.services.strategies.hammer import detect_hammer
from execution.services.strategies.marubozu import detect_marubozu
from execution.services.strategies.shooting_star import detect_shooting_star
from execution.services.strategies.three_soldiers import detect_three_soldiers
from execution.services.strategies.trend_pullback import run_trend_pullback
from execution.services.strategies.breakout_retest import run_breakout_retest
from execution.services.strategies.range_reversion import run_range_reversion
from execution.services.strategies.momentum_ignition import run_momentum_ignition
# Lazy imports inside helpers to avoid circulars


def _is_allowed(ctx: EngineContext, strategy_name: str) -> bool:
    if not ctx.allowed_strategies:
        # empty or None = allow all
        return True
    return strategy_name in ctx.allowed_strategies


def _detect_trend(
    candles: List[Candle],
    lookback: int = 20,
    min_change_pct: Decimal = Decimal("0.001"),  # 0.1%
) -> Trend:
    """
    Very simple trend detector on closes:
    - up   if last close is >= min_change_pct above close[0]
    - down if last close is <= -min_change_pct below close[0]
    - flat otherwise
    """
    if len(candles) < lookback + 1:
        return "flat"

    window = candles[-(lookback + 1):]
    first = window[0]["close"]
    last = window[-1]["close"]

    if not first:
        return "flat"

    change_pct = (last - first) / first

    if change_pct >= min_change_pct:
        return "up"
    if change_pct <= -min_change_pct:
        return "down"
    return "flat"


def _harami_candidate(candles: List[Candle], trend: Trend) -> Optional[EngineDecision]:
    h = detect_harami(candles)
    if h.action != "open" or not h.direction:
        return None

    return EngineDecision(
        action="open",
        direction=h.direction,
        sl=h.sl,
        tp=h.tp,
        reason=h.reason,
        strategy="harami",
        trend=trend,
        score=h.score,
    )

def _engulfing_candidate(candles: List[Candle], trend: Trend) -> Optional[EngineDecision]:
    e = detect_engulfing(candles)
    if e.action != "open" or not e.direction:
        return None

    return EngineDecision(
        action="open",
        direction=e.direction,
        sl=e.sl,
        tp=e.tp,
        reason=e.reason,
        strategy="engulfing",
        trend=trend,
        score=e.score,
    )


def _hammer_candidate(candles: List[Candle], trend: Trend) -> Optional[EngineDecision]:
    h = detect_hammer(candles)
    if h.action != "open" or not h.direction:
        return None
    return EngineDecision(
        action="open",
        direction=h.direction,
        sl=h.sl,
        tp=h.tp,
        reason=h.reason,
        strategy="hammer",
        trend=trend,
        score=h.score,
    )


def _marubozu_candidate(candles: List[Candle], trend: Trend) -> Optional[EngineDecision]:
    m = detect_marubozu(candles)
    if m.action != "open" or not m.direction:
        return None
    return EngineDecision(
        action="open",
        direction=m.direction,
        sl=m.sl,
        tp=m.tp,
        reason=m.reason,
        strategy="marubozu",
        trend=trend,
        score=m.score,
    )


def _shooting_star_candidate(candles: List[Candle], trend: Trend) -> Optional[EngineDecision]:
    s = detect_shooting_star(candles)
    if s.action != "open" or not s.direction:
        return None
    return EngineDecision(
        action="open",
        direction=s.direction,
        sl=s.sl,
        tp=s.tp,
        reason=s.reason,
        strategy="shooting_star",
        trend=trend,
        score=s.score,
    )


def _three_soldiers_candidate(candles: List[Candle], trend: Trend) -> Optional[EngineDecision]:
    t = detect_three_soldiers(candles)
    if t.action != "open" or not t.direction:
        return None
    return EngineDecision(
        action="open",
        direction=t.direction,
        sl=t.sl,
        tp=t.tp,
        reason=t.reason,
        strategy="three_soldiers",
        trend=trend,
        score=t.score,
    )


def _price_action_pinbar_candidate(ctx: EngineContext, candles: List[Candle], trend: Trend) -> Optional[EngineDecision]:
    from execution.services.strategies.price_action_pinbar import run_price_action_pinbar
    dec = run_price_action_pinbar(ctx.symbol, candles)
    if dec.action != "open" or not dec.direction:
        return None
    dec.trend = trend
    return dec


def _doji_breakout_candidate(ctx: EngineContext, candles: List[Candle], trend: Trend) -> Optional[EngineDecision]:
    from execution.services.strategies.doji_breakout import run_doji_breakout
    dec = run_doji_breakout(ctx.symbol, candles)
    if dec.action != "open" or not dec.direction:
        return None
    dec.trend = trend
    return dec


def _trend_pullback_candidate(candles: List[Candle], trend: Trend) -> Optional[EngineDecision]:
    dec = run_trend_pullback(candles)
    if dec.action != "open" or not dec.direction:
        return None
    dec.trend = trend
    return dec


def _breakout_retest_candidate(candles: List[Candle], trend: Trend) -> Optional[EngineDecision]:
    dec = run_breakout_retest(candles)
    if dec.action != "open" or not dec.direction:
        return None
    dec.trend = trend
    return dec


def _range_reversion_candidate(candles: List[Candle], trend: Trend) -> Optional[EngineDecision]:
    dec = run_range_reversion(candles)
    if dec.action != "open" or not dec.direction:
        return None
    dec.trend = trend
    return dec


def _momentum_ignition_candidate(candles: List[Candle], trend: Trend) -> Optional[EngineDecision]:
    dec = run_momentum_ignition(candles)
    if dec.action != "open" or not dec.direction:
        return None
    dec.trend = trend
    return dec


def run_engine(ctx: EngineContext) -> EngineDecision:
    from execution.services.strategies.sansen_sutsumi_liquidity import (
        run_sansen_sutsumi_liquidity,
    )
    from execution.services.strategies.sanpe_tonkachi_fvg import (
        run_sanpe_tonkachi_fvg,
    )
    """
    Engine v2 – multi-strategy:

    Candidates:
      - Harami (harami)
      - Engulfing (engulfing)
      - Hammer (hammer)
      - Marubozu (marubozu)
      - Shooting star (shooting_star)
      - Three soldiers (three_soldiers)
      - Sansen + Tsutsumi + liquidity sweep (sansen_sutsumi_liquidity)
      - Sanpe age + Tonkachi + FVG (sanpe_tonkachi_fvg)
      - Price action pin bar (price_action_pinbar)
      - Doji breakout (doji_breakout)
      - Trend pullback (trend_pullback)
      - Breakout + retest (breakout_retest)
      - Range reversion (range_reversion)
      - Momentum ignition (momentum_ignition)

    Logic:
      1) Build all allowed candidates that want to 'open' (doji is informational).
      2) If none -> skip.
      3) If directions conflict -> skip (be conservative).
      4) Otherwise pick by priority:
         sansen_sutsumi_liquidity > sanpe_tonkachi_fvg > three_soldiers > shooting_star
         > hammer > marubozu > engulfing > harami
         If none of the priority names match, fall back to highest score.
    """
    candles = ctx.entry_candles
    if not candles:
        return EngineDecision(
            action="skip",
            reason="no_candles",
            strategy="engine_v2",
            trend="flat",
        )

    trend = _detect_trend(candles)
    candidates: List[EngineDecision] = []

    # Harami
    if _is_allowed(ctx, "harami"):
        harami_cand = _harami_candidate(candles, trend)
        if harami_cand is not None:
            candidates.append(harami_cand)

    # Engulfing
    if _is_allowed(ctx, "engulfing"):
        engulf_cand = _engulfing_candidate(candles, trend)
        if engulf_cand is not None:
            candidates.append(engulf_cand)

    # Hammer
    if _is_allowed(ctx, "hammer"):
        ham_cand = _hammer_candidate(candles, trend)
        if ham_cand is not None:
            candidates.append(ham_cand)

    # Marubozu
    if _is_allowed(ctx, "marubozu"):
        maru_cand = _marubozu_candidate(candles, trend)
        if maru_cand is not None:
            candidates.append(maru_cand)

    # Shooting star
    if _is_allowed(ctx, "shooting_star"):
        ss_cand = _shooting_star_candidate(candles, trend)
        if ss_cand is not None:
            candidates.append(ss_cand)

    # Three soldiers
    if _is_allowed(ctx, "three_soldiers"):
        ts_cand = _three_soldiers_candidate(candles, trend)
        if ts_cand is not None:
            candidates.append(ts_cand)

    # Price action pin bar
    if _is_allowed(ctx, "price_action_pinbar"):
        pa_cand = _price_action_pinbar_candidate(ctx, candles, trend)
        if pa_cand is not None:
            candidates.append(pa_cand)

    # Doji breakout (tradable)
    if _is_allowed(ctx, "doji_breakout"):
        db_cand = _doji_breakout_candidate(ctx, candles, trend)
        if db_cand is not None:
            candidates.append(db_cand)

    # Trend pullback
    if _is_allowed(ctx, "trend_pullback"):
        tp_cand = _trend_pullback_candidate(candles, trend)
        if tp_cand is not None:
            candidates.append(tp_cand)

    # Breakout + retest
    if _is_allowed(ctx, "breakout_retest"):
        br_cand = _breakout_retest_candidate(candles, trend)
        if br_cand is not None:
            candidates.append(br_cand)

    # Range reversion
    if _is_allowed(ctx, "range_reversion"):
        rr_cand = _range_reversion_candidate(candles, trend)
        if rr_cand is not None:
            candidates.append(rr_cand)

    # Momentum ignition
    if _is_allowed(ctx, "momentum_ignition"):
        mi_cand = _momentum_ignition_candidate(candles, trend)
        if mi_cand is not None:
            candidates.append(mi_cand)

    # Sansen + liquidity
    if _is_allowed(ctx, "sansen_sutsumi_liquidity"):
        s1 = run_sansen_sutsumi_liquidity(ctx)
        if s1.action == "open" and s1.direction:
            # ensure strategy name/trend populated consistently
            if not s1.strategy:
                s1.strategy = "sansen_sutsumi_liquidity"
            s1.trend = trend
            candidates.append(s1)

    # Sanpe + FVG
    if _is_allowed(ctx, "sanpe_tonkachi_fvg"):
        s2 = run_sanpe_tonkachi_fvg(ctx)
        if s2.action == "open" and s2.direction:
            if not s2.strategy:
                s2.strategy = "sanpe_tonkachi_fvg"
            s2.trend = trend
            candidates.append(s2)

    # 1) No candidates
    if not candidates:
        logger.info(
            "[Engine] skip: no_strategy_signal symbol=%s tf=%s allowed=%s trend=%s",
            ctx.symbol,
            ctx.timeframe,
            ctx.allowed_strategies,
            trend,
        )
        return EngineDecision(
            action="skip",
            reason="no_strategy_signal",
            strategy="engine_v2",
            trend=trend,
        )

    # 2) Direction conflict between strategies -> skip (safer than fighting)
    directions = {c.direction for c in candidates if c.direction is not None}
    if len(directions) > 1:
        logger.info(
            "[Engine] skip: direction_conflict symbol=%s tf=%s candidates=%s",
            ctx.symbol,
            ctx.timeframe,
            [(c.strategy, c.direction, c.score) for c in candidates],
        )
        return EngineDecision(
            action="skip",
            reason="multi_strategy_direction_conflict",
            strategy="engine_v2",
            trend=trend,
        )

    # 3) Choose by priority first
    priority = [
        "sansen_sutsumi_liquidity",
        "sanpe_tonkachi_fvg",
        "momentum_ignition",
        "trend_pullback",
        "breakout_retest",
        "range_reversion",
        "price_action_pinbar",
        "doji_breakout",
        "three_soldiers",
        "shooting_star",
        "hammer",
        "marubozu",
        "engulfing",
        "harami",
    ]

    by_strategy = {c.strategy: c for c in candidates if c.strategy}
    for name in priority:
        if name in by_strategy:
            chosen = by_strategy[name]
            chosen.trend = trend
            return chosen

    # 4) Fallback: choose highest score, else first
    chosen = max(candidates, key=lambda c: c.score or 0.0)
    chosen.trend = trend
    return chosen


def run_engine_on_candles(candles: List[Candle]) -> EngineDecision:
    """
    Backwards-compatible helper for legacy calls.

    Uses empty symbol/timeframe and no HTF.
    Prefer run_engine(EngineContext) going forward.
    """
    ctx = EngineContext(
        symbol="",
        timeframe="",
        entry_candles=candles,
        htf_candles=None,
    )
    return run_engine(ctx)
