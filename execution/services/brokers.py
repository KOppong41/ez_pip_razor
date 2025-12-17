
from execution.connectors.paper import PaperConnector
from execution.models import Order
from execution.connectors.mt5 import MT5Connector
from execution.connectors.exness_web import ExnessWebConnector
from execution.connectors.ctrader import CTraderConnector
from decimal import Decimal
from datetime import datetime
import logging
from execution.services.journal import log_journal_event
from execution.services.market_hours import get_market_status
from dataclasses import dataclass
from functools import lru_cache

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
    "exness_web": ExnessWebConnector(),
    "ctrader_api": CTraderConnector(),
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


def get_broker_symbol_constraints(broker_account, symbol: str) -> BrokerSymbolConstraints:
    """
    Fetch broker-level constraints (min lot, step, stops level, freeze level, deviation) for the given symbol.
    Falls back to None for unknown connectors.
    """
    if not broker_account or not symbol:
        return BrokerSymbolConstraints()

    code = normalize_broker_code(getattr(broker_account, "broker", "") or "")
    account_id = getattr(broker_account, "id", None)
    symbol = symbol.upper()
    return _get_broker_constraints_cached(account_id, code, symbol)


@lru_cache(maxsize=256)
def _get_broker_constraints_cached(account_id, code: str, symbol: str) -> BrokerSymbolConstraints:
    """Cached lookup to reduce repeated symbol_info calls."""
    if code in {"mt5_local", "mt5", "exness_mt5", "icmarket_mt5"}:
        try:
            from execution.connectors.mt5 import is_mt5_available, mt5
            if not is_mt5_available():
                return BrokerSymbolConstraints()
            sinfo = mt5.symbol_info(symbol)
            if not sinfo:
                return BrokerSymbolConstraints()
            return BrokerSymbolConstraints(
                min_lot=Decimal(str(getattr(sinfo, "volume_min", None))) if getattr(sinfo, "volume_min", None) else None,
                max_lot=Decimal(str(getattr(sinfo, "volume_max", None))) if getattr(sinfo, "volume_max", None) else None,
                lot_step=Decimal(str(getattr(sinfo, "volume_step", None))) if getattr(sinfo, "volume_step", None) else None,
                point=Decimal(str(getattr(sinfo, "point", None))) if getattr(sinfo, "point", None) else None,
                tick_size=Decimal(str(getattr(sinfo, "trade_tick_size", None))) if getattr(sinfo, "trade_tick_size", None) else None,
                stops_level_points=Decimal(str(getattr(sinfo, "stops_level", None))) if getattr(sinfo, "stops_level", None) else None,
                freeze_level_points=Decimal(str(getattr(sinfo, "freeze_level", None))) if getattr(sinfo, "freeze_level", None) else None,
                max_deviation=Decimal("20"),  # keep aligned with mt5 connector default
            )
        except Exception:
            return BrokerSymbolConstraints()

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
    # Fallback to legacy broker codes so existing accounts keep working.
    raw_code = account.broker
    normalized = normalize_broker_code(raw_code)
    # Paper broker still routes to paper connector.
    if normalized == "paper":
        connector = CONNECTORS.get("paper")
        if connector:
            return connector, "paper"
    # Default MT5 desktop
    connector = CONNECTORS.get("mt5_local")
    return connector, "mt5_local"


def dispatch_place_order(order: Order) -> None:
    """Place order with pre-flight validation."""
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
    connector, connector_key = _resolve_connector(order)
    if not connector:
        raise ValueError(f"No connector for broker adapter '{connector_key}'")
    connector.cancel_order(order)
