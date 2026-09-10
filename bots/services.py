
from copy import deepcopy
from datetime import time
from decimal import Decimal
from typing import Optional

from django.db import models
from django.utils import timezone
from execution.utils.symbols import canonical_symbol

from .models import Bot


ASSET_PRESET_DIRECT_FIELDS = (
    "engine_mode", "default_timeframe", "allowed_timeframes", "enabled_strategies",
    "position_sizing_mode", "risk_per_trade_pct", "risk_max_concurrent_positions",
    "decision_min_score", "trade_interval_minutes", "max_trades_per_day",
    "max_spread_points", "allowed_deviation_points", "allow_opposite_scalp",
    "allow_live_account_execution", "kill_switch_enabled",
    "kill_switch_max_unrealized_pct", "loss_streak_autopause_enabled",
    "max_loss_streak_before_pause", "loss_streak_cooldown_min",
    "soft_drawdown_limit_pct", "soft_size_multiplier", "hard_drawdown_limit_pct",
    "hard_size_multiplier",
)
UNORDERED_PRESET_FIELDS = {
    "allowed_timeframes",
    "enabled_strategies",
    "allowed_trading_days",
}


def _preset_values_equal(field, current, recommended):
    if field in UNORDERED_PRESET_FIELDS:
        return set(current or []) == set(recommended or [])
    if isinstance(current, Decimal) or isinstance(recommended, (Decimal, float)):
        try:
            return Decimal(str(current)) == Decimal(str(recommended))
        except Exception:
            return False
    return current == recommended


def recommended_bot_defaults(asset):
    """Translate an Asset's stored recommendation schema into Bot defaults."""
    config = deepcopy(getattr(asset, "recommended_config", None) or {})
    values = {
        key: config[key] for key in ASSET_PRESET_DIRECT_FIELDS if key in config
    }
    for key, value in tuple(values.items()):
        if isinstance(Bot._meta.get_field(key), models.DecimalField):
            values[key] = Decimal(str(value))
    schedule = config.get("trading_schedule") or {}
    if schedule:
        values.update(
            {
                "trading_schedule_enabled": bool(schedule.get("enabled", True)),
                "trading_timezone": schedule.get("timezone") or "UTC",
                "allowed_trading_days": list(schedule.get("allowed_days") or []),
                "trading_window_start": time.fromisoformat(schedule.get("start", "00:00")),
                "trading_window_end": time.fromisoformat(schedule.get("end", "23:59")),
            }
        )
    return values


def apply_recommendations_to_bot(bot, *, save=True):
    """Explicitly replace preset-controlled fields without touching RiskPolicy."""
    if not bot.asset_id:
        raise ValueError("A bot asset is required before recommendations can be applied")
    values = recommended_bot_defaults(bot.asset)
    for field, value in values.items():
        setattr(bot, field, deepcopy(value))
    params = deepcopy(bot.scalper_params or {})
    symbols = params.get("symbols")
    if isinstance(symbols, dict):
        target = canonical_symbol(bot.asset.symbol)
        params["symbols"] = {
            key: value
            for key, value in symbols.items()
            if canonical_symbol(key) != target
        }
        if not params["symbols"]:
            params.pop("symbols")
    bot.scalper_params = params
    bot.asset_preset_version_applied = bot.asset.recommended_config_version
    bot.asset_preset_applied_at = timezone.now()
    if save:
        bot.full_clean()
        bot.save(
            update_fields=[
                *values.keys(),
                "scalper_params",
                "asset_preset_version_applied",
                "asset_preset_applied_at",
            ]
        )
    return bot


def asset_recommendation_state(bot) -> str:
    """Describe whether an applied preset still matches the bot's settings."""
    asset = getattr(bot, "asset", None)
    applied_version = getattr(bot, "asset_preset_version_applied", None)
    if asset is None or applied_version is None:
        return "not_applied"

    try:
        recommended = recommended_bot_defaults(asset)
    except (ArithmeticError, AttributeError, LookupError, TypeError, ValueError):
        return "customized"
    direct_match = all(
        _preset_values_equal(field, getattr(bot, field, None), value)
        for field, value in recommended.items()
    )

    symbol_match = True
    preset_symbol = (asset.recommended_config or {}).get("symbol_config") or {}
    if preset_symbol:
        from execution.services.scalper_config import build_scalper_config

        try:
            effective = build_scalper_config(bot).resolve_symbol(asset.symbol)
        except (ArithmeticError, AttributeError, LookupError, TypeError, ValueError):
            effective = None
        if effective is None:
            symbol_match = False
        else:
            stop = preset_symbol.get("sl_points") or {}
            expected_fields = {
                "sl_points_min": stop.get("min"),
                "sl_points_max": stop.get("max"),
                "sl_points_unit": stop.get("unit"),
                **{
                    key: value
                    for key, value in preset_symbol.items()
                    if key != "sl_points"
                },
            }
            symbol_match = all(
                _preset_values_equal(
                    field,
                    getattr(effective, field, None),
                    value,
                )
                for field, value in expected_fields.items()
            )

    if direct_match and symbol_match:
        return "recommended"
    if applied_version < asset.recommended_config_version:
        return "update_available"
    return "customized"

def route_bot_for_signal(symbol: str, timeframe: str) -> Optional[Bot]:
    # naive: first ACTIVE bot that accepts symbol/timeframe
    for bot in Bot.objects.filter(status="active").order_by("id"):
        if bot.accepts(symbol, timeframe):
            return bot
    return None
