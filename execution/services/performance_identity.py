"""Stable entry configuration attribution, excluding market and operational state."""
import hashlib
import json
from dataclasses import asdict, is_dataclass
from datetime import time
from decimal import Decimal, InvalidOperation

from django.conf import settings

from bots.services import asset_recommendation_state
from execution.models import RiskPolicy
from execution.services.runtime_config import get_runtime_config
from execution.services.scalper_config import (
    build_scalper_config, normalize_execution_timeframe, resolve_allowed_strategy_pool,
)
from execution.services.strategy_registry import SCALPER_STRATEGY_REGISTRY, build_strategy_config_for_bot
from execution.utils.symbols import canonical_symbol

BOT_FIELDS = (
    "engine_mode", "auto_trade", "default_timeframe", "allowed_timeframes", "allowed_symbols", "enabled_strategies",
    "decision_min_score", "position_sizing_mode", "default_qty", "risk_per_trade_pct", "max_bot_lot_size",
    "max_spread_points", "allowed_deviation_points", "default_tp_pips", "default_sl_pips",
    "risk_max_concurrent_positions", "kill_switch_enabled", "kill_switch_max_unrealized_pct",
    "trade_interval_minutes", "max_trades_per_day", "allocation_amount", "allocation_profit_pct", "allocation_loss_pct",
    "trading_profile", "trading_schedule_enabled", "trading_timezone", "allowed_trading_days",
    "trading_window_start", "trading_window_end", "trading_windows", "allow_opposite_scalp",
    "loss_streak_autopause_enabled", "max_loss_streak_before_pause", "loss_streak_cooldown_min",
    "soft_drawdown_limit_pct", "hard_drawdown_limit_pct", "soft_size_multiplier", "hard_size_multiplier",
    "close_positions_on_emergency_stop", "allow_live_account_execution",
)

POLICY_FIELDS = (
    "max_daily_loss_pct", "max_account_drawdown_pct", "max_total_open_positions",
    "max_positions_per_symbol", "max_order_lot_size", "max_aggregate_open_lots", "stop_after_daily_profit_pct",
)
# These fields have runtime consumers. Deprecated offsets/early-exit controls
# are intentionally absent; fingerprinting a stored but unused value misleads.
RUNTIME_FIELDS = (
    "decision_min_score", "decision_flip_score", "decision_allow_hedging", "decision_order_cooldown_sec",
    "decision_flip_cooldown_min", "decision_max_flips_per_day",
    "decision_scalp_qty_multiplier", "order_ack_timeout_seconds", "trailing_trigger", "trailing_trigger_unit",
    "trailing_distance", "trailing_distance_unit", "max_order_lot", "max_order_notional", "mt5_default_contract_size",
)


def canonical_settings(value):
    if is_dataclass(value):
        value = asdict(value)
    if isinstance(value, dict):
        return {str(key): canonical_settings(item) for key, item in sorted(value.items())
                if key not in {"name", "label", "description", "aliases"}}
    if isinstance(value, (list, tuple)):
        return [canonical_settings(item) for item in value]
    if isinstance(value, time):
        return value.isoformat()
    if value is None or isinstance(value, bool):
        return value
    if isinstance(value, (Decimal, int, float, str)):
        try:
            number = Decimal(str(value))
        except InvalidOperation:
            return str(value)
        if not number.is_finite():
            raise ValueError("Nonfinite configuration value")
        if number == 0:
            return "0"
        normalized = format(number, "f")
        return normalized.rstrip("0").rstrip(".") if "." in normalized else normalized
    raise ValueError("Unsupported configuration value")


def configuration_snapshot(bot, symbol, timeframe):
    config = build_scalper_config(bot)
    effective = asdict(config)
    effective.pop("symbols")
    effective.pop("alias_map")
    effective.pop("profile_slug")
    effective["symbol"] = config.resolve_symbol(symbol)
    effective["strategy_profiles"] = {
        key: profile for key, profile in config.strategy_profiles.items()
        if not profile.symbol or canonical_symbol(profile.symbol) == canonical_symbol(symbol)
    }
    params = bot.scalper_params or {}
    bot_values = {field: getattr(bot, field) for field in BOT_FIELDS}
    if not bot_values["kill_switch_enabled"]:
        bot_values.pop("kill_switch_max_unrealized_pct")
    for field in ("allowed_timeframes", "enabled_strategies", "allowed_symbols", "allowed_trading_days"):
        bot_values[field] = sorted(set(bot_values[field] or []))
    account = bot.broker_account if bot.broker_account_id else None
    policy = (RiskPolicy.objects.filter(broker_account=account).first() if account else None) or RiskPolicy()
    runtime = get_runtime_config()
    return canonical_settings({
        "schema": 2, "symbol": canonical_symbol(symbol),
        "execution_timeframe": normalize_execution_timeframe(timeframe) or str(timeframe),
        "bot": bot_values,
        "account": {"connector": getattr(account, "connector", None), "timezone": getattr(account, "timezone", "UTC"),
                    "risk_policy": {field: getattr(policy, field) for field in POLICY_FIELDS}},
        "runtime": {field: getattr(runtime, field) for field in RUNTIME_FIELDS},
        "strategy_pool": sorted(set(resolve_allowed_strategy_pool(bot)[0])),
        "scalper": effective,
        "profile_selection": {key: params.get(key) for key in ("strategy_profile", "score_profile", "score_profile_key", "risk_preset", "psychology_profile")},
        "opposite_scalp": {key: (params.get("opposite_scalp") or {}).get(key) for key in ("sl_atr_multiplier", "tp_r", "max_duration_minutes")},
        "detectors": {name: build_strategy_config_for_bot(name, bot) for name in SCALPER_STRATEGY_REGISTRY},
    })


def performance_identity(bot, symbol, timeframe):
    build = settings.EXECUTION_BUILD_IDENTITY
    result = {"execution_timeframe": normalize_execution_timeframe(timeframe) or str(timeframe),
              "recommendation_state": "unknown",
              "build_sha": build.get("sha"), "build_status": build.get("status", "unavailable"),
              "config_fingerprint": None, "config_schema": 2}
    try:
        result["recommendation_state"] = asset_recommendation_state(bot)
        snapshot = configuration_snapshot(bot, symbol, timeframe)
        encoded = json.dumps(snapshot, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
        result.update(config_fingerprint=hashlib.sha256(encoded.encode("utf-8")).hexdigest(), config_snapshot=snapshot)
    except (ArithmeticError, AttributeError, LookupError, TypeError, ValueError):
        # Attribution must not break order submission or invent a comparable hash.
        result["config_status"] = "unavailable"
    return result


def submission_identity(order, bot):
    """Freeze the controls actually admitted, without rewriting filled history."""
    context = dict(order.performance_context or {})
    if order.filled_qty > 0 or not order.decision_id or not order.decision.signal_id:
        return context
    identity = performance_identity(bot, order.symbol, order.decision.signal.timeframe)
    original = context.get("decision_config_fingerprint") or context.get("config_fingerprint")
    context.pop("config_snapshot", None)
    context.pop("config_status", None)
    context.update(identity)
    if original and original != identity.get("config_fingerprint"):
        context["decision_config_fingerprint"] = original
    context["source"] = "submission_snapshot"
    return context
