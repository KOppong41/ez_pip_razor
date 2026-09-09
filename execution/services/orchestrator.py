
from dataclasses import dataclass
from typing import Literal, Tuple
from django.db import transaction
from django.db.models import Q
from django.utils import timezone
from execution.models import BrokerPosition, Decision, Order, BrokerAccount, Bot
from execution.services.journal import log_journal_event
from execution.services.protection_policy import (
    decision_exit_mode,
    validate_order_protection,
    validate_protection,
)
import hashlib
from core.metrics import orders_created_total, order_status_total
from decimal import Decimal
import logging

from brokers.models import SUPPORTED_BROKER_CONNECTORS

logger = logging.getLogger(__name__)

VALID_STATUSES = {s for (s, _) in Order.STATUS}

# Canonical statuses used everywhere
OrderStatus = Literal["new", "ack", "filled", "part_filled", "canceled", "rejected", "error"]


def validate_decision_account_scope(decision: Decision, broker_account: BrokerAccount) -> int:
    """Validate that a decision, bot, signal, and account form one ownership boundary."""
    bot = decision.bot
    signal = decision.signal
    if bot is None:
        raise ValueError("Decision must be attached to a bot")
    if bot.broker_account_id != broker_account.id:
        raise ValueError("Decision bot is not configured for this broker account")
    if signal.bot_id and signal.bot_id != bot.id:
        raise ValueError("Decision signal belongs to a different bot")

    owner_ids = {
        owner_id
        for owner_id in (
            broker_account.owner_id,
            bot.owner_id,
            decision.owner_id,
            signal.owner_id,
        )
        if owner_id is not None
    }
    if len(owner_ids) != 1:
        if not owner_ids:
            raise ValueError("Trading objects must have an owner")
        raise ValueError("Decision, bot, signal, and broker account owners do not match")

    connector = getattr(broker_account, "connector", "") or ""
    if connector not in SUPPORTED_BROKER_CONNECTORS:
        raise ValueError(f"Connector '{connector or 'unknown'}' is not available")
    return next(iter(owner_ids))


def validate_order_account_scope(order: Order) -> int:
    """Apply the ownership boundary again immediately before broker dispatch."""
    account = order.broker_account
    bot = order.bot
    if bot.broker_account_id != account.id:
        raise ValueError("Order bot is not configured for this broker account")
    owner_ids = {
        owner_id
        for owner_id in (order.owner_id, bot.owner_id, account.owner_id)
        if owner_id is not None
    }
    if order.decision_id:
        validate_decision_account_scope(order.decision, account)
        if order.decision.owner_id:
            owner_ids.add(order.decision.owner_id)
    if len(owner_ids) != 1:
        if not owner_ids:
            raise ValueError("Order, bot, and broker account must have an owner")
        raise ValueError("Order, bot, and broker account owners do not match")
    connector = getattr(account, "connector", "") or ""
    if connector not in SUPPORTED_BROKER_CONNECTORS:
        raise ValueError(f"Connector '{connector or 'unknown'}' is not available")
    return next(iter(owner_ids))

def make_client_order_id(decision: Decision, broker_account: BrokerAccount) -> str:
    base = f"{decision.id}|{broker_account.id}|{decision.signal.symbol}|{decision.action}"
    return hashlib.sha1(base.encode()).hexdigest()[:20]  # deterministic idempotency


def make_close_order_id(position, broker_account: BrokerAccount, *, stage: str = "full") -> str:
    ticket = getattr(position, "broker_position_ticket", None)
    base = (
        f"close|{ticket}|{broker_account.id}|{position.symbol}"
        if stage == "full"
        else f"close|{stage}|{ticket}|{broker_account.id}|{position.symbol}"
    )
    # Prefix with "close|" so downstream validation can recognize close orders
    prefix = "close|" if stage == "full" else f"close:{stage}|"
    return prefix + hashlib.sha1(base.encode()).hexdigest()[:20]


@dataclass
class OrderSpec:
    bot: Bot
    broker_account: BrokerAccount
    symbol: str
    side: Literal["buy", "sell"]
    qty: str  # Decimal as string is fine for serializer -> model

# Allowed transitions (kept permissive for immediate fills from 'new')
ALLOWED_TRANSITIONS = {
    "new": {"ack", "filled", "part_filled", "rejected", "error", "canceled"},
    "ack": {"filled", "part_filled", "rejected", "error", "canceled"},
    "part_filled": {"filled", "part_filled", "rejected", "error", "canceled"},
    "filled": set(),        # position mgmt/state is separate from order state
    "error": set(),
    "rejected": set(),
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


def _close_order_can_retry(order: Order) -> bool:
    """Return True only when the previous close is terminal with no possible fill."""
    if order.status not in {"canceled", "rejected", "error"}:
        return False
    if Decimal(str(order.filled_qty or 0)) > 0 or order.executions.exists():
        return False
    # A broker-confirmed cancellation is safe to retry even when the original
    # submission was accepted. Other accepted or ambiguous sends retain their
    # deterministic identity until reconciliation proves the outcome.
    unsafe_attempts = {"submitting", "ambiguous", "partial", "reconciled"}
    if order.status != "canceled":
        unsafe_attempts.add("accepted")
    return not order.attempts.filter(status__in=unsafe_attempts).exists()


def _retry_close_order_id(base_client_id: str, existing_ids: set[str]) -> str:
    attempt = 1
    while f"{base_client_id}|retry:{attempt}" in existing_ids:
        attempt += 1
    return f"{base_client_id}|retry:{attempt}"


def create_close_order_for_position(
    position,
    broker_account: BrokerAccount,
    *,
    close_qty: Decimal | None = None,
    stage: str = "full",
) -> Tuple[Order, bool]:
    """
    Idempotently create a close order sized to flatten the given position.
    """
    if getattr(broker_account, "connector", "") == "paper":
        if getattr(position, "broker_account_id", None) != broker_account.id:
            raise ValueError("Position does not belong to this broker account")
        if getattr(position, "status", None) != "open" or not hasattr(position, "qty"):
            raise ValueError("Paper close requires an open paper position")
        bot = Bot.objects.filter(
            broker_account=broker_account,
            asset__symbol=position.symbol,
        ).first() or Bot.objects.filter(broker_account=broker_account).first()
        if bot is None:
            raise ValueError("Cannot close a paper position without its bot")
        side = "sell" if position.qty > 0 else "buy"
        available_qty = abs(position.qty)
        qty = Decimal(str(close_qty)) if close_qty is not None else available_qty
        if qty <= 0 or qty > available_qty:
            raise ValueError("Close quantity must be positive and no greater than the open position")
        client_id = make_close_order_id(position, broker_account, stage=stage)
        order, created = Order.objects.get_or_create(
            client_order_id=client_id,
            defaults={
                "bot": bot,
                "owner_id": bot.owner_id,
                "broker_account": broker_account,
                "symbol": position.symbol,
                "side": side,
                "qty": qty,
                "remaining_qty": qty,
                "intent": "exit",
                "status": "new",
            },
        )
        validate_order_account_scope(order)
        return order, created

    broker_position = position if isinstance(position, BrokerPosition) else None
    if broker_position is None:
        candidates = BrokerPosition.objects.filter(
            broker_account=broker_account,
            symbol=position.symbol,
            status="open",
            ownership="ez_trade",
        )
        if candidates.count() != 1:
            raise ValueError(
                "A close requires exactly one EZ Trade-owned broker position ticket"
            )
        broker_position = candidates.get()

    if broker_position.broker_account_id != broker_account.id:
        raise ValueError("Broker position does not belong to this broker account")
    if not broker_position.is_manageable:
        raise ValueError("Manual or unknown MT5 positions cannot be managed automatically")

    side = "sell" if broker_position.side == "buy" else "buy"
    available_qty = broker_position.volume
    qty = Decimal(str(close_qty)) if close_qty is not None else available_qty
    if qty <= 0 or qty > available_qty:
        raise ValueError("Close quantity must be positive and no greater than the open position")
    client_id = make_close_order_id(broker_position, broker_account, stage=stage)

    # Prefer a bot on this broker account that actually trades the position's symbol
    bot = None
    try:
        bot = broker_position.bot or Bot.objects.filter(
            broker_account=broker_account,
            asset__symbol=broker_position.symbol,
        ).first()
        if not bot:
            bot = broker_account.bots.first() if hasattr(broker_account, "bots") else None
    except Exception:
        bot = None

    if bot is None:
        raise ValueError("Cannot close a broker position without its originating bot")

    defaults = {
        "bot": bot,
        "owner_id": bot.owner_id,
        "broker_account": broker_account,
        "symbol": broker_position.symbol,
        "side": side,
        "qty": str(qty),
        "remaining_qty": qty,
        "intent": "exit",
        "broker_position_ticket": broker_position.broker_position_ticket,
        "status": "new",
    }

    with transaction.atomic():
        close_orders = list(
            Order.objects.select_for_update()
            .filter(
                Q(client_order_id=client_id)
                | Q(client_order_id__startswith=f"{client_id}|retry:")
            )
            .order_by("id")
        )
        if not close_orders:
            order, created = Order.objects.get_or_create(
                client_order_id=client_id,
                defaults=defaults,
            )
        else:
            order = close_orders[-1]
            created = False
            if _close_order_can_retry(order):
                retry_id = _retry_close_order_id(
                    client_id,
                    {existing.client_order_id for existing in close_orders},
                )
                order = Order.objects.create(client_order_id=retry_id, **defaults)
                created = True
    validate_order_account_scope(order)

    # Never recycle an active or ambiguous close into a fresh submission.
    # Definitive zero-fill outcomes receive a separately identified attempt.
    if not created and order.status not in {"filled", "canceled", "rejected", "error"}:
        updates = []
        if order.side != side:
            order.side = side
            updates.append("side")
        if order.broker_position_ticket != broker_position.broker_position_ticket:
            order.broker_position_ticket = broker_position.broker_position_ticket
            updates.append("broker_position_ticket")
        if order.bot_id != (bot.id if bot else None):
            order.bot = bot
            updates.append("bot")
        if updates:
            order.save(update_fields=updates)

    if created:
        orders_created_total.labels(
            broker=broker_account.broker, symbol=broker_position.symbol, side=side
        ).inc()
        log_journal_event(
            "order.close_created",
            bot=bot,
            broker_account=broker_account,
            order=order,
            symbol=broker_position.symbol,
            message=f"Close ticket {broker_position.broker_position_ticket} {broker_position.symbol}",
            context={
                "qty": str(order.qty),
                "broker_position_ticket": broker_position.broker_position_ticket,
            },
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

    owner_id = validate_decision_account_scope(decision, broker_account)

    symbol = decision.signal.symbol
    side = "buy" if decision.signal.direction == "buy" else "sell"
    client_id = make_client_order_id(decision, broker_account)
    
    qty_decimal = Decimal(str(qty))
    qty = str(qty_decimal)

    params = decision.params or {}
    sl = params.get("sl")
    tp = params.get("tp")
    intent = "entry" if decision.action == "open" else "exit"
    protection_ok, protection_reason = validate_protection(
        intent=intent,
        sl=sl,
        tp=tp,
        exit_mode=decision_exit_mode(decision),
    )
    if not protection_ok:
        raise ValueError(f"Automated order protection invalid: {protection_reason}")

    # Base defaults for a new order
    defaults = {
        "bot": decision.bot,
        "decision": decision,
        "broker_account": broker_account,
        "symbol": symbol,
        "side": side,
        "qty": qty,
        "remaining_qty": qty_decimal,
        "intent": intent,
        "status": "new",
        "owner_id": owner_id,
    }

    # Create or reuse an existing order (idempotent)
    order, created = Order.objects.get_or_create(
        client_order_id=client_id,
        defaults=defaults,
    )
    validate_order_account_scope(order)

    # The strategy/decision must define protection. Order creation never fetches
    # MT5 data or invents fallback stops from an API/view process.
    dirty_fields: list[str] = []

    if sl is not None:
        order.sl = Decimal(str(sl))
        dirty_fields.append("sl")

    if tp is not None:
        order.tp = Decimal(str(tp))
        dirty_fields.append("tp")

    if dirty_fields:
        order.save(update_fields=dirty_fields)
    
    # Enforce broker-side protection before a new live entry can exist.
    protection_ok, protection_reason = validate_order_protection(order)
    if not protection_ok:
        raise ValueError(f"Order {order.id} protection invalid: {protection_reason}")

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
) -> Order:
    """
    Central place to update order status.

    - Ensures we only ever store valid statuses.
    - Optionally stores fill price and last_error.
    """
    if new_status not in VALID_STATUSES:
        raise ValueError(f"Unsupported order status: {new_status}")

    with transaction.atomic():
        locked = Order.objects.select_for_update().get(pk=order.pk)
        previous_status = locked.status
        if previous_status == new_status:
            return locked
        if not can_transition(previous_status, new_status):
            raise ValueError(f"Invalid order transition: {previous_status} -> {new_status}")
        locked.status = new_status

        if price is not None:
            locked.price = price
            locked.actual_fill_price = price

        if error_msg:
            if locked.last_error:
                locked.last_error = f"{locked.last_error}\n{error_msg}"
            else:
                locked.last_error = error_msg

        now = timezone.now()
        locked.updated_at = now
        if new_status == "ack" and locked.submitted_at is None:
            locked.submitted_at = now
        if new_status in {"filled", "canceled", "rejected", "error"}:
            locked.resolved_at = now
        locked.save(
            update_fields=[
                "status",
                "price",
                "actual_fill_price",
                "last_error",
                "submitted_at",
                "resolved_at",
                "updated_at",
            ]
        )

    log_journal_event(
        "order.status_changed",
        severity="error" if new_status in {"error", "canceled"} else "info",
        order=locked,
        bot=locked.bot,
        broker_account=locked.broker_account,
        symbol=locked.symbol,
        message=f"{locked.symbol} {locked.side} {previous_status} -> {new_status}",
        context={
            "from": previous_status,
            "to": new_status,
            "price": str(price) if price is not None else None,
            "error": error_msg,
        },
    )
    return locked
