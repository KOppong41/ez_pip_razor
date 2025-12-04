from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from typing import Any, Dict, Tuple

from django.utils import timezone

from execution.services.prices import get_price
from execution.services.strategy import StrategyDecision
from execution.services.scalper_config import ScalperConfig, SymbolConfig

DEFAULT_TREND_WEIGHT = Decimal("0.4")
DEFAULT_STRUCTURE_WEIGHT = Decimal("0.3")
DEFAULT_MARKET_WEIGHT = Decimal("0.2")
DEFAULT_SESSION_WEIGHT = Decimal("0.1")


@dataclass
class ScalpRuntimeState:
    symbol_cfg: SymbolConfig
    countertrend: bool = False
    drawdown_pct: Decimal = Decimal("0")
    conservative_mode: bool = False


def _resolve_bias(payload: dict[str, Any] | None) -> str | None:
    payload = payload or {}
    for key in ("bias", "trend_bias", "bias_m15", "bias_h1"):
        if key in payload and payload[key]:
            return str(payload[key]).lower()
    return None


def _point_size_for_symbol(symbol_cfg: SymbolConfig, symbol: str, payload: dict[str, Any]) -> Decimal:
    if payload:
        for key in ("point", "tick_size", "point_size"):
            if payload.get(key):
                try:
                    return Decimal(str(payload[key]))
                except Exception:
                    continue
    # Fallback heuristics: gold uses 0.1 points, majors 0.0001
    symbol_upper = symbol_cfg.key
    if symbol_upper.startswith("XAU"):
        return Decimal("0.10")
    return Decimal("0.0001")


def _estimate_sl_distance_points(symbol_cfg: SymbolConfig, payload: dict[str, Any]) -> Decimal:
    payload = payload or {}
    if payload.get("sl_points"):
        try:
            pts = Decimal(str(payload["sl_points"]))
            return pts
        except Exception:
            pass
    atr_points = None
    for key in ("atr_points", "atr", "atr_m1_points"):
        if key in payload and payload[key]:
            try:
                atr_points = Decimal(str(payload[key]))
                break
            except Exception:
                atr_points = None
    if atr_points and atr_points > 0:
        distance = atr_points * Decimal("1.0")
    else:
        distance = (symbol_cfg.sl_points_min + symbol_cfg.sl_points_max) / Decimal("2")
    return max(symbol_cfg.sl_points_min, min(symbol_cfg.sl_points_max, distance))


def _resolve_entry_price(symbol: str, payload: dict[str, Any]) -> Decimal | None:
    for key in ("entry", "price", "close", "last_price"):
        if payload.get(key):
            try:
                return Decimal(str(payload[key]))
            except Exception:
                continue
    try:
        price = get_price(symbol)
        if price is None:
            return None
        return Decimal(str(price))
    except Exception:
        return None


def _build_scalper_params(
    signal,
    state: ScalpRuntimeState,
    entry: Decimal,
    sl: Decimal,
    tp: Decimal,
    risk_pct: Decimal,
    config: ScalperConfig,
) -> Dict[str, Any]:
    symbol_cfg = state.symbol_cfg
    return {
        "symbol": signal.symbol,
        "timeframe": signal.timeframe,
        "direction": signal.direction,
        "entry": str(entry),
        "sl": str(sl),
        "tp": str(tp),
        "risk_pct": str(risk_pct),
        "scalper": {
            "profile": config.profile_slug,
            "symbol": symbol_cfg.key,
            "be_trigger_r": str(symbol_cfg.be_trigger_r),
            "be_buffer_r": str(symbol_cfg.be_buffer_r),
            "trail_trigger_r": str(symbol_cfg.trail_trigger_r),
            "trail_mode": symbol_cfg.trail_mode,
            "time_in_trade_limit_min": config.time_in_trade_limit_min,
            "countertrend": state.countertrend,
            "decided_at": timezone.now().isoformat(),
        },
    }


def _parse_decimal(payload: dict[str, Any], *keys: str) -> Decimal | None:
    payload = payload or {}
    for key in keys:
        if payload.get(key) is None:
            continue
        try:
            return Decimal(str(payload[key]))
        except Exception:
            continue
    return None


def _score_components(
    direction: str,
    bias: str | None,
    countertrend: bool,
    sl_points: Decimal,
    symbol_cfg: SymbolConfig,
    config: ScalperConfig,
    payload: dict[str, Any],
) -> Tuple[Decimal, Dict[str, float]]:
    components: Dict[str, float] = {}
    total = Decimal("0")

    if bias and bias == direction:
        w = DEFAULT_TREND_WEIGHT
    elif countertrend:
        w = DEFAULT_TREND_WEIGHT / Decimal("2")
    else:
        w = DEFAULT_TREND_WEIGHT * Decimal("0.75")
    total += w
    components["trend"] = float(w)

    structure_weight = DEFAULT_STRUCTURE_WEIGHT
    if sl_points is not None:
        if not (symbol_cfg.sl_points_min <= sl_points <= symbol_cfg.sl_points_max):
            structure_weight *= Decimal("0.5")
    else:
        structure_weight *= Decimal("0.4")
    total += structure_weight
    components["structure"] = float(structure_weight)

    spread_points = _parse_decimal(payload, "spread_points", "spread")
    market_weight = DEFAULT_MARKET_WEIGHT
    if spread_points is not None and spread_points > symbol_cfg.max_spread_points:
        market_weight *= Decimal("0.25")
    elif spread_points is None:
        market_weight *= Decimal("0.75")
    total += market_weight
    components["market"] = float(market_weight)

    session_weight = (
        DEFAULT_SESSION_WEIGHT if config.sessions else DEFAULT_SESSION_WEIGHT * Decimal("0.5")
    )
    total += session_weight
    components["session"] = float(session_weight)

    total = min(total, Decimal("1"))
    return total, components


def plan_scalper_trade(signal, bot, config: ScalperConfig) -> StrategyDecision:
    payload = signal.payload or {}
    symbol_cfg = config.resolve_symbol(signal.symbol)
    if not symbol_cfg:
        return StrategyDecision(action="ignore", reason="scalper:symbol_disabled")

    tf_raw = (signal.timeframe or "").strip().upper()
    if not tf_raw:
        return StrategyDecision(action="ignore", reason="scalper:timeframe_blocked")
    if len(tf_raw) >= 2 and tf_raw[0].isdigit() and tf_raw[-1].isalpha():
        timeframe = tf_raw[-1] + tf_raw[:-1]
    else:
        timeframe = tf_raw
    if symbol_cfg.execution_timeframes and timeframe not in symbol_cfg.execution_timeframes:
        return StrategyDecision(action="ignore", reason="scalper:timeframe_blocked")

    if config.sessions and not config.is_session_open():
        return StrategyDecision(action="ignore", reason="scalper:session_closed")

    if config.rollover_blackout and config.is_rollover_window():
        return StrategyDecision(action="ignore", reason="scalper:rollover")

    if payload.get("news_blocked"):
        return StrategyDecision(action="ignore", reason="scalper:news_blackout")

    bias = _resolve_bias(payload)
    direction = (signal.direction or "").lower()
    countertrend = False
    if bias and bias in ("buy", "sell") and bias != direction:
        if symbol_cfg.allow_countertrend:
            countertrend = True
        elif not config.countertrend.enabled:
            return StrategyDecision(action="ignore", reason="scalper:htf_conflict")
        else:
            countertrend = True

    entry = _resolve_entry_price(signal.symbol, payload)
    if entry is None:
        return StrategyDecision(action="ignore", reason="scalper:no_price")

    point = _point_size_for_symbol(symbol_cfg, signal.symbol, payload)
    sl_points = _estimate_sl_distance_points(symbol_cfg, payload)

    sl_delta = sl_points * point
    if sl_delta <= 0:
        return StrategyDecision(action="ignore", reason="scalper:invalid_sl")

    if direction == "buy":
        sl = entry - sl_delta
        tp = entry + sl_delta * symbol_cfg.tp_r_multiple
    else:
        sl = entry + sl_delta
        tp = entry - sl_delta * symbol_cfg.tp_r_multiple

    runtime_state = ScalpRuntimeState(
        symbol_cfg=symbol_cfg,
        countertrend=countertrend,
        drawdown_pct=Decimal(str(payload.get("daily_drawdown_pct", "0") or "0")),
        conservative_mode=bool(payload.get("conservative_mode")),
    )

    risk_cfg = config.risk
    if risk_cfg is None:
        return StrategyDecision(action="ignore", reason="scalper:risk_not_configured")

    risk_pct = risk_cfg.effective_risk_pct(runtime_state.drawdown_pct, runtime_state.conservative_mode)
    if countertrend:
        risk_pct = min(risk_pct * config.countertrend.risk_multiplier, risk_cfg.hard_cap_pct)
    risk_pct = min(risk_pct, risk_cfg.hard_cap_pct)

    params = _build_scalper_params(signal, runtime_state, entry, sl, tp, risk_pct, config)
    score_value, score_components = _score_components(
        direction,
        bias,
        countertrend,
        sl_points,
        symbol_cfg,
        config,
        payload,
    )
    payload["score_components"] = score_components
    payload["score"] = float(score_value)
    signal.payload = payload
    signal.save(update_fields=["payload"])
    score = float(score_value)

    return StrategyDecision(
        action="open",
        reason="scalper:plan",
        params=params,
        score=score,
    )
