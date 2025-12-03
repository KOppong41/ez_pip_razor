from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from typing import List, Optional, Literal

from execution.services.marketdata import Candle
from execution.services.engine import EngineDecision  # circular-safe: only the dataclass is used here

PinType = Literal["bullish", "bearish"]


@dataclass
class DojiBreakoutConfig:
    ema_period: int = 20
    lookback_for_levels: int = 80
    wick_level_tolerance: Decimal = Decimal("0.0005")
    breakout_buffer: Decimal = Decimal("0.0001")
    rr: Decimal = Decimal("1.8")
    min_range: Decimal = Decimal("0.00001")
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


def _is_doji(c: Candle, cfg: DojiBreakoutConfig) -> Optional[PinType]:
    high, low, o, cl = c["high"], c["low"], c["open"], c["close"]
    rng = high - low
    if rng <= cfg.min_range:
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


def _collect_wick_levels(candles: List[Candle], cfg: DojiBreakoutConfig) -> List[Decimal]:
    raw: List[Decimal] = []
    window = candles[-cfg.lookback_for_levels :] if candles else []
    for c in window:
        high, low, o, cl = c["high"], c["low"], c["open"], c["close"]
        rng = high - low
        if rng <= cfg.min_range:
            continue
        upper_wick = high - max(o, cl)
        lower_wick = min(o, cl) - low
        if upper_wick >= (Decimal("2") / Decimal("3")) * rng:
            raw.append(high)
        if lower_wick >= (Decimal("2") / Decimal("3")) * rng:
            raw.append(low)

    clustered: List[Decimal] = []
    for lvl in raw:
        if not clustered or all(abs(lvl - x) > cfg.wick_level_tolerance for x in clustered):
            clustered.append(lvl)
    return clustered


def _has_level(pin: PinType, doji: Candle, levels: List[Decimal], cfg: DojiBreakoutConfig) -> bool:
    if not levels:
        return False
    wick_price = doji["low"] if pin == "bullish" else doji["high"]
    return any(abs(wick_price - lvl) <= cfg.wick_level_tolerance for lvl in levels)


def _trend_ok(pin: PinType, ema: List[Decimal], idx: int, close: Decimal) -> bool:
    if idx < 1:
        return False
    now = ema[idx]
    prev = ema[idx - 1]
    if pin == "bullish":
        return close > now and now > prev
    return close < now and now < prev


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
    closes = [c["close"] for c in candles]
    ema = _ema(closes, cfg.ema_period)
    doji_type = _is_doji(doji, cfg)
    if doji_type is None:
        return EngineDecision(action="skip", reason="no_doji", strategy="doji_breakout")

    if not _trend_ok(doji_type, ema, len(candles) - 2, doji["close"]):
        return EngineDecision(action="skip", reason="trend_filter_fail", strategy="doji_breakout")

    levels = _collect_wick_levels(candles[:-2], cfg)
    if not _has_level(doji_type, doji, levels, cfg):
        return EngineDecision(action="skip", reason="no_sr_confluence", strategy="doji_breakout")

    doji_high = doji["high"]
    doji_low = doji["low"]
    buffer = cfg.breakout_buffer

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

    return EngineDecision(
        action="open",
        direction=direction,
        sl=sl,
        tp=tp,
        reason="doji_breakout_sr_confluence",
        strategy="doji_breakout",
        score=0.6,
    )
