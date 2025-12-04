from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from typing import List, Literal, TypedDict

import logging

from brokers.models import BrokerAccount
from execution.connectors.mt5 import MT5Connector, ConnectorError, is_mt5_available, mt5
from core.metrics import mt5_errors_total


TimeframeStr = Literal["1m", "5m", "15m", "30m", "1h", "4h", "1d"]


class Candle(TypedDict):
    time: datetime
    open: Decimal
    high: Decimal
    low: Decimal
    close: Decimal
    tick_volume: int


TIMEFRAME_ATTRS = {
    "1m": "TIMEFRAME_M1",
    "5m": "TIMEFRAME_M5",
    "15m": "TIMEFRAME_M15",
    "30m": "TIMEFRAME_M30",
    "1h": "TIMEFRAME_H1",
    "4h": "TIMEFRAME_H4",
    "1d": "TIMEFRAME_D1",
    "1w": "TIMEFRAME_W1",
    "1mo": "TIMEFRAME_MN1",
}


@dataclass
class MarketDataConfig:
    """
    Config for fetching candles from MT5.

    We keep it very simple for now. Later we can add:
    - lookback window per timeframe
    - different sources (MT5 vs HTTP API, etc.).
    """
    lookback: int = 200  # number of bars to fetch by default


logger = logging.getLogger(__name__)


def _login_mt5_for_account(account: BrokerAccount) -> None:
    """
    Minimal MT5 login using BrokerAccount.creds.

    NOTE: This is intentionally simple. We may later reuse the shared
    session logic from the MT5Connector to avoid multiple logins per process.
    """
    creds = account.get_creds() or {}
    login_raw = creds.get("login")
    password = creds.get("password")
    server = creds.get("server")
    path = creds.get("path")  # terminal64.exe path, if configured
    if not login_raw or not password or not server:
        raise RuntimeError("MT5 creds missing login/password/server")
    try:
        login = int(login_raw)
    except Exception:
        raise RuntimeError(f"MT5 creds invalid login: {login_raw!r}")

    # Reuse connector logic (locks, circuit breaker, allow switch=True for data fetch)
    conn = MT5Connector()
    try:
        conn._login_from_creds(
            {"login": login, "password": password, "server": server, "path": path},
            allow_switch=True,
        )
    except ConnectorError as e:
        raise RuntimeError(str(e))


def get_candles_for_account(
    broker_account: BrokerAccount,
    symbol: str,
    timeframe: TimeframeStr,
    n_bars: int = 200,
) -> List[Candle]:
    """
    Fetches the most recent `n_bars` candles for `symbol/timeframe` from MT5
    using the given BrokerAccount.

    Returns a list of dicts:
        {"time": datetime, "open": Decimal, "high": Decimal,
         "low": Decimal, "close": Decimal, "tick_volume": int}
    """
    attr = TIMEFRAME_ATTRS.get(timeframe)
    if attr is None:
        raise ValueError(f"Unsupported timeframe '{timeframe}'")

    if not is_mt5_available():
        return []

    tf_const = getattr(mt5, attr)

    try:
        _login_mt5_for_account(broker_account)
    except Exception as e:
        logger.exception("[MT5] login failed for account=%s: %s", getattr(broker_account, "id", "?"), e)
        return []

    try:
        # Ensure the symbol is selected in the terminal before requesting rates.
        if not mt5.symbol_select(symbol, True):
            err = mt5.last_error()
            mt5_errors_total.labels(action="symbol_select").inc()
            logger.error("[MT5] symbol_select failed for %s: %s", symbol, err)
            return []

        rates = mt5.copy_rates_from_pos(symbol, tf_const, 0, n_bars)
        if rates is None:
            err = mt5.last_error()
            mt5_errors_total.labels(action="copy_rates").inc()
            logger.error(
                "[MT5] copy_rates_from_pos failed symbol=%s tf=%s n_bars=%s err=%s",
                symbol,
                timeframe,
                n_bars,
                err,
            )
            return []

        candles: List[Candle] = []
        for r in rates:
            candles.append(
                {
                    "time": datetime.fromtimestamp(r["time"]),
                    "open": Decimal(str(r["open"])),
                    "high": Decimal(str(r["high"])),
                    "low": Decimal(str(r["low"])),
                    "close": Decimal(str(r["close"])),
                    "tick_volume": int(r["tick_volume"]),
                }
            )
        return candles
    finally:
        mt5.shutdown()
