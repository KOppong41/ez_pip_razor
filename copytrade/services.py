from decimal import Decimal
from typing import Iterable, List, Tuple

from execution.services.brokers import CONNECTORS, normalize_broker_code
from .models import Follower

def alloc_proportional(master_qty: Decimal, params: dict) -> Decimal:
    mult = Decimal(str(params.get("multiplier", "1")))
    return (master_qty * mult).quantize(Decimal("0.00000001"))

def alloc_fixed(_: Decimal, params: dict) -> Decimal:
    return Decimal(str(params.get("fixed_qty", "0.00"))).quantize(Decimal("0.00000001"))

def alloc_equity_pct(master_qty: Decimal, params: dict, equity: Decimal) -> Decimal:
    # very naive: qty = equity_pct% of equity, scaled by master_qty’s lot scale (placeholder)
    pct = Decimal(str(params.get("equity_pct", "1"))) / Decimal("100")
    target_notional = (equity * pct)  # placeholder (no price conversion in MVP)
    # approximate: map notional to qty magnitude of master
    scale = (master_qty.copy_abs() if master_qty != 0 else Decimal("1"))
    return (target_notional / Decimal("1000") + scale*Decimal("0.0")).quantize(Decimal("0.00000001"))  # dummy

def compute_allocation(f: Follower, master_qty: Decimal, equity: Decimal) -> Decimal:
    if f.model == "proportional":
        return alloc_proportional(master_qty, f.params)
    if f.model == "fixed":
        return alloc_fixed(master_qty, f.params)
    if f.model == "equity_pct":
        return alloc_equity_pct(master_qty, f.params, equity)
    return Decimal("0")

def get_equity_for_account(ba) -> Decimal:
    if ba.broker in ("mt5", "exness_mt5", "icmarket_mt5"):
        # ensure we're logged in to that account before reading equity
        conn = CONNECTORS[normalize_broker_code(ba.broker)]
        # quick, harmless session ensure (reuse login code):
        # create a tiny fake order-like object, or refactor session login out:
        creds = ba.get_creds()
        from execution.connectors.mt5 import _MT5Session
        _MT5Session.ensure_login(
            path=creds.get("path"),
            login=int(creds.get("login")),
            password=creds.get("password"),
            server=creds.get("server"),
        )
        return _MT5Session.account_equity()
    # default for others
    return Decimal("10000")

def eligible_followers(master_bot) -> Iterable[Follower]:
    for f in Follower.objects.filter(master_bot=master_bot, is_enabled=True).select_related("broker_account"):
        equity = get_equity_for_account(f.broker_account)
        if equity >= f.min_balance:
            yield f, equity
