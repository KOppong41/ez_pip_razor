from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from typing import List

from execution.services.engine_types import EngineDecision
from execution.services.marketdata import Candle
from execution.services.indicators import fractals


@dataclass
class TrendPullbackConfig:
    ema_period: int = 20
    slope_lookback: int = 5
    min_trend_slope_pct: Decimal = Decimal("0.00005")
    atr_period: int = 12
    min_atr_pct: Decimal = Decimal("0.00005")
    pullback_atr_multiple: Decimal = Decimal("0.85")
    wick_rejection_ratio: Decimal = Decimal("1.2")
    fractal_period: int = 2
    require_fractal_confirmation: bool = True
    rr: Decimal = Decimal("2")


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
    if not candles or len(candles) < period:
        return Decimal("0")
    window = candles[-period:]
    total = sum((c["high"] - c["low"] for c in window), Decimal("0"))
    if period <= 0:
        return Decimal("0")
    return total / Decimal(str(period))


def run_trend_pullback(candles: List[Candle], cfg: TrendPullbackConfig | None = None) -> EngineDecision:
    cfg = cfg or TrendPullbackConfig()
    if len(candles) < cfg.ema_period + 2:
        return EngineDecision(
            action="skip",
            reason="trend_pullback_insufficient_candles",
            strategy="trend_pullback",
            metadata={"reason": "insufficient_candles", "needed": cfg.ema_period + 2, "got": len(candles)},
        )

    closes = [c["close"] for c in candles]
    emas = _ema(closes, cfg.ema_period)
    fractal_markers = fractals(candles, period=cfg.fractal_period)
    last = candles[-1]
    last_close = last["close"]
    last_low = last["low"]
    last_high = last["high"]
    ema_now = emas[-1]

    # Simple slope check: EMA rising/falling over last 5 bars
    lookback = min(cfg.slope_lookback, len(emas) - 1)
    ema_prev = emas[-(lookback + 1)]
    slope = ema_now - ema_prev
    slope_pct = (slope / last_close) if last_close else Decimal("0")

    atr_price = _atr(candles, cfg.atr_period)
    atr_pct = atr_price / abs(last_close) if last_close else Decimal("0")
    if atr_price <= 0 or atr_pct < cfg.min_atr_pct:
        return EngineDecision(
            action="skip",
            reason="trend_pullback_low_atr",
            strategy="trend_pullback",
            metadata={"reason": "low_atr", "atr_pct": float(atr_pct), "min_atr_pct": float(cfg.min_atr_pct)},
        )

    bull_trend = slope_pct > cfg.min_trend_slope_pct and last_close > ema_now
    bear_trend = slope_pct < -cfg.min_trend_slope_pct and last_close < ema_now

    if not (bull_trend or bear_trend):
        return EngineDecision(
            action="skip",
            reason="trend_pullback_no_trend",
            strategy="trend_pullback",
            metadata={"reason": "no_trend", "slope_pct": float(slope_pct)},
        )

    # Pullback check: price near EMA measured in ATR multiples
    dist_price = abs(last_close - ema_now)
    if dist_price > atr_price * cfg.pullback_atr_multiple:
        return EngineDecision(
            action="skip",
            reason="trend_pullback_not_at_ema",
            strategy="trend_pullback",
            metadata={"reason": "distance", "distance_price": float(dist_price), "allowed": float(atr_price * cfg.pullback_atr_multiple)},
        )

    def _rejection_ratio() -> Decimal:
        body = abs(last["close"] - last["open"])
        upper_wick = last_high - max(last["open"], last_close)
        lower_wick = min(last["open"], last_close) - last_low
        wick = upper_wick if bear_trend else lower_wick
        if body == 0:
            return Decimal("0")
        return (wick / body) if body else Decimal("0")

    rejection = _rejection_ratio()
    if rejection < cfg.wick_rejection_ratio:
        return EngineDecision(
            action="skip",
            reason="trend_pullback_no_rejection",
            strategy="trend_pullback",
            metadata={"reason": "rejection", "ratio": float(rejection), "min": float(cfg.wick_rejection_ratio)},
        )

    if cfg.require_fractal_confirmation:
        # A fractal is only confirmed after ``period`` later candles close, so
        # the newest usable marker is period+1 bars from the end—not the last
        # candle, whose marker is necessarily unconfirmed.
        confirmed_index = len(fractal_markers) - cfg.fractal_period - 1
        if confirmed_index < 0:
            return EngineDecision(
                action="skip",
                reason="trend_pullback_no_fractal",
                strategy="trend_pullback",
                metadata={"reason": "fractal_unconfirmed"},
            )
        latest_confirmed = fractal_markers[confirmed_index]
        # Indicator semantics: ``down`` marks a local low and ``up`` marks a
        # local high. Bullish pullbacks confirm against a swing low; bearish
        # pullbacks confirm against a swing high.
        if bull_trend and not latest_confirmed.get("down"):
            return EngineDecision(
                action="skip",
                reason="trend_pullback_no_fractal",
                strategy="trend_pullback",
                metadata={"reason": "fractal_missing", "direction": "buy"},
            )
        if bear_trend and not latest_confirmed.get("up"):
            return EngineDecision(
                action="skip",
                reason="trend_pullback_no_fractal",
                strategy="trend_pullback",
                metadata={"reason": "fractal_missing", "direction": "sell"},
            )

    confidence = min(
        Decimal("1"),
        max(Decimal("0"), (abs(slope_pct) / cfg.min_trend_slope_pct) * Decimal("0.5"))
        + max(Decimal("0"), (atr_pct - cfg.min_atr_pct) / (cfg.min_atr_pct + Decimal("0.000001"))) * Decimal("0.2")
        + max(Decimal("0"), (cfg.pullback_atr_multiple - (dist_price / atr_price)) * Decimal("0.3")),
    )

    if bull_trend:
        sl = last_low  # conservative: under current bar
        risk = last_close - sl
        tp = last_close + risk * cfg.rr if risk > 0 else None
        return EngineDecision(
            action="open",
            direction="buy",
            sl=sl,
            tp=tp,
            reason="trend_pullback_bull",
            strategy="trend_pullback",
            score=float(confidence),
            metadata={
                "confidence": float(confidence),
                "slope_pct": float(slope_pct),
                "atr_pct": float(atr_pct),
                "pullback_atr": float(dist_price / atr_price),
                "rejection_ratio": float(rejection),
            },
        )
    else:
        sl = last_high
        risk = sl - last_close
        tp = last_close - risk * cfg.rr if risk > 0 else None
        return EngineDecision(
            action="open",
            direction="sell",
            sl=sl,
            tp=tp,
            reason="trend_pullback_bear",
            strategy="trend_pullback",
            score=float(confidence),
            metadata={
                "confidence": float(confidence),
                "slope_pct": float(abs(slope_pct)),
                "atr_pct": float(atr_pct),
                "pullback_atr": float(dist_price / atr_price),
                "rejection_ratio": float(rejection),
            },
        )
