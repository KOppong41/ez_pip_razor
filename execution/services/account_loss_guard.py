"""Durable account-stop transitions; cleanup remains retryable on every scan."""
from django.db import transaction
from django.utils import timezone

from execution.models import RiskPolicy
from execution.services.journal import log_journal_event


@transaction.atomic
def latch_account_loss(account, *, daily_loss_pct, drawdown_pct,
                       daily_baseline_source, daily_baseline_locked):
    # Match final entry admission and operator controls: account before policy.
    type(account).objects.select_for_update().get(pk=account.pk)
    policy = RiskPolicy.objects.select_for_update().get(broker_account=account)
    reason = None
    if policy.emergency_stop:
        reason = "explicit_emergency_stop"
    elif policy.max_daily_loss_pct > 0 and daily_loss_pct is not None and daily_loss_pct >= policy.max_daily_loss_pct:
        reason = "maximum_daily_loss"
    elif policy.max_account_drawdown_pct > 0 and drawdown_pct >= policy.max_account_drawdown_pct:
        reason = "maximum_account_drawdown"
    if reason is None:
        return None

    new_trigger = policy.emergency_stop_triggered_at is None
    policy.entries_enabled = False
    policy.emergency_stop = True
    policy.emergency_stop_triggered_at = policy.emergency_stop_triggered_at or timezone.now()
    policy.save(update_fields=["entries_enabled", "emergency_stop", "emergency_stop_triggered_at", "updated_at"])
    if new_trigger:
        log_journal_event(
            "kill_switch.triggered", severity="error", broker_account=account, owner=account.owner,
            message=f"Kill switch triggered: {reason}",
            context={
                "daily_loss_pct": str(daily_loss_pct) if daily_loss_pct is not None else None,
                "drawdown_pct": str(drawdown_pct),
                "daily_baseline_source": daily_baseline_source,
                "daily_baseline_locked": daily_baseline_locked,
                "triggered_at": policy.emergency_stop_triggered_at.isoformat(),
            },
        )
    return reason
