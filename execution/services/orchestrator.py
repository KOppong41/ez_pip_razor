
from dataclasses import dataclass
from typing import Literal, Tuple
from django.db import transaction
from django.utils import timezone
from execution.models import Decision, Order, BrokerAccount, Bot
from execution.services.journal import log_journal_event
import hashlib
from core.metrics import orders_created_total, order_status_total
from decimal import Decimal
import logging

logger = logging.getLogger(__name__)

VALID_STATUSES = {s for (s, _) in Order.STATUS}

# Canonical statuses used everywhere
OrderStatus = Literal["new", "ack", "filled", "part_filled", "canceled", "error"]

def make_client_order_id(decision: Decision, broker_account: BrokerAccount) -> str:
    base = f"{decision.id}|{broker_account.id}|{decision.signal.symbol}|{decision.action}"
    return hashlib.sha1(base.encode()).hexdigest()[:20]  # deterministic idempotency


def make_close_order_id(position, broker_account: BrokerAccount) -> str:
    base = f"close|{position.id}|{broker_account.id}|{position.symbol}"
    # Prefix with "close|" so downstream validation can recognize close orders
    return "close|" + hashlib.sha1(base.encode()).hexdigest()[:20]

@dataclass
class OrderSpec:
    bot: Bot
    broker_account: BrokerAccount
    symbol: str
    side: Literal["buy", "sell"]
    qty: str  # Decimal as string is fine for serializer -> model

# Allowed transitions (kept permissive for immediate fills from 'new')
ALLOWED_TRANSITIONS = {
    "new": {"ack", "filled", "error", "canceled"},
    "ack": {"filled", "part_filled", "error", "canceled"},
    "part_filled": {"filled", "error", "canceled"},
    "filled": set(),        # position mgmt/state is separate from order state
    "error": set(),
    "canceled": set(),
}

def can_transition(current: str, target: str) -> bool:
    return target in ALLOWED_TRANSITIONS.get(current, set())

def calculate_position_size(qty_base: Decimal, atr: Decimal, symbol: str) -> Decimal:
    """
    Risk-based position sizing: Scale qty inversely to volatility.
    Formula: adj_qty = base_qty * (baseline_atr / current_atr)
    """
    baseline_atr = Decimal("0.001")  # Baseline for EURUSD ~10 pips
    if atr <= 0:
        return qty_base
    
    scaling = baseline_atr / atr
    # Cap scaling to 0.5x - 2.0x to prevent extreme position sizes
    scaling = max(Decimal("0.5"), min(scaling, Decimal("2.0")))
    adjusted = qty_base * scaling
    
    logger.info(f"Position sizing: base={qty_base}, atr={atr}, scaling={scaling}, adjusted={adjusted}")
    return adjusted


def _get_minimum_stop_distance(symbol: str) -> Decimal:
    """
    Get the minimum distance between SL and TP for this symbol.
    Prevents 'Invalid stops' errors from MT5 broker.
    For high-precision instruments like GOLD (XAUUSDm), minimum is higher.
    """
    symbol_upper = symbol.upper()
    
    # Gold is high precision and needs larger minimum
    if "XAU" in symbol_upper or "GOLD" in symbol_upper:
        return Decimal("0.01")  # 10 pips minimum for GOLD
    
    # Forex pairs: standard 5 pip minimum
    if any(pair in symbol_upper for pair in ["EUR", "GBP", "USD", "JPY", "CHF"]):
        return Decimal("0.0005")  # 5 pips for most forex
    
    # Crypto and others: 0.0001 (1 pip)
    return Decimal("0.0001")


def _enforce_minimum_stop_distance(symbol: str, side: str, entry_px: Decimal | None, 
                                    sl: Decimal | None, tp: Decimal | None) -> Tuple[Decimal | None, Decimal | None]:
    """
    Validate and adjust SL/TP to ensure they meet minimum distance requirements.
    Returns (adjusted_sl, adjusted_tp).
    
    If SL and TP are too close, widens them by applying minimum distance rules.
    """
    if not (sl and tp) or not entry_px:
        return sl, tp
    
    min_distance = _get_minimum_stop_distance(symbol)
    actual_distance = abs(sl - tp)
    
    if actual_distance < min_distance:
        logger.warning(
            f"SL/TP too close for {symbol}: distance={actual_distance} < min={min_distance}. "
            f"Adjusting: entry={entry_px}, sl={sl}, tp={tp}"
        )
        
        # Rebuild SL/TP to enforce minimum distance
        if side == "buy":
            # For buy: SL should be below entry, TP should be above
            adjusted_sl = entry_px - min_distance * Decimal("1.5")  # 1.5x minimum for buffer
            adjusted_tp = entry_px + min_distance * Decimal("1.5")
        else:
            # For sell: SL should be above entry, TP should be below
            adjusted_sl = entry_px + min_distance * Decimal("1.5")
            adjusted_tp = entry_px - min_distance * Decimal("1.5")
        
        logger.info(
            f"Enforced minimum distance for {symbol} {side}: "
            f"adjusted_sl={adjusted_sl}, adjusted_tp={adjusted_tp}"
        )
        return adjusted_sl, adjusted_tp
    
    return sl, tp


def create_close_order_for_position(position, broker_account: BrokerAccount) -> Tuple[Order, bool]:
    """
    Idempotently create a close order sized to flatten the given position.
    """
    side = "buy" if position.qty < 0 else "sell"  # opposite side to close
    client_id = make_close_order_id(position, broker_account)

    # Prefer a bot on this broker account that actually trades the position's symbol
    bot = None
    try:
        bot = Bot.objects.filter(broker_account=broker_account, asset__symbol=position.symbol).first()
        if not bot:
            bot = broker_account.bots.first() if hasattr(broker_account, "bots") else None
    except Exception:
        bot = None

    defaults = {
        "bot": bot,
        "broker_account": broker_account,
        "symbol": position.symbol,
        "side": side,
        "qty": str(abs(position.qty)),
        "status": "new",
    }

    order, created = Order.objects.get_or_create(
        client_order_id=client_id,
        defaults=defaults,
    )

    # If the position size changed or the prior close attempt already filled/errored,
    # refresh the order so it can be dispatched again with the correct quantity.
    if not created:
        desired_qty = abs(position.qty)
        updates = []
        if order.qty != desired_qty:
            order.qty = desired_qty
            updates.append("qty")
        if order.side != side:
            order.side = side
            updates.append("side")
        if order.status in ("filled", "error", "canceled"):
            order.status = "new"
            order.last_error = ""
            updates.extend(["status", "last_error"])
        if order.bot_id != (bot.id if bot else None):
            order.bot = bot
            updates.append("bot")
        if updates:
            order.save(update_fields=updates)

    if created:
        orders_created_total.labels(
            broker=broker_account.broker, symbol=position.symbol, side=side
        ).inc()
        log_journal_event(
            "order.close_created",
            bot=bot,
            broker_account=broker_account,
            order=order,
            position=position,
            symbol=position.symbol,
            message=f"Close {position.symbol} {side} qty {order.qty}",
            context={"qty": str(order.qty), "position_qty": str(position.qty)},
        )

    return order, created

@transaction.atomic
def create_order_from_decision(
    decision: Decision, broker_account: BrokerAccount, qty: str, atr: Decimal = None
) -> Tuple[Order, bool]:
    """
    Idempotent creation keyed by (decision, broker_account, symbol, action).
    Qty is now taken directly from the bot/user preference; no auto-resizing.
    Returns (order, created_bool).
    """
    if decision.action not in ("open", "close"):
        raise ValueError("Decision action must be 'open' or 'close' to create an order")

    symbol = decision.signal.symbol
    side = "buy" if decision.signal.direction == "buy" else "sell"
    client_id = make_client_order_id(decision, broker_account)
    
    qty_decimal = Decimal(str(qty))
    qty = str(qty_decimal)

    # Base defaults for a new order
    defaults = {
        "bot": decision.bot,
        "broker_account": broker_account,
        "symbol": symbol,
        "side": side,
        "qty": qty,
        "status": "new",
        "owner": getattr(decision, "owner", None) or getattr(decision.bot, "owner", None),
    }

    # Create or reuse an existing order (idempotent)
    order, created = Order.objects.get_or_create(
        client_order_id=client_id,
        defaults=defaults,
    )

    # Apply SL/TP from decision params; if missing, add fallbacks so orders don't get rejected.
    params = decision.params or {}
    sl = params.get("sl")
    tp = params.get("tp")

    dirty_fields: list[str] = []

    try:
        from execution.services.prices import get_price

        px = Decimal(str(get_price(symbol)))
    except Exception:
        px = None

    # Optional: derive ATR from recent candles for smarter SL/TP fallbacks.
    atr_val = None
    try:
        if atr is not None:
            atr_val = Decimal(str(atr))
        else:
            from execution.services.marketdata import get_candles_for_account
            from execution.services.indicators import atr as calc_atr

            tf = getattr(decision.signal, "timeframe", "5m")
            candles = get_candles_for_account(
                broker_account=broker_account,
                symbol=symbol,
                timeframe=tf,
                n_bars=50,
            )
            atr_val = calc_atr(candles, period=14)
    except Exception:
        atr_val = None

    if sl is not None:
        order.sl = Decimal(str(sl))
        dirty_fields.append("sl")
    elif order.sl is None and px is not None:
        # Use ATR-based distance when available; otherwise percent offset.
        if atr_val and atr_val > 0:
            offset = atr_val * Decimal("1.2")
        else:
            offset = px * Decimal("0.0025")
        order.sl = px - offset if side == "buy" else px + offset
        dirty_fields.append("sl")
        logger.warning(f"Order {order.id} missing SL; applied fallback at {order.sl}")

    if tp is not None:
        order.tp = Decimal(str(tp))
        dirty_fields.append("tp")
    elif order.tp is None and px is not None:
        # TP slightly further than SL
        if atr_val and atr_val > 0:
            offset = atr_val * Decimal("1.8")
        else:
            offset = px * Decimal("0.0035")
        order.tp = px + offset if side == "buy" else px - offset
        dirty_fields.append("tp")
        logger.warning(f"Order {order.id} missing TP; applied fallback at {order.tp}")

    # ⚠️ CRITICAL: Validate SL/TP distance to prevent "Invalid stops" broker rejections
    if order.sl and order.tp and px:
        adjusted_sl, adjusted_tp = _enforce_minimum_stop_distance(
            symbol=symbol,
            side=side,
            entry_px=px,
            sl=order.sl,
            tp=order.tp
        )
        if adjusted_sl != order.sl or adjusted_tp != order.tp:
            order.sl = adjusted_sl
            order.tp = adjusted_tp
            dirty_fields = list(set(dirty_fields + ["sl", "tp"]))  # add if not already present

    if dirty_fields:
        order.save(update_fields=dirty_fields)
    
    # Enforce: both SL and TP should be set for risk management
    if not (order.sl and order.tp):
        logger.error(f"Order {order.id} missing SL or TP - risk management disabled!")

    if created:
        orders_created_total.labels(
            broker=broker_account.broker, symbol=symbol, side=side
        ).inc()
        log_journal_event(
            "order.created",
            bot=order.bot,
            broker_account=broker_account,
            order=order,
            decision=decision,
            signal=decision.signal,
            symbol=symbol,
            message=f"{symbol} {side} qty {order.qty}",
            context={
                "qty": str(order.qty),
                "sl": str(order.sl) if order.sl else None,
                "tp": str(order.tp) if order.tp else None,
            },
        )

    return order, created

def update_order_status(
    order: Order,
    new_status: str,
    price: Decimal | None = None,
    error_msg: str | None = None,
) -> None:
    """
    Central place to update order status.

    - Ensures we only ever store valid statuses.
    - Optionally stores fill price and last_error.
    """
    if new_status not in VALID_STATUSES:
        raise ValueError(f"Unsupported order status: {new_status}")

    previous_status = order.status
    was_filled = previous_status == "filled"
    order.status = new_status

    if price is not None:
        order.price = price

    if error_msg:
        # append to any existing error info
        if order.last_error:
            order.last_error = f"{order.last_error}\n{error_msg}"
        else:
            order.last_error = error_msg

    order.updated_at = timezone.now()
    order.save(update_fields=["status", "price", "last_error", "updated_at"])

    log_journal_event(
        "order.status_changed",
        severity="error" if new_status in {"error", "canceled"} else "info",
        order=order,
        bot=order.bot,
        broker_account=order.broker_account,
        symbol=order.symbol,
        message=f"{order.symbol} {order.side} {previous_status} -> {new_status}",
        context={
            "from": previous_status,
            "to": new_status,
            "price": str(price) if price is not None else None,
            "error": error_msg,
        },
    )

    # NOTE: We no longer auto-create executions here. Connectors/tasks that
    # mark an order filled must explicitly call record_fill once they have a
    # confirmed broker success to avoid phantom fills or double-counting.
    if new_status == "filled" and not was_filled:
        # Log trade history entry
        try:
            from execution.models import TradeLog

            already_logged = TradeLog.objects.filter(order=order, status=new_status).exists()
            if not already_logged:
                TradeLog.objects.create(
                    order=order,
                    bot=order.bot,
                    owner=order.owner,
                    broker_account=order.broker_account,
                    symbol=order.symbol,
                    side=order.side,
                    qty=order.qty,
                    price=order.price,
                    status=new_status,
                    pnl=None,
                )
        except Exception:
            pass
