"""Pure candle strategies shared by the live scalper and historical simulations."""
from dataclasses import dataclass, fields, replace
from decimal import Decimal
from typing import Callable

from execution.services.strategies.breakout_retest import BreakoutRetestConfig, run_breakout_retest
from execution.services.strategies.doji_breakout import DojiBreakoutConfig, run_doji_breakout
from execution.services.strategies.momentum_ignition import MomentumIgnitionConfig, run_momentum_ignition
from execution.services.strategies.price_action_pinbar import PinBarConfig, run_price_action_pinbar
from execution.services.strategies.range_reversion import RangeReversionConfig, run_range_reversion
from execution.services.strategies.trend_pullback import TrendPullbackConfig, run_trend_pullback


@dataclass(frozen=True)
class ScalperStrategyEntry:
    runner: Callable
    config_factory: Callable[[], object]
    requires_symbol: bool = False


SCALPER_STRATEGY_REGISTRY = {
    "price_action_pinbar": ScalperStrategyEntry(run_price_action_pinbar, PinBarConfig, True),
    "trend_pullback": ScalperStrategyEntry(run_trend_pullback, TrendPullbackConfig),
    "doji_breakout": ScalperStrategyEntry(run_doji_breakout, DojiBreakoutConfig, True),
    "range_reversion": ScalperStrategyEntry(run_range_reversion, RangeReversionConfig),
    "breakout_retest": ScalperStrategyEntry(run_breakout_retest, BreakoutRetestConfig),
    "momentum_ignition": ScalperStrategyEntry(run_momentum_ignition, MomentumIgnitionConfig),
}


def _coerce_override(value, current):
    if isinstance(current, Decimal):
        return Decimal(str(value))
    if isinstance(current, bool):
        return bool(value)
    if isinstance(current, int):
        return int(value)
    if isinstance(current, float):
        return float(value)
    if isinstance(current, tuple) and isinstance(value, (list, tuple)):
        return tuple(
            tuple(item) if isinstance(item, (list, tuple)) else item
            for item in value
        )
    return value


def apply_strategy_config_overrides(config, overrides):
    """Apply known, type-coerced values to a strategy dataclass."""
    overrides = overrides if isinstance(overrides, dict) else {}
    allowed_fields = {field.name for field in fields(config)}
    values = {
        key: _coerce_override(value, getattr(config, key))
        for key, value in overrides.items()
        if key in allowed_fields
    }
    return replace(config, **values) if values else config


def build_strategy_config(strategy_name: str, asset=None, *, strategy_overrides=None):
    """Build generic strategy config and apply explicitly supplied tuning."""
    entry = SCALPER_STRATEGY_REGISTRY[strategy_name]
    config = entry.config_factory()
    if strategy_overrides is None:
        preset = getattr(asset, "recommended_config", None) or {}
        strategy_overrides = (
            preset.get("strategy_overrides") or {}
            if isinstance(preset, dict)
            else {}
        )
    overrides = (
        strategy_overrides.get(strategy_name) or {}
        if isinstance(strategy_overrides, dict)
        else {}
    )
    return apply_strategy_config_overrides(config, overrides)


def build_strategy_config_for_bot(strategy_name: str, bot):
    """Use the detector tuning frozen when this bot's preset was applied."""
    overrides = (
        getattr(bot, "asset_strategy_overrides_applied", None) or {}
        if getattr(bot, "asset_preset_version_applied", None) is not None
        else {}
    )
    return build_strategy_config(
        strategy_name,
        strategy_overrides=overrides,
    )
