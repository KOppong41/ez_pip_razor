from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal
from typing import Tuple

from execution.services.scalper_config import ScalperConfig, SymbolConfig


@dataclass
class RiskConfig:
    max_positions_per_symbol: int = 1
    max_concurrent_positions: int = 5
    allowed_symbols: tuple = ()


@dataclass
class ScalperRiskContext:
    """
    Additional runtime inputs required to enforce the scalper guardrails.
    Computed in the decision service to avoid coupling to ORM queries here.
    """

    config: ScalperConfig
    symbol: str
    direction: str
    trades_today_symbol: int = 0
    trades_today_total: int = 0
    reentry_count: int = 0
    minutes_since_last_same_direction: int | None = None
    minutes_since_last_loss: int | None = None
    spread_points: Decimal | None = None
    slippage_points: Decimal | None = None
    floating_symbol_risk_pct: Decimal | None = None
    scale_in_allowed: bool = False
    allow_scale_in_default: bool = False
    countertrend: bool = False
    last_flip_at: datetime | None = None
    flip_cooldown_minutes: int = 0
    payload_snapshot: dict = field(default_factory=dict)


def check_risk(
    signal,
    open_positions_count_symbol: int,
    open_positions_total: int,
    cfg: RiskConfig,
    scalper_ctx: ScalperRiskContext | None = None,
) -> Tuple[bool, str]:
    symbol = (signal.symbol or "").upper()
    allowed = {s.upper() for s in cfg.allowed_symbols} if cfg.allowed_symbols else set()
    if allowed and symbol not in allowed:
        return False, "symbol_not_allowed"

    if open_positions_total >= cfg.max_concurrent_positions:
        return False, "max_concurrent_positions"

    if open_positions_count_symbol >= cfg.max_positions_per_symbol:
        return False, "max_positions_per_symbol"

    if scalper_ctx:
        return _check_scalper_limits(open_positions_count_symbol, open_positions_total, scalper_ctx)

    return True, "ok"


def _check_scalper_limits(
    open_positions_count_symbol: int,
    open_positions_total: int,
    ctx: ScalperRiskContext,
) -> Tuple[bool, str]:
    config = ctx.config
    sym_cfg = config.resolve_symbol(ctx.symbol)
    if not sym_cfg:
        return False, "scalper:symbol_disabled"

    risk = config.risk
    if not risk:
        return False, "scalper:risk_not_configured"

    if open_positions_total >= risk.max_concurrent_trades:
        return False, "scalper:max_concurrent"

    if open_positions_count_symbol >= risk.max_trades_per_symbol:
        return False, "scalper:max_symbol_trades"

    scale_in_ok = (
        ctx.scale_in_allowed
        or ctx.allow_scale_in_default
        or open_positions_count_symbol == 0
    )
    if open_positions_count_symbol > 0 and not scale_in_ok:
        return False, "scalper:scale_in_blocked"

    if ctx.countertrend and not (config.countertrend.enabled or sym_cfg.allow_countertrend):
        return False, "scalper:countertrend_disabled"

    if (
        ctx.countertrend
        and config.countertrend.enabled
        and open_positions_count_symbol >= config.countertrend.max_positions
    ):
        return False, "scalper:countertrend_cap"

    if ctx.trades_today_symbol >= risk.max_trades_per_symbol:
        return False, "scalper:daily_symbol_cap"

    # Optional daily total cap; disabled when set to 0
    daily_cap = getattr(risk, "max_trades_per_day", 0) or 0
    if daily_cap > 0 and ctx.trades_today_total >= daily_cap:
        return False, "scalper:daily_total_cap"

    reentry_rules = config.reentry
    if ctx.reentry_count >= reentry_rules.max_trades_per_move:
        return False, "scalper:reentry_cap"

    if (
        ctx.minutes_since_last_same_direction is not None
        and ctx.minutes_since_last_same_direction < reentry_rules.min_minutes_between_wins
    ):
        return False, "scalper:cooldown_active"

    if (
        ctx.minutes_since_last_loss is not None
        and ctx.minutes_since_last_loss < reentry_rules.min_minutes_after_loss
    ):
        return False, "scalper:loss_cooldown"

    if ctx.spread_points is not None and ctx.spread_points > sym_cfg.max_spread_points:
        return False, "scalper:spread_exceeded"

    if ctx.slippage_points is not None and ctx.slippage_points > sym_cfg.max_slippage_points:
        return False, "scalper:slippage_exceeded"

    if (
        ctx.floating_symbol_risk_pct is not None
        and ctx.floating_symbol_risk_pct > risk.max_symbol_risk_pct
    ):
        return False, "scalper:floating_risk_cap"

    return True, "ok"
