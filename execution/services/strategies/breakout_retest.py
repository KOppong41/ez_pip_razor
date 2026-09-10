from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from typing import List, Tuple

from execution.services.engine_types import EngineDecision
from execution.services.marketdata import Candle


@dataclass
class BreakoutRetestConfig:
    lookback: int = 40  # bars to define the range
    min_range_pct: Decimal = Decimal("0.0008")  # 0.08% min range width
    retest_tolerance: Decimal = Decimal("0.0012")  # distance to level to count as retest
    min_breakout_body_pct: Decimal = Decimal("0.0003")
    breakout_extension_pct: Decimal = Decimal("0.0003")
    min_breakout_volume: int = 80
    rr: Decimal = Decimal("2")


def _range_levels(candles: List[Candle], lookback: int) -> Tuple[Decimal, Decimal]:
    window = candles[-lookback:]
    highs = [c["high"] for c in window]
    lows = [c["low"] for c in window]
    return max(highs), min(lows)


def _quality_above_minimum(
    value: Decimal,
    minimum: Decimal,
    *,
    strong_multiple: Decimal = Decimal("3"),
) -> Decimal:
    """Return 0.5 at the validity threshold and 1 only when clearly stronger."""
    if minimum <= 0 or value < minimum:
        return Decimal("0")
    span = minimum * (strong_multiple - Decimal("1"))
    progress = min(Decimal("1"), (value - minimum) / span) if span > 0 else Decimal("1")
    return Decimal("0.5") + progress * Decimal("0.5")


def run_breakout_retest(candles: List[Candle], cfg: BreakoutRetestConfig | None = None) -> EngineDecision:
    cfg = cfg or BreakoutRetestConfig()
    if len(candles) < cfg.lookback + 2:
        return EngineDecision(
            action="skip",
            reason="breakout_retest_insufficient_candles",
            strategy="breakout_retest",
            metadata={"reason": "insufficient_candles", "needed": cfg.lookback + 2, "got": len(candles)},
        )

    # The previous candle is the breakout candidate, so the reference range
    # must end before it (the final candle is the retest candidate).
    range_high, range_low = _range_levels(candles[:-2], cfg.lookback)
    range_width = range_high - range_low
    if range_width <= range_low * cfg.min_range_pct:
        return EngineDecision(
            action="skip",
            reason="breakout_retest_too_tight",
            strategy="breakout_retest",
            metadata={"reason": "range_too_tight", "range_width": float(range_width)},
        )

    prev = candles[-2]
    last = candles[-1]

    # Detect breakout on previous bar
    broke_up = prev["close"] > range_high * (Decimal("1") + cfg.breakout_extension_pct)
    broke_down = prev["close"] < range_low * (Decimal("1") - cfg.breakout_extension_pct)

    prev_body = abs(prev["close"] - prev["open"])
    prev_body_pct = (prev_body / prev["open"]) if prev["open"] else Decimal("0")
    if (broke_up or broke_down) and prev_body_pct < cfg.min_breakout_body_pct:
        return EngineDecision(
            action="skip",
            reason="breakout_retest_small_break_candle",
            strategy="breakout_retest",
            metadata={"reason": "small_body", "body_pct": float(prev_body_pct)},
        )

    if (broke_up or broke_down) and prev["tick_volume"] < cfg.min_breakout_volume:
        return EngineDecision(
            action="skip",
            reason="breakout_retest_low_volume",
            strategy="breakout_retest",
            metadata={"reason": "low_volume", "volume": int(prev["tick_volume"])},
        )

    if not broke_up and not broke_down:
        return EngineDecision(action="skip", reason="breakout_retest_no_break", strategy="breakout_retest")

    range_width_pct = range_width / range_low if range_low else Decimal("0")
    range_quality = _quality_above_minimum(
        range_width_pct,
        cfg.min_range_pct,
    )
    body_quality = _quality_above_minimum(
        prev_body_pct,
        cfg.min_breakout_body_pct,
    )
    volume_quality = _quality_above_minimum(
        Decimal(str(prev["tick_volume"])),
        Decimal(str(cfg.min_breakout_volume)),
        strong_multiple=Decimal("2"),
    )
    breakout_extension = (
        (prev["close"] - range_high) / range_high
        if broke_up and range_high
        else (range_low - prev["close"]) / range_low
        if broke_down and range_low
        else Decimal("0")
    )
    extension_quality = _quality_above_minimum(
        breakout_extension,
        cfg.breakout_extension_pct,
    )

    def setup_quality(level_distance: Decimal, tolerance: Decimal):
        retest_quality = max(
            Decimal("0"),
            Decimal("1") - level_distance / tolerance,
        ) if tolerance > 0 else Decimal("1")
        components = {
            "range": range_quality,
            "breakout_body": body_quality,
            "volume": volume_quality,
            "extension": extension_quality,
            "retest": retest_quality,
        }
        score = min(
            Decimal("1"),
            range_quality * Decimal("0.20")
            + body_quality * Decimal("0.25")
            + volume_quality * Decimal("0.15")
            + extension_quality * Decimal("0.20")
            + retest_quality * Decimal("0.20"),
        )
        return score, {key: float(value) for key, value in components.items()}

    if broke_up:
        # Retest current bar into old range high
        level_distance = abs(last["low"] - range_high)
        tolerance = range_high * cfg.retest_tolerance
        near_level = level_distance <= tolerance
        if not near_level or last["close"] < range_high:
            return EngineDecision(action="skip", reason="breakout_retest_no_retest_up", strategy="breakout_retest")
        confidence, score_components = setup_quality(level_distance, tolerance)
        sl = max(range_low, prev["low"])
        risk = last["close"] - sl
        tp = last["close"] + risk * cfg.rr if risk > 0 else None
        return EngineDecision(
            action="open",
            direction="buy",
            sl=sl,
            tp=tp,
            reason="breakout_retest_up",
            strategy="breakout_retest",
            score=float(confidence),
            metadata={
                "confidence": float(confidence),
                "range_width": float(range_width),
                "breakout_body_pct": float(prev_body_pct),
                "breakout_volume": int(prev["tick_volume"]),
                "breakout_extension_pct": float(breakout_extension),
                "score_components": score_components,
            },
        )

    if broke_down:
        level_distance = abs(last["high"] - range_low)
        tolerance = range_low * cfg.retest_tolerance
        near_level = level_distance <= tolerance
        if not near_level or last["close"] > range_low:
            return EngineDecision(action="skip", reason="breakout_retest_no_retest_down", strategy="breakout_retest")
        confidence, score_components = setup_quality(level_distance, tolerance)
        sl = min(range_high, prev["high"])
        risk = sl - last["close"]
        tp = last["close"] - risk * cfg.rr if risk > 0 else None
        return EngineDecision(
            action="open",
            direction="sell",
            sl=sl,
            tp=tp,
            reason="breakout_retest_down",
            strategy="breakout_retest",
            score=float(confidence),
            metadata={
                "confidence": float(confidence),
                "range_width": float(range_width),
                "breakout_body_pct": float(prev_body_pct),
                "breakout_volume": int(prev["tick_volume"]),
                "breakout_extension_pct": float(breakout_extension),
                "score_components": score_components,
            },
        )

    return EngineDecision(action="skip", reason="breakout_retest_no_setup", strategy="breakout_retest")
