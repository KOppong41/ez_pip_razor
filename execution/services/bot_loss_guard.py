"""Per-bot floating-loss stops, attributed by durable broker position tickets."""
from decimal import Decimal

from django.db import transaction
from django.utils import timezone

from bots.models import Bot
from execution.models import BrokerPosition
from execution.services.journal import log_journal_event


def _finite(value):
    result = Decimal(str(value))
    if not result.is_finite():
        raise ValueError("Nonfinite broker financial value")
    return result


def latch_bot_losses(account, account_info, broker_positions):
    """Latch triggered bots until explicit restart; keep retrying their cleanup.

    The denominator is the bot's virtual bankroll when configured, otherwise
    current broker balance. Fresh broker profit/swap is used, never stale PnL.
    """
    if broker_positions is None:
        raise ValueError("Broker positions unavailable for bot loss monitoring")
    raw_by_ticket = {int(p.ticket): p for p in broker_positions}
    results = []
    bot_ids = list(Bot.objects.filter(broker_account=account, kill_switch_enabled=True).values_list("pk", flat=True))
    for bot_id in bot_ids:
        with transaction.atomic():
            type(account).objects.select_for_update().get(pk=account.pk)
            bot = Bot.objects.select_for_update().get(pk=bot_id)
            if not bot.kill_switch_enabled:
                continue
            owned = list(BrokerPosition.objects.filter(broker_account=account, bot=bot, ownership="ez_trade",
                                                       status__in=("open", "missing")))
            fresh = [(p, raw_by_ticket[p.broker_position_ticket]) for p in owned if p.broker_position_ticket in raw_by_ticket]
            pnl = sum((_finite(raw.profit) + _finite(getattr(raw, "swap", 0)) for _, raw in fresh), Decimal(0))
            capital = _finite(bot.allocation_amount) if bot.allocation_amount > 0 else _finite(account_info.balance)
            threshold = _finite(bot.kill_switch_max_unrealized_pct)
            breached = bool(fresh) and capital > 0 and threshold > 0 and -pnl * 100 >= capital * threshold
            if not breached and not bot.kill_switch_triggered_at:
                continue
            new_trigger = bot.kill_switch_triggered_at is None
            bot.kill_switch_triggered_at = bot.kill_switch_triggered_at or timezone.now()
            bot.status, bot.schedule_paused = "stopped", False
            # Emergency state must not depend on unrelated model validation
            # after an operator has tightened an account limit.
            Bot.objects.filter(pk=bot.pk).update(kill_switch_triggered_at=bot.kill_switch_triggered_at,
                                                status="stopped", schedule_paused=False)
            for position, raw in fresh:
                position.volume = _finite(raw.volume)
                position.profit = _finite(raw.profit)
                position.swap = _finite(getattr(raw, "swap", 0))
                position.status = "open"
                position.last_reconciled_at = timezone.now()
                position.save(update_fields=["volume", "profit", "swap", "status", "last_reconciled_at"])
            if new_trigger:
                log_journal_event("bot.kill_switch_triggered", severity="error", bot=bot, broker_account=account,
                                  message="Bot floating-loss limit reached; entries stopped and owned exits requested",
                                  context={"floating_pnl": str(pnl), "capital_basis": str(capital),
                                           "limit_pct": str(threshold), "basis": "allocation" if bot.allocation_amount > 0 else "account_balance"})
            results.append((bot, [p for p, _ in fresh]))
    return results
