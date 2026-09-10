from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from typing import List

from execution.services.engine_types import EngineDecision
from execution.services.marketdata import Candle


@dataclass
class RangeReversionConfig:
    lookback: int = 50
    band_factor: Decimal = Decimal("0.25")  # how close to extremes to trigger
    min_range_pct: Decimal = Decimal("0.0008")
    max_directional_efficiency: Decimal = Decimal("0.55")
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

    closes = [candle["close"] for candle in window]
    path_length = sum(
        (abs(current - previous) for previous, current in zip(closes, closes[1:])),
        Decimal("0"),
    )
    directional_efficiency = (
        abs(closes[-1] - closes[0]) / path_length
        if path_length > 0
        else Decimal("0")
    )
    if directional_efficiency > cfg.max_directional_efficiency:
        return EngineDecision(
            action="skip",
            reason="range_reversion_trending_regime",
            strategy="range_reversion",
            metadata={
                "directional_efficiency": float(directional_efficiency),
                "maximum": float(cfg.max_directional_efficiency),
            },
        )

    last = candles[-1]
    last_close = last["close"]

    upper_band = range_high - width * cfg.band_factor
    lower_band = range_low + width * cfg.band_factor
    width_pct = width / range_low
    range_quality = min(
        Decimal("1"),
        width_pct / (cfg.min_range_pct * Decimal("3")),
    )
    regime_quality = max(
        Decimal("0"),
        Decimal("1")
        - directional_efficiency / max(
            cfg.max_directional_efficiency,
            Decimal("0.000001"),
        ),
    )

    # Fade extremes back to mid
    if last_close >= upper_band:
        sl = range_high
        risk = sl - last_close
        tp = last_close - risk * cfg.rr if risk > 0 else None
        edge_span = width * cfg.band_factor
        proximity = min(
            Decimal("1"),
            max(Decimal("0"), (last_close - upper_band) / edge_span),
        ) if edge_span > 0 else Decimal("0")
        quality = min(
            Decimal("1"),
            range_quality * Decimal("0.5")
            + proximity * Decimal("0.3")
            + regime_quality * Decimal("0.2"),
        )
        return EngineDecision(
            action="open",
            direction="sell",
            sl=sl,
            tp=tp,
            reason="range_reversion_upper",
            strategy="range_reversion",
            score=float(quality),
            metadata={
                "confidence": float(quality),
                "range_width_pct": float(width_pct),
                "directional_efficiency": float(directional_efficiency),
            },
        )

    if last_close <= lower_band:
        sl = range_low
        risk = last_close - sl
        tp = last_close + risk * cfg.rr if risk > 0 else None
        edge_span = width * cfg.band_factor
        proximity = min(
            Decimal("1"),
            max(Decimal("0"), (lower_band - last_close) / edge_span),
        ) if edge_span > 0 else Decimal("0")
        quality = min(
            Decimal("1"),
            range_quality * Decimal("0.5")
            + proximity * Decimal("0.3")
            + regime_quality * Decimal("0.2"),
        )
        return EngineDecision(
            action="open",
            direction="buy",
            sl=sl,
            tp=tp,
            reason="range_reversion_lower",
            strategy="range_reversion",
            score=float(quality),
            metadata={
                "confidence": float(quality),
                "range_width_pct": float(width_pct),
                "directional_efficiency": float(directional_efficiency),
            },
        )

    return EngineDecision(action="skip", reason="range_reversion_mid_range", strategy="range_reversion")
