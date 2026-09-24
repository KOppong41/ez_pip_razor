"""Separate risk-limit edits from explicit account operating-state changes."""
from django.core.exceptions import ValidationError
from django.db import transaction

from brokers.models import BrokerAccount
from execution.models import RiskPolicy


RISK_LIMIT_FIELDS = frozenset({
    "max_daily_loss_pct", "max_account_drawdown_pct", "max_total_open_positions",
    "max_positions_per_symbol", "max_order_lot_size", "max_aggregate_open_lots",
    "stop_after_daily_profit_pct",
})


def _locked_policy(account):
    # Call only within an atomic operation. This matches admission and monitors.
    BrokerAccount.objects.select_for_update().get(pk=account.pk)
    policy, _ = RiskPolicy.objects.select_for_update().get_or_create(broker_account=account)
    return policy


@transaction.atomic
def update_risk_limits(account, changes):
    """Apply only requested limits, preserving newer safety and equity state."""
    if set(changes) - RISK_LIMIT_FIELDS:
        raise ValidationError("Only risk limits may be changed through this operation.")
    policy = _locked_policy(account)
    if changes:
        for field, value in changes.items():
            setattr(policy, field, value)
        policy.full_clean()
        policy.save(update_fields=[*changes, "updated_at"])
        policy.refresh_from_db()
    return policy


@transaction.atomic
def apply_account_control(account, action):
    """Explicit operator intent; callers validate connection and bot permissions."""
    if action not in {"start", "stop", "emergency_stop"}:
        raise ValueError("Unknown account control")
    policy = _locked_policy(account)
    fields = ["entries_enabled", "updated_at"]
    policy.entries_enabled = action == "start"
    if action == "start":
        policy.emergency_stop = False
        policy.emergency_stop_triggered_at = None
        fields += ["emergency_stop", "emergency_stop_triggered_at"]
    elif action == "emergency_stop":
        policy.emergency_stop = True
        fields.append("emergency_stop")
    policy.save(update_fields=fields)
    return policy
