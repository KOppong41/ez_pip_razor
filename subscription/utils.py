from django.utils import timezone
from django.db import models

from .models import UserSubscription


def get_broker_account_limit(user) -> int:
    """
    Return the allowed number of broker accounts for a user based on the
    latest active subscription. Default is 1 if none.
    """
    if not user:
        return 1

    now = timezone.now()
    sub = (
        UserSubscription.objects.filter(
            user=user,
            is_active=True,
        )
        .filter(models.Q(expires_at__isnull=True) | models.Q(expires_at__gt=now))
        .order_by("-created_at")
        .first()
    )
    if not sub:
        return 1
    return max(1, sub.broker_account_limit())


def get_bot_limit(user) -> int:
    """
    Return the allowed number of bots for a user.
    """
    if not user:
        return 1

    now = timezone.now()
    sub = (
        UserSubscription.objects.filter(
            user=user,
            is_active=True,
        )
        .filter(models.Q(expires_at__isnull=True) | models.Q(expires_at__gt=now))
        .order_by("-created_at")
        .first()
    )
    if not sub:
        return 1
    return max(1, sub.bot_limit())
