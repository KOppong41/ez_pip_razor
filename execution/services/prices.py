from datetime import datetime, timezone
from decimal import Decimal
from django.conf import settings

from execution.connectors.base import ConnectorError
from execution.connectors.mt5 import MT5Connector, is_mt5_available


class MarketDataUnavailable(ConnectorError):
    pass


def get_price(broker_account, symbol: str) -> Decimal:
    """
    Return an account-scoped, fresh MT5 mid-price.

    There is deliberately no synthetic fallback. New entries must fail closed
    when the connected broker cannot provide a valid current quote.
    """
    if broker_account is None:
        raise MarketDataUnavailable("A BrokerAccount is required for live pricing")
    if not is_mt5_available():
        raise MarketDataUnavailable("MetaTrader5 package is unavailable")

    try:
        tick = MT5Connector().tick_for_account(broker_account, symbol)
    except Exception as exc:
        raise MarketDataUnavailable(f"MT5 price unavailable for {symbol}") from exc

    bid = Decimal(str(getattr(tick, "bid", 0) or 0))
    ask = Decimal(str(getattr(tick, "ask", 0) or 0))
    if bid <= 0 or ask <= 0 or ask < bid:
        raise MarketDataUnavailable(f"Invalid MT5 bid/ask for {symbol}")

    tick_time = getattr(tick, "time", None)
    if tick_time:
        observed = datetime.fromtimestamp(float(tick_time), tz=timezone.utc)
        age = (datetime.now(timezone.utc) - observed).total_seconds()
        max_age = int(getattr(settings, "MT5_MAX_TICK_AGE_SECONDS", 120))
        if age < 0 or age > max_age:
            raise MarketDataUnavailable(f"Stale MT5 price for {symbol} (age={int(age)}s)")

    return (bid + ask) / Decimal("2")
