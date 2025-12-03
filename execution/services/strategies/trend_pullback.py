from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from typing import List

from execution.services.engine import EngineDecision
from execution.services.marketdata import Candle


@dataclass
class TrendPullbackConfig:
    ema_period: int = 20
    min_trend_slope: Decimal = Decimal("0.0001")
    pullback_tolerance: Decimal = Decimal("0.001")  # 0.1% distance to EMA
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


def run_trend_pullback(candles: List[Candle], cfg: TrendPullbackConfig | None = None) -> EngineDecision:
    cfg = cfg or TrendPullbackConfig()
    if len(candles) < cfg.ema_period + 2:
        return EngineDecision(action="skip", reason="trend_pullback_insufficient_candles", strategy="trend_pullback")

    closes = [c["close"] for c in candles]
    emas = _ema(closes, cfg.ema_period)
    last = candles[-1]
    last_close = last["close"]
    last_low = last["low"]
    last_high = last["high"]
    ema_now = emas[-1]

    # Simple slope check: EMA rising/falling over last 5 bars
    lookback = min(5, len(emas) - 1)
    ema_prev = emas[-lookback]
    slope = ema_now - ema_prev

    bull_trend = slope > cfg.min_trend_slope and last_close > ema_now
    bear_trend = slope < -cfg.min_trend_slope and last_close < ema_now

    if not (bull_trend or bear_trend):
        return EngineDecision(action="skip", reason="trend_pullback_no_trend", strategy="trend_pullback")

    # Pullback check: price near EMA
    dist_pct = abs(last_close - ema_now) / last_close if last_close else Decimal("1")
    if dist_pct > cfg.pullback_tolerance:
        return EngineDecision(action="skip", reason="trend_pullback_not_at_ema", strategy="trend_pullback")

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
            score=float(slope),
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
            score=float(abs(slope)),
        )
