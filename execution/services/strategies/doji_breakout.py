from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from typing import List, Optional, Literal

from execution.services.marketdata import Candle
from execution.services.engine_types import EngineDecision

PinType = Literal["bullish", "bearish"]


@dataclass
class DojiBreakoutConfig:
    ema_period: int = 20
    lookback_for_levels: int = 80
    atr_period: int = 12
    min_atr_pct: Decimal = Decimal("0.00005")
    wick_level_tolerance_atr: Decimal = Decimal("0.5")
    breakout_buffer_atr: Decimal = Decimal("0.1")
    min_range_atr: Decimal = Decimal("0.05")
    rr: Decimal = Decimal("1.8")
    body_ratio_max: Decimal = Decimal("0.2")  # doji body <= 20% of range
    wick_dom_ratio: Decimal = Decimal("0.4")  # short wick must be <= 40% of long wick


def _ema(values: List[Decimal], period: int) -> List[Decimal]:
    if not values or period <= 0:
        return [Decimal("0")] * len(values)
    k = Decimal("2") / Decimal(str(period + 1))
    ema_vals: List[Decimal] = []
    ema_val = values[0]
    ema_vals.append(ema_val)
    for v in values[1:]:
        ema_val = v * k + ema_val * (Decimal("1") - k)
        ema_vals.append(ema_val)
    return ema_vals


def _atr(candles: List[Candle], period: int) -> Decimal:
    if period <= 0 or len(candles) < period:
        return Decimal("0")
    return sum(
        (candle["high"] - candle["low"] for candle in candles[-period:]),
        Decimal("0"),
    ) / Decimal(period)


def _is_doji(
    c: Candle,
    cfg: DojiBreakoutConfig,
    *,
    min_range: Decimal,
) -> Optional[PinType]:
    high, low, o, cl = c["high"], c["low"], c["open"], c["close"]
    rng = high - low
    if rng <= min_range:
        return None

    body = abs(cl - o)
    if body > rng * cfg.body_ratio_max:
        return None

    upper_wick = high - max(o, cl)
    lower_wick = min(o, cl) - low
    long_wick = max(upper_wick, lower_wick)
    short_wick = min(upper_wick, lower_wick)
    if long_wick <= 0:
        return None
    if short_wick > long_wick * cfg.wick_dom_ratio:
        return None

    if lower_wick == long_wick:
        return "bullish"
    if upper_wick == long_wick:
        return "bearish"
    return None


def _collect_wick_levels(
    candles: List[Candle],
    cfg: DojiBreakoutConfig,
    *,
    min_range: Decimal,
    tolerance: Decimal,
) -> List[Decimal]:
    raw: List[Decimal] = []
    window = candles[-cfg.lookback_for_levels :] if candles else []
    for c in window:
        high, low, o, cl = c["high"], c["low"], c["open"], c["close"]
        rng = high - low
        if rng <= min_range:
            continue
        upper_wick = high - max(o, cl)
        lower_wick = min(o, cl) - low
        if upper_wick >= (Decimal("2") / Decimal("3")) * rng:
            raw.append(high)
        if lower_wick >= (Decimal("2") / Decimal("3")) * rng:
            raw.append(low)

    clustered: List[Decimal] = []
    for lvl in raw:
        if not clustered or all(abs(lvl - x) > tolerance for x in clustered):
            clustered.append(lvl)
    return clustered


def _trend_ok(pin: PinType, ema: List[Decimal], idx: int, close: Decimal) -> bool:
    if idx < 1:
        return False
    now = ema[idx]
    prev = ema[idx - 1]
    if pin == "bullish":
        return close > now and now > prev
    return close < now and now < prev


def _quality_score(
    *,
    doji: Candle,
    breakout: Candle,
    doji_type: PinType,
    ema: List[Decimal],
    ema_index: int,
    atr_price: Decimal,
    level_distance: Decimal,
    level_tolerance: Decimal,
    cfg: DojiBreakoutConfig,
) -> tuple[Decimal, dict[str, float]]:
    """Score already-observed setup quality on a comparable 0..1 scale."""
    doji_range = doji["high"] - doji["low"]
    doji_body = abs(doji["close"] - doji["open"])
    upper_wick = doji["high"] - max(doji["open"], doji["close"])
    lower_wick = min(doji["open"], doji["close"]) - doji["low"]
    long_wick = max(upper_wick, lower_wick)
    short_wick = min(upper_wick, lower_wick)
    doji_quality = max(
        Decimal("0"),
        Decimal("1")
        - doji_body
        / max(doji_range * cfg.body_ratio_max, Decimal("0.00000001")),
    )
    wick_quality = max(
        Decimal("0"),
        Decimal("1")
        - short_wick
        / max(long_wick * cfg.wick_dom_ratio, Decimal("0.00000001")),
    )
    level_quality = max(
        Decimal("0"),
        Decimal("1") - level_distance / max(level_tolerance, Decimal("0.00000001")),
    )
    boundary = doji["high"] if doji_type == "bullish" else doji["low"]
    displacement = abs(breakout["close"] - boundary)
    displacement_quality = min(
        Decimal("1"),
        displacement / max(atr_price * Decimal("0.3"), Decimal("0.00000001")),
    )
    ema_slope = abs(ema[ema_index] - ema[ema_index - 1])
    trend_quality = min(
        Decimal("1"),
        ema_slope / max(atr_price * Decimal("0.1"), Decimal("0.00000001")),
    )
    breakout_body = abs(breakout["close"] - breakout["open"])
    candle_quality = min(
        Decimal("1"),
        breakout_body / max(atr_price * Decimal("0.3"), Decimal("0.00000001")),
    )
    components = {
        "doji": float(doji_quality),
        "wick": float(wick_quality),
        "level": float(level_quality),
        "displacement": float(displacement_quality),
        "trend": float(trend_quality),
        "breakout_candle": float(candle_quality),
    }
    score = min(
        Decimal("1"),
        doji_quality * Decimal("0.20")
        + wick_quality * Decimal("0.15")
        + level_quality * Decimal("0.20")
        + displacement_quality * Decimal("0.25")
        + trend_quality * Decimal("0.10")
        + candle_quality * Decimal("0.10"),
    )
    return score, components


def run_doji_breakout(symbol: str, candles: List[Candle], cfg: Optional[DojiBreakoutConfig] = None) -> EngineDecision:
    """
    Doji breakout with S/R confluence and trend filter:
    - penultimate candle must be a doji on/near S/R
    - trend via EMA slope + location
    - last candle must break the doji high/low by a small buffer
    """
    cfg = cfg or DojiBreakoutConfig()
    if len(candles) < max(cfg.lookback_for_levels + 2, cfg.ema_period + 2):
        return EngineDecision(action="skip", reason="not_enough_candles", strategy="doji_breakout")

    doji = candles[-2]
    last = candles[-1]
    atr_price = _atr(candles[:-1], cfg.atr_period)
    reference_price = abs(doji["close"])
    atr_pct = atr_price / reference_price if reference_price else Decimal("0")
    if atr_price <= 0 or atr_pct < cfg.min_atr_pct:
        return EngineDecision(
            action="skip",
            reason="doji_breakout_low_atr",
            strategy="doji_breakout",
            metadata={
                "atr_pct": float(atr_pct),
                "min_atr_pct": float(cfg.min_atr_pct),
            },
        )
    min_range = atr_price * cfg.min_range_atr
    level_tolerance = atr_price * cfg.wick_level_tolerance_atr
    buffer = atr_price * cfg.breakout_buffer_atr
    closes = [c["close"] for c in candles]
    ema = _ema(closes, cfg.ema_period)
    doji_type = _is_doji(doji, cfg, min_range=min_range)
    if doji_type is None:
        return EngineDecision(action="skip", reason="no_doji", strategy="doji_breakout")

    if not _trend_ok(doji_type, ema, len(candles) - 2, doji["close"]):
        return EngineDecision(action="skip", reason="trend_filter_fail", strategy="doji_breakout")

    levels = _collect_wick_levels(
        candles[:-2],
        cfg,
        min_range=min_range,
        tolerance=level_tolerance,
    )
    wick_price = doji["low"] if doji_type == "bullish" else doji["high"]
    level_distances = [abs(wick_price - level) for level in levels]
    if not level_distances or min(level_distances) > level_tolerance:
        return EngineDecision(action="skip", reason="no_sr_confluence", strategy="doji_breakout")

    doji_high = doji["high"]
    doji_low = doji["low"]
    if doji_type == "bullish":
        if last["close"] <= doji_high + buffer:
            return EngineDecision(action="skip", reason="no_breakout", strategy="doji_breakout")
        entry = last["close"]
        sl = doji_low - buffer
        tp = entry + cfg.rr * (entry - sl)
        direction = "buy"
    else:
        if last["close"] >= doji_low - buffer:
            return EngineDecision(action="skip", reason="no_breakout", strategy="doji_breakout")
        entry = last["close"]
        sl = doji_high + buffer
        tp = entry - cfg.rr * (sl - entry)
        direction = "sell"

    score, score_components = _quality_score(
        doji=doji,
        breakout=last,
        doji_type=doji_type,
        ema=ema,
        ema_index=len(candles) - 2,
        atr_price=atr_price,
        level_distance=min(level_distances),
        level_tolerance=level_tolerance,
        cfg=cfg,
    )
    return EngineDecision(
        action="open",
        direction=direction,
        sl=sl,
        tp=tp,
        reason="doji_breakout_sr_confluence",
        strategy="doji_breakout",
        score=float(score),
        metadata={
            "confidence": float(score),
            "score_components": score_components,
            "atr_pct": float(atr_pct),
            "level_tolerance_atr": float(cfg.wick_level_tolerance_atr),
            "breakout_buffer_atr": float(cfg.breakout_buffer_atr),
        },
    )
