"""Limited runtime sessions used only to stop a user's automation on app exit."""

from __future__ import annotations

from django.conf import settings
from django.core import signing
from django.db import transaction

from bots.models import Bot
from brokers.models import BrokerAccount
from execution.models import RiskPolicy


RUNTIME_STOP_SALT = "ez-trade.runtime-stop.v1"


def create_runtime_stop_token(user) -> str:
    return signing.dumps(
        {"user_id": user.pk},
        key=settings.SECRET_KEY,
        salt=RUNTIME_STOP_SALT,
        compress=True,
    )


def runtime_stop_user_id(token: str) -> int:
    payload = signing.loads(
        token,
        key=settings.SECRET_KEY,
        salt=RUNTIME_STOP_SALT,
        max_age=settings.RUNTIME_STOP_TOKEN_MAX_AGE_SECONDS,
    )
    return int(payload["user_id"])


@transaction.atomic
def stop_user_automation(user) -> dict[str, int]:
    account_ids = list(
        BrokerAccount.objects.filter(
            owner=user, connector="mt5_local"
        ).values_list("id", flat=True)
    )
    policies = RiskPolicy.objects.filter(broker_account_id__in=account_ids).update(
        entries_enabled=False
    )
    bots = Bot.objects.filter(owner=user).exclude(status="stopped").update(
        status="stopped"
    )
    return {"accounts_stopped": policies, "bots_stopped": bots}
