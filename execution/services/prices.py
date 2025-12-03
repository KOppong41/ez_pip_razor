from decimal import Decimal

# Super‑simple stub; replace with connector feeds later.
FIXED = {
    "EURUSD": Decimal("1.1010"),
    "XAUUSD": Decimal("2400.00"),
}

def get_price(symbol: str) -> Decimal:
    """
    Try live MT5 mid-price (bid+ask)/2; fallback to stub if unavailable.
    """
    try:
        from brokers.models import BrokerAccount
        from execution.connectors.mt5 import MT5Connector
        import MetaTrader5 as mt5

        acct = BrokerAccount.objects.filter(is_active=True, broker__in=["mt5", "exness_mt5", "icmarket_mt5"]).first()
        if acct:
            conn = MT5Connector()
            conn.login_for_account(acct)
            mt5.symbol_select(symbol, True)
            tick = mt5.symbol_info_tick(symbol)
            if tick:
                bid = Decimal(str(getattr(tick, "bid", 0) or 0))
                ask = Decimal(str(getattr(tick, "ask", 0) or 0))
                if bid > 0 and ask > 0:
                    return (bid + ask) / Decimal("2")
    except Exception:
        # best-effort only
        pass

    sym_no_suffix = symbol.rstrip("m")
    return FIXED.get(sym_no_suffix, FIXED.get(symbol, Decimal("1.0000")))
