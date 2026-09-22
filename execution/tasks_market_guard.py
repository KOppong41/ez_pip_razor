import logging

from django.db import transaction
from django.db.models import Q
from django.utils import timezone

from bots.models import Bot
from execution.services.market_hours import get_market_status_for_bot, is_crypto_symbol
from execution.services.bot_schedule import can_automatically_resume
from execution.services.trading_type import is_within_trading_window

logger = logging.getLogger(__name__)


@transaction.atomic
def _apply_market_status(bot_id, status, now):
    # Re-read under lock: a user/risk pause during a broker probe wins.
    bot = Bot.objects.select_for_update().get(pk=bot_id)
    params = dict(bot.scalper_params or {})
    guard_flag = params.get("_market_guard")
    if bot.status == "active" and not is_within_trading_window(bot, now):
        Bot.objects.filter(pk=bot.pk).update(status="paused", schedule_paused=True)
        return "paused"
    if not status.is_open and bot.status == "active":
        params["_market_guard"] = {"was": "active", "reason": status.reason}
        Bot.objects.filter(pk=bot.pk).update(
            status="stopped", schedule_paused=False, scalper_params=params,
        )
        return "stopped"
    if status.is_open and guard_flag and bot.status == "stopped":
        if not can_automatically_resume(bot, now):
            return None
        in_window = is_within_trading_window(bot, now)
        params.pop("_market_guard")
        Bot.objects.filter(pk=bot.pk).update(
            status="active" if in_window else "paused",
            schedule_paused=not in_window,
            scalper_params=params,
        )
        return "resumed" if in_window else None
    return None


def apply_market_guard() -> dict:
    """
    Scan bots and auto-stop those whose market is closed, restoring them when open.
    Only affects bots we auto-stopped (tracked via scalper_params['_market_guard']).
    """
    bots_qs = Bot.objects.select_related("asset", "broker_account").filter(auto_trade=True).filter(
        Q(status="active") | Q(status="stopped", scalper_params__has_key="_market_guard")
    )
    stopped = 0
    resumed = 0
    skipped_crypto = 0
    skipped_no_asset = 0
    errors = []
    now = timezone.now()

    for bot in bots_qs:
        symbol = getattr(getattr(bot, "asset", None), "symbol", None)
        if not symbol:
            skipped_no_asset += 1
            continue
        crypto = is_crypto_symbol(symbol)
        if crypto:
            skipped_crypto += 1
        try:
            status = get_market_status_for_bot(bot, now=now, use_mt5_probe=not crypto)
            if status:
                event = _apply_market_status(bot.pk, status, now)
                stopped += event == "stopped"
                resumed += event == "resumed"
        except Exception as exc:
            errors.append((bot.id, str(exc)))
            logger.exception("[MarketGuard] status check failed bot=%s", bot.id)
            continue

    return {
        "stopped": stopped,
        "resumed": resumed,
        "skipped_crypto": skipped_crypto,
        "skipped_no_asset": skipped_no_asset,
        "errors": errors,
    }
