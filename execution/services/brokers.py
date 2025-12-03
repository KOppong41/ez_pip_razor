
from execution.connectors.paper import PaperConnector
from execution.models import Order
from execution.connectors.mt5 import MT5Connector
from decimal import Decimal
from datetime import datetime
import logging
from core.utils import audit_log

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
    "mt5": _mt5_connector,
    # "binance": BinanceConnector(...),
}
# Register aliases pointing to the same connector to avoid KeyErrors in legacy configs.
for _alias, _target in BROKER_ALIASES.items():
    CONNECTORS[_alias] = CONNECTORS.get(_target, _mt5_connector)


def normalize_broker_code(code: str) -> str:
    """Map legacy broker codes to platform-based codes."""
    return BROKER_ALIASES.get(code, code)

# Spread thresholds (in pips) - adjust per symbol
SPREAD_LIMITS = {
    "EURUSDm": Decimal("2.5"),  # 2.5 pips max
    "XAUUSDm": Decimal("50"),   # 50 cents max (gold is wider)
    "BTCUSDm": Decimal("50"),   # crypto spreads are wider; allow up to $50
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

    # Enforce SL/TP presence before sending live (required for opens)
    if order.broker_account.broker != "paper" and not is_close_order:
        if order.sl is None or order.tp is None:
            return False, "missing_sl_tp"

    # Spread check would require market data; placeholder for future integration
    return True, "ok"

def dispatch_place_order(order: Order) -> None:
    """Place order with pre-flight validation."""
    raw_code = order.broker_account.broker
    code = normalize_broker_code(raw_code)
    connector = CONNECTORS.get(code)
    if not connector:
        raise ValueError(f"No connector for broker '{raw_code}'")
    
    # Validate conditions before sending
    valid, reason = validate_order_conditions(order)
    if not valid:
        from execution.services.orchestrator import update_order_status
        msg = f"Order rejected: {reason}"
        update_order_status(order, "error", error_msg=msg)
        logger.warning(f"Order {order.id} rejected: {reason}")
        audit_log(
            "order.dispatch_error",
            "Order",
            order.id,
            {"reason": reason, "symbol": order.symbol, "side": order.side},
        )
        raise ValueError(msg)
    
    connector.place_order(order)

def dispatch_cancel_order(order: Order) -> None:
    raw_code = order.broker_account.broker
    code = normalize_broker_code(raw_code)
    connector = CONNECTORS.get(code)
    if not connector:
        raise ValueError(f"No connector for broker '{raw_code}'")
    connector.cancel_order(order)
