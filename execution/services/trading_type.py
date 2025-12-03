from dataclasses import dataclass
from datetime import datetime, time
from functools import lru_cache
from decimal import Decimal
from typing import Dict, List, Optional

from django.utils import timezone

from execution.models import TradingProfile, default_trading_profile_data

WEEKDAY_MAP = {
    0: "mon",
    1: "tue",
    2: "wed",
    3: "thu",
    4: "fri",
    5: "sat",
    6: "sun",
}


@dataclass(frozen=True)
class TradingProfileConfig:
    slug: str
    name: str
    description: str
    risk_per_trade_pct: Decimal
    max_trades_per_day: int
    max_concurrent_positions: int
    max_drawdown_pct: Decimal
    decision_min_score: Decimal
    signal_quality_threshold: Decimal
    cooldown_seconds: int
    allowed_days: List[str]
    trading_start: time
    trading_end: time


def _normalize_entry(entry) -> TradingProfileConfig:
    if isinstance(entry, TradingProfile):
        return TradingProfileConfig(
            slug=entry.slug,
            name=entry.name,
            description=entry.description,
            risk_per_trade_pct=entry.risk_per_trade_pct,
            max_trades_per_day=entry.max_trades_per_day,
            max_concurrent_positions=entry.max_concurrent_positions,
            max_drawdown_pct=entry.max_drawdown_pct,
            decision_min_score=entry.decision_min_score,
            signal_quality_threshold=entry.signal_quality_threshold,
            cooldown_seconds=entry.cooldown_seconds,
            allowed_days=entry.allowed_days or [],
            trading_start=entry.trading_start,
            trading_end=entry.trading_end,
        )
    return TradingProfileConfig(
        slug=entry["slug"],
        name=entry["name"],
        description=entry.get("description", ""),
        risk_per_trade_pct=Decimal(entry["risk_per_trade_pct"]),
        max_trades_per_day=int(entry["max_trades_per_day"]),
        max_concurrent_positions=int(entry["max_concurrent_positions"]),
        max_drawdown_pct=Decimal(entry["max_drawdown_pct"]),
        decision_min_score=Decimal(entry["decision_min_score"]),
        signal_quality_threshold=Decimal(entry["signal_quality_threshold"]),
        cooldown_seconds=int(entry["cooldown_seconds"]),
        allowed_days=list(entry.get("allowed_days") or []),
        trading_start=entry.get("trading_start", time(0, 0)),
        trading_end=entry.get("trading_end", time(23, 59)),
    )


@lru_cache(maxsize=None)
def get_profile_config(slug: Optional[str] = "very_safe") -> TradingProfileConfig:
    if not slug:
        slug = "very_safe"
    profile = TradingProfile.get_default_profile(slug)
    return _normalize_entry(profile if profile else TradingProfile.get_default_profile("very_safe"))


def get_all_profile_configs() -> Dict[str, TradingProfileConfig]:
    configs: Dict[str, TradingProfileConfig] = {}
    for entry in TradingProfile.objects.all():
        config = _normalize_entry(entry)
        configs[config.slug] = config
    if not configs:
        for entry in default_trading_profile_data():
            config = _normalize_entry(entry)
            configs[config.slug] = config
    return configs


def apply_profile_defaults(bot, slug: Optional[str] = None):
    slug = slug or getattr(bot, "trading_profile", "very_safe")
    config = get_profile_config(slug)
    bot.trading_profile = config.slug
    bot.max_trades_per_day = config.max_trades_per_day
    bot.risk_max_concurrent_positions = config.max_concurrent_positions
    bot.decision_min_score = config.decision_min_score
    bot.allowed_trading_days = config.allowed_days or []
    bot.trading_window_start = config.trading_start
    bot.trading_window_end = config.trading_end


def get_profile_warnings(bot) -> Dict[str, str]:
    profile = get_profile_config(getattr(bot, "trading_profile", None))
    warnings = {}
    if getattr(bot, "risk_max_concurrent_positions", 0) > profile.max_concurrent_positions:
        warnings["risk_max_concurrent_positions"] = (
            "Increases the maximum open positions beyond the profile recommendation."
        )
    if getattr(bot, "max_trades_per_day", 0) > profile.max_trades_per_day:
        warnings["max_trades_per_day"] = "Raises the daily trade cap above the profile bounds."
    if getattr(bot, "kill_switch_max_unrealized_pct", Decimal("0")) > profile.max_drawdown_pct:
        warnings["kill_switch_max_unrealized_pct"] = (
            "Drawdown limit is wider than the selected profile, which increases risk."
        )
    return warnings


def is_within_trading_window(bot, now: Optional[datetime] = None) -> bool:
    now = now or timezone.localtime()

    # If the bot has trading schedule enforcement disabled, always allow.
    if not getattr(bot, "trading_schedule_enabled", True):
        return True

    weekday = WEEKDAY_MAP[now.weekday()]
    days = bot.allowed_trading_days or get_profile_config(bot.trading_profile).allowed_days
    if days and weekday not in [d.lower() for d in days]:
        return False
    start = getattr(bot, "trading_window_start", None)
    end = getattr(bot, "trading_window_end", None)
    if start and end:
        current = now.time()
        if start <= end:
            return start <= current <= end
        return current >= start or current <= end
    return True
