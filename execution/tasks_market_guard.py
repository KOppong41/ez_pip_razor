import logging

from django.utils import timezone

from bots.models import Bot
from execution.services.market_hours import (
    get_market_status_for_bot,
    maybe_pause_bot_for_market,
    maybe_unpause_crypto_for_open_market,
    is_crypto_symbol,
)

logger = logging.getLogger(__name__)


def apply_market_guard() -> dict:
    """
    Scan bots and auto-stop those whose market is closed, restoring them when open.
    Only affects bots we auto-stopped (tracked via scalper_params['_market_guard']).
    """
    bots_qs = Bot.objects.select_related("asset", "broker_account").filter(auto_trade=True)
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
        guard_flag = (bot.scalper_params or {}).get("_market_guard")
        if is_crypto_symbol(symbol):
            # Crypto is 24/7; ensure any accidental auto-stop is reverted.
            status = get_market_status_for_bot(bot, now=now, use_mt5_probe=False)
            maybe_unpause_crypto_for_open_market(bot, status)
            if guard_flag and getattr(bot, "status", None) == "stopped":
                sp = bot.scalper_params or {}
                sp.pop("_market_guard", None)
                bot.scalper_params = sp
                bot.status = guard_flag.get("was", "active")
                bot.paused_until = None
                bot.save(update_fields=["scalper_params", "status", "paused_until"])
                resumed += 1
            skipped_crypto += 1
            continue

        try:
            status = get_market_status_for_bot(bot, now=now, use_mt5_probe=True)
        except Exception as exc:
            errors.append((bot.id, str(exc)))
            logger.exception("[MarketGuard] status check failed bot=%s", bot.id)
            continue

        if status and not status.is_open:
            # Auto-stop only if currently active.
            if getattr(bot, "status", None) == "active":
                sp = bot.scalper_params or {}
                sp["_market_guard"] = {"was": "active", "reason": status.reason}
                bot.scalper_params = sp
                bot.status = "stopped"
                bot.save(update_fields=["scalper_params", "status"])
                maybe_pause_bot_for_market(bot, status)
                stopped += 1
        elif status and status.is_open:
            # Resume only if we previously auto-stopped it.
            if guard_flag and getattr(bot, "status", None) == "stopped":
                sp = bot.scalper_params or {}
                sp.pop("_market_guard", None)
                bot.scalper_params = sp
                bot.status = guard_flag.get("was", "active")
                bot.paused_until = None
                bot.save(update_fields=["scalper_params", "status", "paused_until"])
                resumed += 1

    return {
        "stopped": stopped,
        "resumed": resumed,
        "skipped_crypto": skipped_crypto,
        "skipped_no_asset": skipped_no_asset,
        "errors": errors,
    }
