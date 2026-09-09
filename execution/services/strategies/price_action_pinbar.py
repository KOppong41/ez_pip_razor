from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from typing import List, Optional, Literal, Tuple

from execution.services.marketdata import Candle
from execution.services.engine_types import EngineDecision

PinType = Literal["bullish", "bearish"]


@dataclass
class PinBarConfig:
    ema_period: int = 20
    lookback_for_levels: int = 80
    wick_level_tolerance_atr: Decimal = Decimal("0.5")
    rr: Decimal = Decimal("2.0")
    entry_buffer_factor: Decimal = Decimal("0.1")
    sl_buffer_factor: Decimal = Decimal("0.1")
    min_range_atr: Decimal = Decimal("0.05")
    atr_period: int = 12
    min_atr_pct: Decimal = Decimal("0.00005")
    session_hours: Tuple[Tuple[int, int], ...] = ((5, 21),)


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
    total = sum((c["high"] - c["low"] for c in candles[-period:]), Decimal("0"))
    return total / Decimal(str(period))


def _session_ok(candles: List[Candle], cfg: PinBarConfig) -> bool:
    if not cfg.session_hours:
        return True
    last = candles[-1] if candles else None
    if not last:
        return True
    t = last.get("time")
    if t is None:
        return True
    hour = t.hour
    return any(start <= hour < end for start, end in cfg.session_hours)


def _classify_pin_bar(c: Candle, min_range: Decimal) -> Optional[PinType]:
    high, low, open_, close = c["high"], c["low"], c["open"], c["close"]
    total_range = high - low
    if total_range <= min_range:
        return None

    body = abs(close - open_)
    upper_wick = high - max(open_, close)
    lower_wick = min(open_, close) - low

    # Body <= 1/3 of bar
    if body > total_range / Decimal("3"):
        return None

    long_wick = max(upper_wick, lower_wick)
    short_wick = min(upper_wick, lower_wick)

    # Wick at least 2/3 of bar
    if long_wick < (Decimal("2") / Decimal("3")) * total_range:
        return None

    # One wick must dominate
    if short_wick > Decimal("0") and short_wick > long_wick * Decimal("0.25"):
        return None

    if lower_wick == long_wick and close > open_:
        return "bullish"
    if upper_wick == long_wick and close < open_:
        return "bearish"
    return None


def _trend_ok(pin: PinType, ema: List[Decimal], idx: int, close: Decimal) -> bool:
    if idx < 1:
        return False
    ema_now = ema[idx]
    ema_prev = ema[idx - 1]
    if pin == "bullish":
        return close > ema_now and ema_now > ema_prev
    return close < ema_now and ema_now < ema_prev


def _collect_wick_levels(
    candles: List[Candle], cfg: PinBarConfig, *, min_range: Decimal, tolerance: Decimal
) -> List[Decimal]:
    raw_levels: List[Decimal] = []
    window = candles[-cfg.lookback_for_levels :] if candles else []
    for c in window:
        high, low, open_, close = c["high"], c["low"], c["open"], c["close"]
        total_range = high - low
        if total_range <= min_range:
            continue
        upper_wick = high - max(open_, close)
        lower_wick = min(open_, close) - low
        if upper_wick >= (Decimal("2") / Decimal("3")) * total_range:
            raw_levels.append(high)
        if lower_wick >= (Decimal("2") / Decimal("3")) * total_range:
            raw_levels.append(low)

    clustered: List[Decimal] = []
    for lvl in raw_levels:
        if not clustered:
            clustered.append(lvl)
            continue
        if all(abs(lvl - existing) > tolerance for existing in clustered):
            clustered.append(lvl)
    return clustered


def _pin_has_valid_level(pin: PinType, c: Candle, levels: List[Decimal], tolerance: Decimal) -> bool:
    if not levels:
        return False
    wick_price = c["low"] if pin == "bullish" else c["high"]
    return any(abs(wick_price - lvl) <= tolerance for lvl in levels)


def _build_orders(pin: PinType, c: Candle, cfg: PinBarConfig) -> tuple[Decimal, Decimal, Decimal]:
    high, low, open_, close = c["high"], c["low"], c["open"], c["close"]
    total_range = high - low

    if pin == "bullish":
        nose = max(open_, close)
        wick_extreme = low
        direction = Decimal("1")
        entry = nose + cfg.entry_buffer_factor * total_range
        stop_loss = wick_extreme - cfg.sl_buffer_factor * total_range
    else:
        nose = min(open_, close)
        wick_extreme = high
        direction = Decimal("-1")
        entry = nose - cfg.entry_buffer_factor * total_range
        stop_loss = wick_extreme + cfg.sl_buffer_factor * total_range

    risk = abs(entry - stop_loss)
    take_profit = entry + direction * cfg.rr * risk
    return entry, stop_loss, take_profit


def run_price_action_pinbar(symbol: str, candles: List[Candle], cfg: Optional[PinBarConfig] = None) -> EngineDecision:
    cfg = cfg or PinBarConfig()
    if len(candles) < max(cfg.ema_period + 5, cfg.lookback_for_levels + 5):
        return EngineDecision(
            action="skip",
            reason="not_enough_candles",
            strategy="price_action_pinbar",
            metadata={"reason": "insufficient_candles", "needed": max(cfg.ema_period + 5, cfg.lookback_for_levels + 5), "got": len(candles)},
        )

    if not _session_ok(candles, cfg):
        return EngineDecision(
            action="skip",
            reason="pinbar_session_blocked",
            strategy="price_action_pinbar",
            metadata={"reason": "session", "time": str(candles[-1].get("time"))},
        )

    atr_price = _atr(candles, cfg.atr_period)
    last_close = candles[-1]["close"]
    atr_pct = atr_price / abs(last_close) if last_close else Decimal("0")
    if atr_price <= 0 or atr_pct < cfg.min_atr_pct:
        return EngineDecision(
            action="skip",
            reason="pinbar_low_volatility",
            strategy="price_action_pinbar",
            metadata={"reason": "low_atr", "atr_pct": float(atr_pct), "min_atr_pct": float(cfg.min_atr_pct)},
        )

    closes = [c["close"] for c in candles]
    ema = _ema(closes, cfg.ema_period)
    last = candles[-1]
    idx = len(candles) - 1

    min_range = atr_price * cfg.min_range_atr
    level_tolerance = atr_price * cfg.wick_level_tolerance_atr
    pin_type = _classify_pin_bar(last, min_range)
    if pin_type is None:
        return EngineDecision(
            action="skip",
            reason="no_pinbar",
            strategy="price_action_pinbar",
            metadata={"reason": "no_pin", "payload": last},
        )

    if not _trend_ok(pin_type, ema, idx, last["close"]):
        return EngineDecision(
            action="skip",
            reason="trend_filter_fail",
            strategy="price_action_pinbar",
            metadata={"reason": "trend_fail"},
        )

    levels = _collect_wick_levels(
        candles[:-1], cfg, min_range=min_range, tolerance=level_tolerance
    )
    if not _pin_has_valid_level(pin_type, last, levels, level_tolerance):
        return EngineDecision(
            action="skip",
            reason="no_sr_confluence",
            strategy="price_action_pinbar",
            metadata={"reason": "no_sr", "levels": [str(l) for l in levels]},
        )

    entry, sl, tp = _build_orders(pin_type, last, cfg)
    wick = abs(last["high"] - last["low"])
    confidence = min(
        Decimal("1"),
        max(Decimal("0.4"), wick / (atr_price * Decimal("2"))),
    )

    return EngineDecision(
        action="open",
        direction="buy" if pin_type == "bullish" else "sell",
        sl=sl,
        tp=tp,
        reason="price_action_pinbar",
        strategy="price_action_pinbar",
        score=float(confidence),
        metadata={
            "confidence": float(confidence),
            "wick_range": float(wick),
            "atr_pct": float(atr_pct),
            "level_count": len(levels),
        },
    )
