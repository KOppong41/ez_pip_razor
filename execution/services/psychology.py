"""Psychology and allocation controls for bot runtime state.

This module keeps trading-psychology decisions separate from model validation.
Runtime-maintained fields are persisted with queryset updates so an unrelated
broker-account validation failure cannot prevent loss-streak or allocation
state from being recorded.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import timedelta
from decimal import Decimal

from django.db import transaction
from django.db.models import Sum
from django.utils import timezone

from execution.models import ExecutionSetting, TradeLog
from execution.services.journal import log_journal_event


ZERO = Decimal("0")
ONE = Decimal("1")
HUNDRED = Decimal("100")


@dataclass
class SizeAdjustment:
    multiplier: Decimal = ONE


def _as_decimal(value, default: Decimal = ZERO) -> Decimal:
    """Convert a value to Decimal while preserving a safe fallback."""
    try:
        return Decimal(str(value if value is not None else default))
    except (TypeError, ValueError, ArithmeticError):
        return default


def _positive_min(*values: Decimal) -> Decimal:
    """Return the smallest positive value, or zero when none are positive."""
    positives = [value for value in values if value > ZERO]
    return min(positives) if positives else ZERO


def _positive_max(*values: int) -> int:
    """Return the largest positive integer, or zero when none are positive."""
    positives = [int(value) for value in values if value and int(value) > 0]
    return max(positives) if positives else 0


def _persist_runtime_fields(bot, field_names: list[str] | tuple[str, ...]) -> None:
    """Persist engine-owned fields without invoking Bot.full_clean().

    These callers only mutate existing runtime state. Using QuerySet.update()
    avoids coupling psychology bookkeeping to unrelated broker/account
    validation performed by Bot.save().
    """
    if not bot or not getattr(bot, "pk", None) or not field_names:
        return

    values = {field: getattr(bot, field) for field in field_names}
    type(bot).objects.filter(pk=bot.pk).update(**values)


def _get_settings() -> ExecutionSetting | None:
    try:
        return ExecutionSetting.objects.first()
    except Exception:
        return None


def _set_allocation_guard(bot, payload: dict) -> bool:
    """Persist the allocation guard only when its reason/cap changes."""
    if not hasattr(bot, "scalper_params"):
        return True

    params = dict(bot.scalper_params or {})
    existing = params.get("_allocation_guard")
    if (
        existing
        and existing.get("reason") == payload.get("reason")
        and existing.get("cap") == payload.get("cap")
    ):
        return False

    params["_allocation_guard"] = payload
    bot.scalper_params = params
    _persist_runtime_fields(bot, ["scalper_params"])
    return True


def _clear_allocation_guard(bot) -> None:
    if not hasattr(bot, "scalper_params"):
        return

    params = dict(bot.scalper_params or {})
    if "_allocation_guard" not in params:
        return

    params.pop("_allocation_guard", None)
    bot.scalper_params = params
    _persist_runtime_fields(bot, ["scalper_params"])


def _allocation_amount(bot) -> Decimal:
    return _as_decimal(getattr(bot, "allocation_amount", ZERO))


def reset_allocation_cycle(
    bot,
    *,
    reason: str = "manual",
    log_event: bool = True,
) -> bool:
    """Reset the allocation baseline to the bot's current lifetime PnL."""
    if not bot:
        return False

    allocation = _allocation_amount(bot)
    if allocation <= ZERO:
        return False

    lifetime = _get_lifetime_realized_pnl(bot)
    bot.allocation_start_pnl = lifetime
    bot.allocation_started_at = timezone.now()
    _persist_runtime_fields(
        bot,
        ["allocation_start_pnl", "allocation_started_at"],
    )
    _clear_allocation_guard(bot)

    if log_event:
        symbol = getattr(getattr(bot, "asset", None), "symbol", None)
        log_journal_event(
            "allocation.cycle_reset",
            bot=bot,
            symbol=symbol,
            owner=getattr(bot, "owner", None),
            message=f"{bot.name} allocation cycle reset",
            context={
                "reason": reason,
                "allocation_amount": str(allocation),
            },
        )

    return True


def _maybe_reset_allocation_cycle(bot) -> None:
    if not bot or _allocation_amount(bot) <= ZERO:
        return

    started_at = getattr(bot, "allocation_started_at", None)
    if not started_at or started_at.date() < timezone.localdate():
        reset_allocation_cycle(bot, reason="daily_reset")


def _get_broker_balance_decimal(bot) -> Decimal | None:
    account = getattr(bot, "broker_account", None)
    if not account:
        return None

    try:
        from execution.services.accounts import get_account_balances  # noqa: WPS433
    except Exception:
        return None

    try:
        data = get_account_balances(account)
    except Exception:
        return None

    balance = data.get("balance") if isinstance(data, dict) else None
    if balance is None:
        return None

    try:
        return Decimal(str(balance))
    except (TypeError, ValueError, ArithmeticError):
        return None


def _stop_bot(
    bot,
    *,
    event_type: str,
    message: str,
    context: dict | None = None,
    new_baseline: Decimal | None = None,
) -> None:
    if not bot:
        return

    if hasattr(bot, "schedule_paused"):
        from execution.services.bot_schedule import set_bot_status

        set_bot_status(bot, "stopped")

    update_fields: list[str] = []

    if getattr(bot, "status", None) != "stopped":
        bot.status = "stopped"
        update_fields.append("status")

    if hasattr(bot, "paused_until"):
        bot.paused_until = None
        update_fields.append("paused_until")

    if new_baseline is None:
        new_baseline = _get_lifetime_realized_pnl(bot)

    if hasattr(bot, "allocation_start_pnl"):
        bot.allocation_start_pnl = new_baseline
        update_fields.append("allocation_start_pnl")

    if hasattr(bot, "allocation_started_at"):
        bot.allocation_started_at = None
        update_fields.append("allocation_started_at")

    _persist_runtime_fields(bot, update_fields)

    symbol = getattr(getattr(bot, "asset", None), "symbol", None)
    log_journal_event(
        event_type,
        severity="warning",
        bot=bot,
        broker_account=getattr(bot, "broker_account", None),
        symbol=symbol,
        owner=getattr(bot, "owner", None),
        message=message,
        context=context or {},
    )


def _effective_loss_streak_policy(
    bot,
    settings: ExecutionSetting | None,
) -> tuple[int, int]:
    """Return effective max-loss streak and cooldown in minutes."""
    global_max = (
        int(getattr(settings, "max_loss_streak_before_pause", 0) or 0)
        if settings
        else 0
    )
    global_cooldown = (
        int(getattr(settings, "loss_streak_cooldown_min", 0) or 0)
        if settings
        else 0
    )

    bot_enabled = bool(getattr(bot, "loss_streak_autopause_enabled", False))
    bot_max = int(getattr(bot, "max_loss_streak_before_pause", 0) or 0)
    bot_cooldown = int(getattr(bot, "loss_streak_cooldown_min", 0) or 0)

    effective_max = global_max if global_max > 0 else 0
    if bot_enabled and bot_max > 0:
        effective_max = (
            min(effective_max, bot_max)
            if effective_max > 0
            else bot_max
        )

    effective_cooldown = _positive_max(global_cooldown, bot_cooldown)
    return effective_max, effective_cooldown


def update_bot_after_realized_pnl(order, realized_pnl: Decimal) -> None:
    """Update loss streak and apply a configured loss-streak pause."""
    bot = getattr(order, "bot", None)
    if not bot:
        return

    effective_max, effective_cooldown = _effective_loss_streak_policy(
        bot,
        _get_settings(),
    )
    if effective_max <= 0:
        return

    _record_loss_streak(
        bot.pk,
        _as_decimal(realized_pnl),
        effective_max,
        effective_cooldown,
    )


@transaction.atomic
def _record_loss_streak(
    bot_id,
    realized_pnl: Decimal,
    effective_max: int,
    effective_cooldown: int,
) -> None:
    from bots.models import Bot

    bot = Bot.objects.select_for_update().get(pk=bot_id)

    streak = int(getattr(bot, "current_loss_streak", 0) or 0)
    if realized_pnl < ZERO:
        streak += 1
    elif realized_pnl > ZERO:
        streak = 0

    bot.current_loss_streak = streak
    update_fields = ["current_loss_streak"]

    should_pause = (
        streak >= effective_max
        and (bot.status == "active" or bot.schedule_paused)
        and not bot.kill_switch_triggered_at
    )

    if should_pause:
        if hasattr(bot, "schedule_paused"):
            from execution.services.bot_schedule import set_bot_status

            set_bot_status(bot, "paused")

        bot.status = "paused"
        bot.pause_reason = (
            "loss_cooldown"
            if effective_cooldown > 0
            else "loss_lock"
        )
        bot.paused_until = (
            timezone.now() + timedelta(minutes=effective_cooldown)
            if effective_cooldown > 0
            else None
        )
        update_fields.extend(
            ["status", "pause_reason", "paused_until"],
        )

        params = dict(bot.scalper_params or {})
        if effective_cooldown > 0:
            params["_loss_pause_owner"] = {
                "until": bot.paused_until.isoformat(),
            }
        else:
            params.pop("_loss_pause_owner", None)

        bot.scalper_params = params
        update_fields.append("scalper_params")

    _persist_runtime_fields(bot, update_fields)


def _realized_pnl_sum(queryset) -> Decimal:
    total = (
        queryset.exclude(pnl__isnull=True)
        .aggregate(total=Sum("pnl"))
        .get("total")
    )
    return total or ZERO


def _get_today_realized_pnl(bot) -> Decimal:
    """Return today's realized PnL for a bot across all symbols."""
    if not bot:
        return ZERO

    return _realized_pnl_sum(
        TradeLog.objects.filter(
            bot=bot,
            created_at__date=timezone.localdate(),
        )
    )


def _get_lifetime_realized_pnl(bot) -> Decimal:
    """Return lifetime realized PnL for a bot."""
    if not bot:
        return ZERO

    return _realized_pnl_sum(TradeLog.objects.filter(bot=bot))


def _get_allocation_cycle_pnl(bot) -> Decimal:
    """Return realized PnL relative to the allocation-cycle baseline."""
    baseline = _as_decimal(
        getattr(bot, "allocation_start_pnl", ZERO),
    )
    return _get_lifetime_realized_pnl(bot) - baseline


def _settings_decimal(
    settings: ExecutionSetting | None,
    field: str,
    default: Decimal,
) -> Decimal:
    if settings is None:
        return default
    return _as_decimal(getattr(settings, field, default), default)


def get_size_multiplier(bot) -> Decimal:
    """Compute the effective drawdown-based position-size multiplier."""
    if not bot:
        return ONE

    settings = _get_settings()

    global_soft = _settings_decimal(
        settings,
        "drawdown_soft_limit_pct",
        ZERO,
    )
    global_hard = _settings_decimal(
        settings,
        "drawdown_hard_limit_pct",
        ZERO,
    )
    global_soft_multiplier = _settings_decimal(
        settings,
        "soft_size_multiplier",
        ONE,
    )
    global_hard_multiplier = _settings_decimal(
        settings,
        "hard_size_multiplier",
        ONE,
    )

    bot_soft = _as_decimal(
        getattr(bot, "soft_drawdown_limit_pct", ZERO),
    )
    bot_hard = _as_decimal(
        getattr(bot, "hard_drawdown_limit_pct", ZERO),
    )
    bot_soft_multiplier = _as_decimal(
        getattr(bot, "soft_size_multiplier", ONE),
        ONE,
    )
    bot_hard_multiplier = _as_decimal(
        getattr(bot, "hard_size_multiplier", ONE),
        ONE,
    )

    soft_limit = _positive_min(global_soft, bot_soft)
    hard_limit = _positive_min(global_hard, bot_hard)

    if soft_limit <= ZERO and hard_limit <= ZERO:
        return ONE

    soft_multiplier = _positive_min(
        global_soft_multiplier,
        bot_soft_multiplier,
    )
    hard_multiplier = _positive_min(
        global_hard_multiplier,
        bot_hard_multiplier,
    )

    if soft_multiplier <= ZERO:
        soft_multiplier = ONE
    if hard_multiplier <= ZERO:
        hard_multiplier = ONE

    realized_today = _get_today_realized_pnl(bot)
    if realized_today >= ZERO:
        return ONE

    account = getattr(bot, "broker_account", None)
    if account and account.connector != "paper":
        from execution.models import AccountRiskDay
        from execution.services.daily_risk import risk_day_window

        risk_day = AccountRiskDay.objects.filter(
            broker_account=account,
            risk_date=risk_day_window(account).risk_date,
            baseline_locked=True,
        ).first()
        start_balance = risk_day.starting_equity if risk_day else None
        if start_balance is None or start_balance <= ZERO:
            # Missing live account equity must not silently inherit paper capital.
            enabled = [multiplier for limit, multiplier in (
                (soft_limit, soft_multiplier), (hard_limit, hard_multiplier),
            ) if limit > ZERO]
            return min(enabled) if enabled else ONE
    else:
        start_balance = _settings_decimal(
            settings,
            "paper_start_balance",
            Decimal("100000"),
        )
        if start_balance <= ZERO:
            return ONE

    drawdown_pct = (-realized_today / start_balance) * HUNDRED

    if hard_limit > ZERO and drawdown_pct >= hard_limit:
        return hard_multiplier
    if soft_limit > ZERO and drawdown_pct >= soft_limit:
        return soft_multiplier

    return ONE


def _market_is_open(bot) -> bool:
    """Return market availability while preserving the existing fallback."""
    try:
        from execution.services.market_hours import get_market_status_for_bot  # noqa: WPS433
    except Exception:
        return True

    market_status = get_market_status_for_bot(
        bot,
        use_mt5_probe=False,
    )
    return not market_status or bool(market_status.is_open)


def _allocation_guard_hit(
    bot,
    allocation: Decimal,
) -> tuple[str | None, Decimal | None, Decimal]:
    """Return allocation guard reason, cap and current cycle PnL."""
    realized_cycle = _get_allocation_cycle_pnl(bot)

    loss_pct = _as_decimal(
        getattr(bot, "allocation_loss_pct", HUNDRED),
        HUNDRED,
    )
    if loss_pct <= ZERO:
        loss_pct = ZERO

    loss_cap = (
        allocation * loss_pct / HUNDRED
        if loss_pct > ZERO
        else allocation
    )

    profit_pct = _as_decimal(
        getattr(bot, "allocation_profit_pct", ZERO),
    )
    profit_cap = (
        allocation * profit_pct / HUNDRED
        if profit_pct > ZERO
        else ZERO
    )

    if loss_cap > ZERO and realized_cycle <= -loss_cap:
        return "loss", loss_cap, realized_cycle
    if profit_cap > ZERO and realized_cycle >= profit_cap:
        return "profit", profit_cap, realized_cycle

    return None, None, realized_cycle


def _apply_allocation_cap_guard(
    bot,
    allocation: Decimal,
) -> bool:
    """Stop the bot when its allocation profit/loss cap has been reached."""
    reason, cap, realized_cycle = _allocation_guard_hit(
        bot,
        allocation,
    )
    if reason is None or cap is None:
        _clear_allocation_guard(bot)
        return False

    profit_pct = _as_decimal(
        getattr(bot, "allocation_profit_pct", ZERO),
    )
    loss_pct = _as_decimal(
        getattr(bot, "allocation_loss_pct", HUNDRED),
        HUNDRED,
    )
    if loss_pct <= ZERO:
        loss_pct = ZERO

    payload = {
        "reason": reason,
        "cap": str(cap),
        "cycle_pnl": str(realized_cycle),
        "at": timezone.now().isoformat(),
    }
    _set_allocation_guard(bot, payload)

    baseline = _as_decimal(
        getattr(bot, "allocation_start_pnl", ZERO),
    )
    _stop_bot(
        bot,
        event_type="allocation.cap_hit",
        message=f"{bot.name} allocation {reason} cap reached; bot stopped",
        context={
            "cycle_pnl": str(realized_cycle),
            "cap": str(cap),
            "allocation_amount": str(allocation),
            "allocation_profit_pct": str(profit_pct),
            "allocation_loss_pct": str(loss_pct),
            "reason": reason,
        },
        new_baseline=baseline + realized_cycle,
    )
    return True


def bot_is_available_for_trading(bot) -> bool:
    """Return whether a bot currently passes psychology/allocation gates."""
    if not bot:
        return False

    _maybe_reset_allocation_cycle(bot)

    if getattr(bot, "status", None) != "active":
        return False

    paused_until = getattr(bot, "paused_until", None)
    if paused_until and timezone.now() < paused_until:
        return False

    if not _market_is_open(bot):
        return False

    allocation = _allocation_amount(bot)
    if allocation <= ZERO:
        return True

    broker_balance = _get_broker_balance_decimal(bot)
    if broker_balance is not None and allocation > broker_balance:
        _stop_bot(
            bot,
            event_type="allocation.balance_insufficient",
            message=f"{bot.name} stopped: insufficient trading balance",
            context={
                "required_allocation": str(allocation),
                "account_balance": str(broker_balance),
            },
        )
        return False

    if _apply_allocation_cap_guard(bot, allocation):
        return False

    return True
