
from execution.connectors.paper import PaperConnector
from execution.models import Order
from execution.connectors.mt5 import MT5Connector
from decimal import Decimal
from datetime import datetime
import logging
from execution.services.journal import log_journal_event
from execution.services.market_hours import get_market_status
from dataclasses import dataclass
from functools import lru_cache
import time

from django.conf import settings

logger = logging.getLogger(__name__)

# Broker-code normalization (platform-based). Keep legacy codes as aliases.
BROKER_ALIASES = {
    "exness_mt5": "mt5",
    "icmarket_mt5": "mt5",
}

# Simple registry
# Reuse one MT5 connector instance for all MT5-based brokers to share the singleton session.
_mt5_connector = MT5Connector()
CONNECTORS = {
    "paper": PaperConnector(),
    "mt5_local": _mt5_connector,
}
# Legacy aliases still map to MT5 desktop unless an explicit connector is set on the account.
for _alias in BROKER_ALIASES:
    CONNECTORS[_alias] = _mt5_connector


def normalize_broker_code(code: str) -> str:
    """Map legacy broker codes to platform-based codes."""
    return BROKER_ALIASES.get(code, code)

# Spread thresholds (in pips) - adjust per symbol
SPREAD_LIMITS = {
    "EURUSDm": Decimal("2.5"),  # 2.5 pips max
    "XAUUSDm": Decimal("50"),   # 50 cents max (gold is wider)
    "BTCUSDm": Decimal("50"),   # crypto spreads are wider; allow up to $50
    "ETHUSDm": Decimal("30"),   # ETH spread guardrail; widen as needed per broker
}

# Trading session windows (UTC hours)
# TODO: For production, restrict to London + New York overlap (8-17 UTC)
# For now, enable 24/5 trading to allow testing outside market hours
LIQUID_SESSIONS = [
    (0, 24),   # 24 hours (TODO: restrict to 8-17 UTC for production)
]

def get_current_session_hour() -> int:
    """Get current hour in UTC."""
    return datetime.utcnow().hour

def is_liquid_session() -> bool:
    """Check if current time is in liquid trading session."""
    hour = get_current_session_hour()
    return any(start <= hour < end for start, end in LIQUID_SESSIONS)

def get_spread_limit(symbol: str) -> Decimal:
    """Get spread limit for symbol, default 3.0 pips."""
    return SPREAD_LIMITS.get(symbol, Decimal("3.0"))


@dataclass(frozen=True)
class BrokerSymbolConstraints:
    """Broker/account-level limits that must precede asset/profile settings."""
    min_lot: Decimal | None = None
    max_lot: Decimal | None = None
    lot_step: Decimal | None = None
    point: Decimal | None = None
    tick_size: Decimal | None = None
    stops_level_points: Decimal | None = None
    freeze_level_points: Decimal | None = None
    max_deviation: Decimal | None = None


ENTRY_CONSTRAINT_FIELDS = (
    "point",
    "min_lot",
    "max_lot",
    "lot_step",
    "stops_level_points",
)


def missing_entry_constraint_fields(constraints: BrokerSymbolConstraints) -> tuple[str, ...]:
    """Return entry-critical fields that the broker did not provide.

    A numeric zero is a valid broker stop level and must not be treated as
    missing. Point and lot values must be positive before an entry can be
    risk-sized safely.
    """
    missing = []
    for field in ENTRY_CONSTRAINT_FIELDS:
        value = getattr(constraints, field, None)
        if value is None:
            missing.append(field)
        elif field != "stops_level_points" and value <= 0:
            missing.append(field)
    return tuple(missing)


def _optional_decimal(value) -> Decimal | None:
    return None if value is None else Decimal(str(value))


def get_broker_symbol_constraints(broker_account, symbol: str) -> BrokerSymbolConstraints:
    """
    Fetch broker-level constraints (min lot, step, stops level, freeze level, deviation) for the given symbol.
    Falls back to None for unknown connectors.
    """
    if not broker_account or not symbol:
        return BrokerSymbolConstraints()

    explicit_connector = getattr(broker_account, "connector", "") or ""
    code = explicit_connector or normalize_broker_code(
        getattr(broker_account, "broker", "") or ""
    )
    account_id = getattr(broker_account, "id", None)
    # MT5 symbol names are case-sensitive. Broker suffixes such as the trailing
    # ``m`` in XAUUSDm must be preserved exactly.
    symbol = str(symbol).strip()
    cache_seconds = max(
        1,
        int(getattr(settings, "BROKER_CONSTRAINT_CACHE_SECONDS", 60)),
    )
    cache_bucket = int(time.monotonic() // cache_seconds)
    try:
        return _get_broker_constraints_cached(
            account_id,
            code,
            symbol,
            cache_bucket,
        )
    except Exception as exc:
        # Exceptions are raised inside the cached function, so failures are not
        # retained as successful cache entries.
        logger.warning(
            "Broker constraints unavailable account=%s connector=%s symbol=%s: %s",
            account_id,
            code,
            symbol,
            exc,
        )
        return BrokerSymbolConstraints()


@lru_cache(maxsize=256)
def _get_broker_constraints_cached(
    account_id,
    code: str,
    symbol: str,
    _cache_bucket: int,
) -> BrokerSymbolConstraints:
    """Cache successful MT5 lookups for one configured time bucket."""
    if code in {"mt5_local", "mt5", "exness_mt5", "icmarket_mt5"}:
        from brokers.models import BrokerAccount
        from execution.connectors.mt5 import is_mt5_available

        if not is_mt5_available():
            raise RuntimeError("MetaTrader5 Python package is unavailable")
        account = BrokerAccount.objects.get(pk=account_id)
        sinfo = MT5Connector().symbol_info_for_account(account, symbol)
        if not sinfo:
            raise RuntimeError("MT5 symbol_info returned no data")

        stops_level = getattr(sinfo, "trade_stops_level", None)
        if stops_level is None:
            stops_level = getattr(sinfo, "stops_level", None)
        freeze_level = getattr(sinfo, "trade_freeze_level", None)
        if freeze_level is None:
            freeze_level = getattr(sinfo, "freeze_level", None)

        return BrokerSymbolConstraints(
            min_lot=_optional_decimal(getattr(sinfo, "volume_min", None)),
            max_lot=_optional_decimal(getattr(sinfo, "volume_max", None)),
            lot_step=_optional_decimal(getattr(sinfo, "volume_step", None)),
            point=_optional_decimal(getattr(sinfo, "point", None)),
            tick_size=_optional_decimal(getattr(sinfo, "trade_tick_size", None)),
            stops_level_points=_optional_decimal(stops_level),
            freeze_level_points=_optional_decimal(freeze_level),
            max_deviation=Decimal("20"),  # keep aligned with mt5 connector default
        )

    # Placeholder for other connectors (ctrader/exness_web) when their constraint APIs are added.
    return BrokerSymbolConstraints()

def validate_order_conditions(order: Order) -> tuple:
    """
    Pre-trade validation:
    - Check if in liquid session
    - Verify spread is acceptable
    Returns (valid, reason)
    """
    # Detect close orders by client_order_id prefix (make_close_order_id uses 'close|...')
    is_close_order = str(getattr(order, "client_order_id", "")).startswith("close|")

    # Session check (skip for paper trading)
    if order.broker_account.broker != "paper":
        if not is_liquid_session():
            return False, "outside_liquid_session"

    # Market-hours guard (skip paper + close orders)
    if order.broker_account.broker != "paper" and not is_close_order:
        asset_category = None
        bot = getattr(order, "bot", None)
        try:
            asset = getattr(bot, "asset", None) if bot else None
            asset_category = getattr(asset, "category", None) if asset else None
        except Exception:
            asset_category = None

        market_status = get_market_status(
            symbol=getattr(order, "symbol", None),
            asset_category=asset_category,
            broker_account=getattr(order, "broker_account", None),
            use_mt5_probe=True,
            side=getattr(order, "side", None),
        )
        if market_status and not market_status.is_open:
            return False, f"market_closed:{market_status.reason}"

    # Enforce SL/TP presence before sending live (required for opens)
    if order.broker_account.broker != "paper" and not is_close_order:
        if order.sl is None or order.tp is None:
            return False, "missing_sl_tp"

    # Spread check would require market data; placeholder for future integration
    return True, "ok"

def _resolve_connector(order: Order):
    account = order.broker_account
    explicit = getattr(account, "connector", "") or ""
    if explicit:
        connector = CONNECTORS.get(explicit)
        if connector:
            return connector, explicit
        return None, explicit
    # Fallback to legacy broker codes so existing accounts keep working.
    raw_code = account.broker
    normalized = normalize_broker_code(raw_code)
    # Paper broker still routes to paper connector.
    if normalized == "paper":
        connector = CONNECTORS.get("paper")
        if connector:
            return connector, "paper"
    return None, normalized or raw_code or "unknown"


def dispatch_place_order(order: Order) -> None:
    """Place order with pre-flight validation."""
    from execution.services.orchestrator import validate_order_account_scope

    validate_order_account_scope(order)
    connector, connector_key = _resolve_connector(order)
    if not connector:
        raise ValueError(f"No connector for broker adapter '{connector_key}'")
    
    # Validate conditions before sending
    valid, reason = validate_order_conditions(order)
    if not valid:
        from execution.services.orchestrator import update_order_status
        msg = f"Order rejected: {reason}"
        update_order_status(order, "error", error_msg=msg)
        logger.warning(f"Order {order.id} rejected: {reason}")
        log_journal_event(
            "order.dispatch_error",
            severity="warning",
            order=order,
            bot=order.bot,
            broker_account=order.broker_account,
            symbol=order.symbol,
            message=f"{order.symbol} {order.side} rejected before dispatch",
            context={"reason": reason},
        )
        raise ValueError(msg)
    
    connector.place_order(order)

def dispatch_cancel_order(order: Order) -> None:
    from execution.services.orchestrator import validate_order_account_scope

    validate_order_account_scope(order)
    connector, connector_key = _resolve_connector(order)
    if not connector:
        raise ValueError(f"No connector for broker adapter '{connector_key}'")
    connector.cancel_order(order)
