
from decimal import Decimal
from django.conf import settings
from execution.connectors.mt5 import MT5Connector, ConnectorError
from execution.models import Execution
import MetaTrader5 as mt5
from execution.services.runtime_config import get_runtime_config

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
        latest = (
            Execution.objects.filter(order__broker_account=broker_account, account_balance__isnull=False)
            .order_by("-exec_time")
            .values_list("account_balance", flat=True)
            .first()
        )
        balance = latest or start
        return {"balance": balance, "equity": balance, "margin": Decimal("0")}

    mt5_codes = {"mt5", "exness_mt5", "icmarket_mt5"}
    if getattr(broker_account, "broker", None) not in mt5_codes:
        return empty

    # FIRST check if account is active - this prevents any login attempts for inactive accounts
    if not getattr(broker_account, "is_active", False):
        return empty

    # THEN respect global toggle unless force_live is set.
    if getattr(settings, "ADMIN_DISABLE_MT5_LOGIN", True) and not force_live:
        return empty

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
        return empty
