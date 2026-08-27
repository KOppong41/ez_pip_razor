from __future__ import annotations

from decimal import Decimal

from django.utils import timezone

from execution.models import RiskPolicy


def _decimal(value) -> Decimal:
    try:
        return Decimal(str(value))
    except Exception:
        return Decimal("0")


def equity_drawdown_pct(policy: RiskPolicy, equity) -> Decimal:
    """Return drawdown from the persistent account equity high-water mark."""
    current = _decimal(equity)
    peak = _decimal(policy.equity_high_water)
    if current <= 0 or peak <= 0 or current >= peak:
        return Decimal("0")
    return (peak - current) / peak * Decimal("100")


def update_equity_high_water(
    policy: RiskPolicy,
    equity,
    *,
    observed_at=None,
) -> Decimal:
    """Atomically persist a new all-time peak and return account drawdown."""
    current = _decimal(equity)
    if current > 0:
        RiskPolicy.objects.filter(
            pk=policy.pk,
            equity_high_water__lt=current,
        ).update(
            equity_high_water=current,
            equity_high_water_at=observed_at or timezone.now(),
        )
        policy.refresh_from_db(fields=["equity_high_water", "equity_high_water_at"])
    return equity_drawdown_pct(policy, current)
