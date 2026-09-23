"""Trading-window state transitions; never starts manually paused/stopped bots."""

import logging

from django.db import transaction
from django.db.models import Q
from django.utils import timezone

from bots.models import Bot
from execution.models import RiskPolicy
from execution.services.journal import log_journal_event
from execution.services.market_hours import get_market_status_for_bot
from execution.services.trading_type import is_within_trading_window

logger = logging.getLogger(__name__)


def can_automatically_resume(bot, now):
    if bot.kill_switch_triggered_at:
        return False
    if bot.paused_until and bot.paused_until > now:
        return False
    if not bot.asset_id or not bot.asset.is_active:
        return False
    if not bot.broker_account_id or not bot.broker_account.is_active:
        return False
    if RiskPolicy.objects.filter(broker_account_id=bot.broker_account_id).filter(
        Q(entries_enabled=False) | Q(emergency_stop=True)
    ).exists():
        return False
    # Calendar only: the serialized execution worker checks broker availability
    # and all entry risk limits before placing an order.
    return get_market_status_for_bot(bot, now=now, use_mt5_probe=False).is_open


@transaction.atomic
def reconcile_bot_schedule(bot_id, *, now=None):
    now = now or timezone.now()
    bot = Bot.objects.select_for_update().filter(pk=bot_id).first()
    if bot is None:
        return None
    in_window = is_within_trading_window(bot, now)
    if bot.status == "active" and not in_window:
        new_status, schedule_paused, event = "paused", True, "paused"
    elif bot.status == "paused" and bot.schedule_paused and in_window:
        if not can_automatically_resume(bot, now):
            return None
        new_status, schedule_paused, event = "active", False, "resumed"
    else:
        return None
    Bot.objects.filter(pk=bot.pk).update(status=new_status, schedule_paused=schedule_paused)
    log_journal_event(
        f"bot.schedule_{event}",
        bot=bot,
        symbol=bot.asset.symbol if bot.asset_id else None,
        message=f"{bot.name} {event} by its trading schedule",
        context={"status": new_status, "checked_at": now.isoformat()},
    )
    return event


def reconcile_trading_schedules(*, now=None, queryset=None):
    now = now or timezone.now()
    bots = queryset if queryset is not None else Bot.objects.all()
    bot_ids = bots.filter(
        Q(status="active") | Q(status="paused", schedule_paused=True)
    ).values_list("pk", flat=True)
    counts = {"paused": 0, "resumed": 0, "errors": 0}
    for bot_id in list(bot_ids):
        try:
            event = reconcile_bot_schedule(bot_id, now=now)
            if event:
                counts[event] += 1
        except Exception:
            counts["errors"] += 1
            logger.exception("Trading schedule check failed for bot=%s", bot_id)
    return counts


@transaction.atomic
def set_bot_status(bot, status):
    """Explicit user/risk intent cancels any automatic-resume ownership."""
    current = Bot.objects.select_for_update().get(pk=bot.pk)
    params = dict(current.scalper_params or {})
    fields = {"status": status, "schedule_paused": False}
    if status == "active":
        # Only an explicit operator start resets the bot's loss-stop latch.
        fields["kill_switch_triggered_at"] = None
    if "_market_guard" in params:
        params.pop("_market_guard")
        fields["scalper_params"] = params
    Bot.objects.filter(pk=bot.pk).update(**fields)
    if status == "active":
        reconcile_bot_schedule(bot.pk)
    bot.refresh_from_db(fields=["status", "schedule_paused", "scalper_params", "kill_switch_triggered_at"])


def set_bots_status(queryset, status):
    count = 0
    for bot in queryset.order_by("pk"):
        set_bot_status(bot, status)
        count += 1
    return count
