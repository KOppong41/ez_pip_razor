
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

def route_bot_for_signal(symbol: str, timeframe: str) -> Optional[Bot]:
    # naive: first ACTIVE bot that accepts symbol/timeframe
    for bot in Bot.objects.filter(status="active").order_by("id"):
        if bot.accepts(symbol, timeframe):
            return bot
    return None
