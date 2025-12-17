
from decimal import Decimal
from django.conf import settings
from execution.connectors.mt5 import (
    MT5Connector,
    ConnectorError,
    is_mt5_available,
    mt5,
)
from execution.models import Execution
from execution.services.runtime_config import get_runtime_config

def _cached_balance_or_empty(broker_account, empty: dict) -> dict:
    latest = (
        Execution.objects.filter(order__broker_account=broker_account, account_balance__isnull=False)
        .order_by("-exec_time")
        .values_list("account_balance", flat=True)
        .first()
    )
    if latest is None:
        return empty
    return {"balance": latest, "equity": latest, "margin": Decimal("0")}


def get_account_balances(broker_account, *, force_live: bool = False) -> dict:
    """
    Safely fetch balances for a BrokerAccount.

    - Paper accounts: return mock balance from Execution history or configured start.
    - MT5 accounts: fetch live balance/equity if MT5 logins are allowed.
    """

    # Default "empty" response to avoid admin 500s
    empty = {"balance": None, "equity": None, "margin": None}

    # Paper accounts: return a fixed starting balance/equity (configurable) so admin/UI isn't blank.
    if getattr(broker_account, "broker", None) == "paper":
        start = get_runtime_config().paper_start_balance
        cached = _cached_balance_or_empty(broker_account, empty)
        cached_balance = cached.get("balance")
        balance = cached_balance if cached_balance is not None else start
        return {"balance": balance, "equity": balance, "margin": Decimal("0")}

    connector = getattr(broker_account, "connector", "mt5_local") or "mt5_local"
    mt5_codes = {"mt5", "exness_mt5", "icmarket_mt5"}
    if connector != "mt5_local" and getattr(broker_account, "broker", None) not in mt5_codes:
        return _cached_balance_or_empty(broker_account, empty)

    if not is_mt5_available():
        return _cached_balance_or_empty(broker_account, empty)

    # FIRST check if account is active - this prevents any login attempts for inactive accounts
    if not getattr(broker_account, "is_active", False):
        return _cached_balance_or_empty(broker_account, empty)

    # THEN respect global toggle unless force_live is set.
    if getattr(settings, "ADMIN_DISABLE_MT5_LOGIN", True) and not force_live:
        return _cached_balance_or_empty(broker_account, empty)

    # If we reach here, account is active and we're allowed to login
    try:
        MT5Connector().login_for_account(broker_account)
        info = mt5.account_info()
        if not info:
            return empty
        balance = Decimal(str(getattr(info, "balance", 0)))
        equity = Decimal(str(getattr(info, "equity", 0)))
        margin = Decimal(str(getattr(info, "margin", 0) or 0))
        return {"balance": balance, "equity": equity, "margin": margin}
    except Exception:
        return _cached_balance_or_empty(broker_account, empty)
