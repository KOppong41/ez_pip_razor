from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

from django.db.models import Sum
from django.utils import timezone

from execution.models import ExecutionSetting, TradeLog


@dataclass
class SizeAdjustment:
    multiplier: Decimal = Decimal("1")


def _get_settings() -> ExecutionSetting | None:
    try:
        return ExecutionSetting.objects.first()
    except Exception:
        return None


def update_bot_after_realized_pnl(order, realized_pnl: Decimal) -> None:
    """
    Update a bot's loss streak and optionally auto-pause it after consecutive losses.

    This is intentionally conservative:
    - Feature is disabled unless max_loss_streak_before_pause > 0.
    - Only trades with a non-zero realized PnL affect the streak.
    """
    bot = getattr(order, "bot", None)
    if not bot:
        return

    settings = _get_settings()
    # Global bounds (superuser-controlled)
    global_max = int(getattr(settings, "max_loss_streak_before_pause", 0) or 0) if settings else 0
    global_cd = int(getattr(settings, "loss_streak_cooldown_min", 0) or 0) if settings else 0

    # Per-bot configuration (user/profile controlled)
    bot_enabled = bool(getattr(bot, "loss_streak_autopause_enabled", False))
    bot_max = int(getattr(bot, "max_loss_streak_before_pause", 0) or 0)
    bot_cd = int(getattr(bot, "loss_streak_cooldown_min", 0) or 0)

    # Decide whether loss-streak auto-pause is active at all:
    # - if global_max > 0, it acts as a hard safety floor regardless of bot flag.
    # - bot can opt in with its own lower threshold, but not relax the global.
    effective_max = 0
    if global_max > 0:
        effective_max = global_max
    if bot_enabled and bot_max > 0:
        effective_max = min(effective_max, bot_max) if effective_max > 0 else bot_max
    if effective_max <= 0:
        # No streak-based pause configured.
        return

    # Cool-down: use the longest of global/bot values so cooling is never shorter than the platform floor.
    effective_cd = 0
    for v in (global_cd, bot_cd):
        if v and v > 0:
            effective_cd = max(effective_cd, v)
    if effective_cd <= 0:
        # No sensible cool-down available; keep streak stats but don't auto-pause.
        effective_cd = 0

    # Normalize to Decimal for safe comparisons
    realized_pnl = Decimal(str(realized_pnl))

    # Update streak: increment on loss, reset on win; breakeven leaves it unchanged.
    streak = getattr(bot, "current_loss_streak", 0) or 0
    if realized_pnl < 0:
        streak += 1
    elif realized_pnl > 0:
        streak = 0

    bot.current_loss_streak = streak

    # Auto-pause when streak exceeded.
    if streak >= effective_max and effective_cd > 0:
        bot.status = "paused"
        bot.paused_until = timezone.now() + timezone.timedelta(minutes=effective_cd)

    # Persist minimal fields; status/bot_id already part of the model.
    update_fields = ["current_loss_streak"]
    if hasattr(bot, "paused_until"):
        update_fields.append("paused_until")
    if hasattr(bot, "status"):
        update_fields.append("status")

    bot.save(update_fields=update_fields)


def _get_today_realized_pnl(bot) -> Decimal:
    """
    Sum of today's realized PnL for a bot across all symbols.
    Uses TradeLog rows where pnl is populated.
    """
    if not bot:
        return Decimal("0")
    today = timezone.localdate()
    agg = (
        TradeLog.objects.filter(
            bot=bot,
            created_at__date=today,
        )
        .exclude(pnl__isnull=True)
        .aggregate(total=Sum("pnl"))
    )
    return agg["total"] or Decimal("0")


def get_size_multiplier(bot) -> Decimal:
    """
    Compute a size multiplier based on daily drawdown.

    By default this is neutral (1x) until the admin enables the
    soft/hard limits on the ExecutionSetting singleton.
    """
    settings = _get_settings()
    if not bot:
        return Decimal("1")

    # Global thresholds and multipliers
    g_soft = Decimal(str(getattr(settings, "drawdown_soft_limit_pct", Decimal("0")) or 0)) if settings else Decimal("0")
    g_hard = Decimal(str(getattr(settings, "drawdown_hard_limit_pct", Decimal("0")) or 0)) if settings else Decimal("0")
    g_soft_mult = Decimal(str(getattr(settings, "soft_size_multiplier", Decimal("1")) or 1)) if settings else Decimal("1")
    g_hard_mult = Decimal(str(getattr(settings, "hard_size_multiplier", Decimal("1")) or 1)) if settings else Decimal("1")

    # Per-bot overrides (more conservative only)
    b_soft = Decimal(str(getattr(bot, "soft_drawdown_limit_pct", Decimal("0")) or 0))
    b_hard = Decimal(str(getattr(bot, "hard_drawdown_limit_pct", Decimal("0")) or 0))
    b_soft_mult = Decimal(str(getattr(bot, "soft_size_multiplier", Decimal("1")) or 1))
    b_hard_mult = Decimal(str(getattr(bot, "hard_size_multiplier", Decimal("1")) or 1))

    # Effective thresholds: pick the most conservative (smallest) positive value.
    soft_candidates = [v for v in (g_soft, b_soft) if v > 0]
    hard_candidates = [v for v in (g_hard, b_hard) if v > 0]
    soft = min(soft_candidates) if soft_candidates else Decimal("0")
    hard = min(hard_candidates) if hard_candidates else Decimal("0")

    # Effective multipliers: pick the smallest (most conservative) positive multiplier.
    soft_mult_candidates = [v for v in (g_soft_mult, b_soft_mult) if v > 0]
    hard_mult_candidates = [v for v in (g_hard_mult, b_hard_mult) if v > 0]
    soft_mult = min(soft_mult_candidates) if soft_mult_candidates else Decimal("1")
    hard_mult = min(hard_mult_candidates) if hard_mult_candidates else Decimal("1")

    # If all limits are effectively disabled, keep behavior unchanged.
    if soft <= 0 and hard <= 0:
        return Decimal("1")

    # Approximate equity as paper_start_balance; for live we would want real balances.
    start_balance = Decimal(str(getattr(settings, "paper_start_balance", Decimal("100000")))) if settings else Decimal("100000")
    if start_balance <= 0:
        return Decimal("1")

    realized_today = _get_today_realized_pnl(bot)
    if realized_today >= 0:
        return Decimal("1")

    # Drawdown in percent of starting balance.
    dd_pct = (-realized_today / start_balance) * Decimal("100")

    # Hard limit has priority; if both are zero or multipliers are 1, this is a no-op.
    if hard > 0 and dd_pct >= hard:
        return hard_mult
    if soft > 0 and dd_pct >= soft:
        return soft_mult
    return Decimal("1")


def bot_is_available_for_trading(bot) -> bool:
    """
    Centralised guard for whether a bot should be allowed to trade.
    - status must be 'active'
    - paused_until (if set) must be in the past
    """
    if not bot:
        return False
    if getattr(bot, "status", None) != "active":
        return False
    paused_until = getattr(bot, "paused_until", None)
    if paused_until:
        if timezone.now() < paused_until:
            return False
    return True
