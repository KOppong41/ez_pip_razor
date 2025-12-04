from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, time
from decimal import Decimal
from typing import Dict, Iterable, List, Tuple

from django.utils import timezone

from bots.models import Bot
from execution.models import ScalperProfile, default_scalper_profile_config


def _deep_merge(base: dict, override: dict | None) -> dict:
    result = base.copy()
    for key, value in (override or {}).items():
        if isinstance(value, dict) and isinstance(result.get(key), dict):
            result[key] = _deep_merge(result[key], value)
        else:
            result[key] = value
    return result


def _parse_time(value: str) -> time:
    hours, minutes = value.split(":")
    return time(hour=int(hours), minute=int(minutes))


@dataclass(frozen=True)
class SessionWindow:
    start: time
    end: time
    label: str = "session"
    enabled: bool = True

    def contains(self, dt: datetime) -> bool:
        if not self.enabled:
            return False
        start_dt = dt.replace(hour=self.start.hour, minute=self.start.minute, second=0, microsecond=0)
        end_dt = dt.replace(hour=self.end.hour, minute=self.end.minute, second=0, microsecond=0)
        if self.start <= self.end:
            return start_dt <= dt <= end_dt
        # Overnight window
        if dt.time() >= self.start or dt.time() <= self.end:
            return True
        return False


@dataclass(frozen=True)
class NewsBlackout:
    label: str
    lead_minutes: int
    trail_minutes: int
    keywords: Tuple[str, ...] = ()
    enabled: bool = True


@dataclass(frozen=True)
class ReentryRules:
    min_minutes_after_loss: int = 5
    min_minutes_between_wins: int = 1
    max_trades_per_move: int = 3
    require_pullback_points: int = 20


@dataclass(frozen=True)
class CountertrendRule:
    enabled: bool = False
    risk_multiplier: Decimal = Decimal("0.5")
    max_positions: int = 1
    min_score: Decimal = Decimal("1.5")


@dataclass(frozen=True)
class SymbolConfig:
    key: str
    aliases: Tuple[str, ...]
    execution_timeframes: Tuple[str, ...]
    context_timeframes: Tuple[str, ...]
    sl_points_min: Decimal
    sl_points_max: Decimal
    tp_r_multiple: Decimal
    be_trigger_r: Decimal
    be_buffer_r: Decimal
    trail_trigger_r: Decimal
    trail_mode: str
    max_spread_points: Decimal
    max_slippage_points: Decimal
    allow_countertrend: bool
    risk_pct: Decimal

    def matches_symbol(self, symbol: str) -> bool:
        target = symbol.upper()
        return target == self.key or target in self.aliases


@dataclass(frozen=True)
class RiskEnvelope:
    default_risk_pct: Decimal
    conservative_risk_pct: Decimal
    hard_cap_pct: Decimal
    soft_dd_pct: Decimal
    hard_dd_pct: Decimal
    soft_multiplier: Decimal
    hard_multiplier: Decimal
    max_concurrent_trades: int
    max_trades_per_symbol: int
    max_scale_ins_per_symbol: int
    max_symbol_risk_pct: Decimal
    kill_switch_exit_minutes: int
    max_trades_per_day: int = 0

    def effective_risk_pct(self, drawdown_pct: Decimal | float, conservative: bool = False) -> Decimal:
        base = self.conservative_risk_pct if conservative else self.default_risk_pct
        dd = Decimal(str(drawdown_pct or 0))
        if dd >= self.hard_dd_pct:
            return (base * self.hard_multiplier).quantize(Decimal("0.0001"))
        if dd >= self.soft_dd_pct:
            return (base * self.soft_multiplier).quantize(Decimal("0.0001"))
        return base


@dataclass(frozen=True)
class StrategyProfile:
    key: str
    name: str
    symbol: str | None
    execution_timeframes: Tuple[str, ...]
    description: str | None
    enabled_strategies: Tuple[str, ...]
    internal_triggers: Tuple[str, ...]
    disabled_strategies: Tuple[str, ...]


@dataclass(frozen=True)
class RiskPreset:
    key: str
    name: str
    tp_pips: Decimal
    sl_pips: Decimal
    kill_switch_pct: Decimal


@dataclass(frozen=True)
class PsychologyProfile:
    key: str
    autopause: bool
    max_loss_streak: int
    cooldown_min: int
    soft_dd_pct: Decimal
    hard_dd_pct: Decimal
    soft_multiplier: Decimal
    hard_multiplier: Decimal


@dataclass(frozen=True)
class FlipSettings:
    min_score: Decimal
    cooldown_minutes: int


@dataclass(frozen=True)
class ScalperConfig:
    profile_slug: str
    symbols: Dict[str, SymbolConfig] = field(default_factory=dict)
    alias_map: Dict[str, str] = field(default_factory=dict)
    sessions: Tuple[SessionWindow, ...] = ()
    rollover_blackout: Tuple[SessionWindow, ...] = ()
    news_blackouts: Tuple[NewsBlackout, ...] = ()
    risk: RiskEnvelope | None = None
    reentry: ReentryRules = ReentryRules()
    countertrend: CountertrendRule = CountertrendRule()
    time_in_trade_limit_min: int = 30
    score_profiles: Dict[str, Decimal] = field(default_factory=dict)
    default_score_profile: str = "default"
    strategy_profiles: Dict[str, StrategyProfile] = field(default_factory=dict)
    default_strategy_profile: str = "default"
    risk_presets: Dict[str, RiskPreset] = field(default_factory=dict)
    default_risk_preset: str = "default"
    psychology_profiles: Dict[str, PsychologyProfile] = field(default_factory=dict)
    default_psychology_profile: str = "default"
    flip_settings: FlipSettings | None = None
    score_profiles: Dict[str, Decimal] = field(default_factory=dict)
    default_score_profile: str = "default"

    def resolve_symbol(self, symbol: str) -> SymbolConfig | None:
        if not symbol:
            return None
        key = symbol.upper()
        canon = self.alias_map.get(key, key)
        return self.symbols.get(canon)

    def is_session_open(self, moment: datetime | None = None) -> bool:
        moment = moment or timezone.now()
        if not self.sessions:
            return True
        return any(session.contains(moment) for session in self.sessions)

    def is_rollover_window(self, moment: datetime | None = None) -> bool:
        moment = moment or timezone.now()
        if not self.rollover_blackout:
            return False
        return any(window.contains(moment) for window in self.rollover_blackout)


def _build_symbol_configs(raw_symbols: dict) -> Tuple[Dict[str, SymbolConfig], Dict[str, str]]:
    configs: Dict[str, SymbolConfig] = {}
    alias_map: Dict[str, str] = {}
    for symbol, settings in (raw_symbols or {}).items():
        key = symbol.upper()
        aliases = tuple(a.upper() for a in settings.get("aliases", []))
        cfg = SymbolConfig(
            key=key,
            aliases=aliases,
            execution_timeframes=tuple(settings.get("execution_timeframes", [])),
            context_timeframes=tuple(settings.get("context_timeframes", [])),
            sl_points_min=Decimal(str(settings.get("sl_points", {}).get("min", 1))),
            sl_points_max=Decimal(str(settings.get("sl_points", {}).get("max", 10))),
            tp_r_multiple=Decimal(str(settings.get("tp_r_multiple", 1.0))),
            be_trigger_r=Decimal(str(settings.get("be_trigger_r", 1.0))),
            be_buffer_r=Decimal(str(settings.get("be_buffer_r", 0.2))),
            trail_trigger_r=Decimal(str(settings.get("trail_trigger_r", 1.5))),
            trail_mode=settings.get("trail_mode", "swing"),
            max_spread_points=Decimal(str(settings.get("max_spread_points", 10))),
            max_slippage_points=Decimal(str(settings.get("max_slippage_points", 5))),
            allow_countertrend=bool(settings.get("allow_countertrend", False)),
            risk_pct=Decimal(str(settings.get("risk_pct", 0.5))),
        )
        configs[key] = cfg
        alias_map[key] = key
        for alias in aliases:
            alias_map[alias] = key
    return configs, alias_map


def _build_sessions(raw_sessions: Iterable[dict] | None) -> Tuple[SessionWindow, ...]:
    if not raw_sessions:
        return ()
    sessions: List[SessionWindow] = []
    for item in raw_sessions:
        try:
            start = _parse_time(item["start"])
            end = _parse_time(item["end"])
        except Exception:
            continue
        sessions.append(
            SessionWindow(
                start=start,
                end=end,
                label=item.get("label", "session"),
                enabled=item.get("enabled", True),
            )
        )
    return tuple(sessions)


def _build_blackouts(raw: Iterable[dict] | None) -> Tuple[NewsBlackout, ...]:
    if not raw:
        return ()
    windows: List[NewsBlackout] = []
    for item in raw:
        windows.append(
            NewsBlackout(
                label=item.get("label", "event"),
                lead_minutes=int(item.get("lead_minutes", 30)),
                trail_minutes=int(item.get("trail_minutes", 30)),
                keywords=tuple(item.get("keywords", [])),
                enabled=item.get("enabled", True),
            )
        )
    return tuple(windows)


def _build_strategy_profiles(raw_profiles: dict | None) -> Dict[str, StrategyProfile]:
    profiles: Dict[str, StrategyProfile] = {}
    for key, data in (raw_profiles or {}).items():
        data = data or {}
        profile = StrategyProfile(
            key=key,
            name=data.get("name", key.replace("_", " ").title()),
            symbol=data.get("symbol"),
            execution_timeframes=tuple(data.get("execution_timeframes", [])),
            description=data.get("description"),
            enabled_strategies=tuple(data.get("enabled_strategies", [])),
            internal_triggers=tuple(data.get("internal_triggers", [])),
            disabled_strategies=tuple(data.get("disabled_strategies", [])),
        )
        profiles[key] = profile
    return profiles


def _build_risk_presets(raw_presets: dict | None) -> Dict[str, RiskPreset]:
    presets: Dict[str, RiskPreset] = {}
    for key, data in (raw_presets or {}).items():
        data = data or {}
        preset = RiskPreset(
            key=key,
            name=data.get("name", key.replace("_", " ").title()),
            tp_pips=Decimal(str(data.get("tp_pips", 100))),
            sl_pips=Decimal(str(data.get("sl_pips", 50))),
            kill_switch_pct=Decimal(str(data.get("kill_switch_pct", 5.0))),
        )
        presets[key] = preset
    return presets


def _build_psychology_profiles(raw_profiles: dict | None) -> Dict[str, PsychologyProfile]:
    profiles: Dict[str, PsychologyProfile] = {}
    for key, data in (raw_profiles or {}).items():
        data = data or {}
        profile = PsychologyProfile(
            key=key,
            autopause=bool(data.get("autopause", True)),
            max_loss_streak=int(data.get("max_loss_streak", 3)),
            cooldown_min=int(data.get("cooldown_min", 60)),
            soft_dd_pct=Decimal(str(data.get("soft_dd_pct", 3.0))),
            hard_dd_pct=Decimal(str(data.get("hard_dd_pct", 5.0))),
            soft_multiplier=Decimal(str(data.get("soft_multiplier", 0.5))),
            hard_multiplier=Decimal(str(data.get("hard_multiplier", 0.25))),
        )
        profiles[key] = profile
    return profiles


def _build_flip_settings(raw: dict | None) -> FlipSettings | None:
    if not raw:
        return None
    return FlipSettings(
        min_score=Decimal(str(raw.get("min_score", 0.85))),
        cooldown_minutes=int(raw.get("cooldown_minutes", 5)),
    )


def _build_score_profiles(raw_profiles: dict | None) -> Dict[str, Decimal]:
    profiles: Dict[str, Decimal] = {}
    for key, value in (raw_profiles or {}).items():
        threshold = value
        if isinstance(value, dict):
            threshold = value.get("threshold", value.get("value"))
        try:
            profiles[key] = Decimal(str(threshold))
        except Exception:
            continue
    return profiles


def _build_risk_envelope(raw: dict | None) -> RiskEnvelope:
    raw = raw or {}
    return RiskEnvelope(
        default_risk_pct=Decimal(str(raw.get("default_risk_pct", 0.5))),
        conservative_risk_pct=Decimal(str(raw.get("conservative_risk_pct", 0.25))),
        hard_cap_pct=Decimal(str(raw.get("hard_cap_pct", 1.0))),
        soft_dd_pct=Decimal(str(raw.get("soft_dd_pct", 3.0))),
        hard_dd_pct=Decimal(str(raw.get("hard_dd_pct", 5.0))),
        soft_multiplier=Decimal(str(raw.get("soft_multiplier", 0.5))),
        hard_multiplier=Decimal(str(raw.get("hard_multiplier", 0.0))),
        max_concurrent_trades=int(raw.get("max_concurrent_trades", 5)),
        max_trades_per_symbol=int(raw.get("max_trades_per_symbol", 3)),
        max_scale_ins_per_symbol=int(raw.get("max_scale_ins_per_symbol", 2)),
        max_symbol_risk_pct=Decimal(str(raw.get("max_symbol_risk_pct", 1.5))),
        kill_switch_exit_minutes=int(raw.get("kill_switch_exit_minutes", 10)),
        max_trades_per_day=int(raw.get("max_trades_per_day", 0)),
    )


def _build_countertrend(raw: dict | None) -> CountertrendRule:
    raw = raw or {}
    return CountertrendRule(
        enabled=bool(raw.get("enabled", False)),
        risk_multiplier=Decimal(str(raw.get("risk_multiplier", 0.5))),
        max_positions=int(raw.get("max_positions", 1)),
        min_score=Decimal(str(raw.get("min_score", 1.5))),
    )


def build_scalper_config(bot: Bot | None) -> ScalperConfig:
    """
    Compose the effective scalper config for a bot by layering defaults, profile data, and per-bot overrides.
    """
    base = default_scalper_profile_config()
    slug = "core_scalper"
    if bot and bot.scalper_profile_id:
        profile = bot.scalper_profile
        if profile is None:
            profile = ScalperProfile.objects.filter(pk=bot.scalper_profile_id).first()
        if profile is None:
            profile = ScalperProfile.get_or_create_default()
        slug = profile.slug
        base = profile.get_config()
    if bot and bot.scalper_params:
        base = _deep_merge(base, bot.scalper_params)

    symbols, alias_map = _build_symbol_configs(base.get("symbols", {}))
    sessions = _build_sessions(base.get("sessions"))
    rollover = _build_sessions(base.get("rollover_blackout"))
    news_blackouts = _build_blackouts(base.get("news_blackouts"))
    risk = _build_risk_envelope(base.get("risk"))
    reentry_data = base.get("reentry") or {}
    reentry = ReentryRules(
        min_minutes_after_loss=int(reentry_data.get("min_minutes_after_loss", 5)),
        min_minutes_between_wins=int(reentry_data.get("min_minutes_between_wins", 1)),
        max_trades_per_move=int(reentry_data.get("max_trades_per_move", 3)),
        require_pullback_points=int(reentry_data.get("require_pullback_points", 20)),
    )
    countertrend = _build_countertrend(base.get("countertrend"))
    score_profiles = _build_score_profiles(base.get("score_profiles"))
    default_score_profile = base.get("default_score_profile") or (
        next(iter(score_profiles.keys()), "default")
    )
    strategy_profiles = _build_strategy_profiles(base.get("strategy_profiles"))
    default_strategy_profile = base.get("default_strategy_profile") or (
        next(iter(strategy_profiles.keys()), "default")
    )
    risk_presets = _build_risk_presets(base.get("risk_presets"))
    default_risk_preset = base.get("default_risk_preset") or (
        next(iter(risk_presets.keys()), "default")
    )
    psychology_profiles = _build_psychology_profiles(base.get("psychology_profiles"))
    default_psychology_profile = base.get("default_psychology_profile") or (
        next(iter(psychology_profiles.keys()), "default")
    )
    flip_settings = _build_flip_settings(base.get("flip"))

    return ScalperConfig(
        profile_slug=slug,
        symbols=symbols,
        alias_map=alias_map,
        sessions=sessions,
        rollover_blackout=rollover,
        news_blackouts=news_blackouts,
        risk=risk,
        reentry=reentry,
        countertrend=countertrend,
        time_in_trade_limit_min=int(base.get("time_in_trade_limit_min", 30)),
        score_profiles=score_profiles,
        default_score_profile=default_score_profile,
        strategy_profiles=strategy_profiles,
        default_strategy_profile=default_strategy_profile,
        risk_presets=risk_presets,
        default_risk_preset=default_risk_preset,
        psychology_profiles=psychology_profiles,
        default_psychology_profile=default_psychology_profile,
        flip_settings=flip_settings,
    )
