from decimal import Decimal
import logging
import re
from typing import List, Tuple

from execution.models import Decision, Order
from execution.services.orchestrator import (
    create_order_from_decision,
    create_close_order_for_position,
    make_client_order_id,
)
from copytrade.services import eligible_followers, compute_allocation
from django.utils import timezone
from django.db import transaction
from datetime import timedelta
from execution.services.runtime_config import get_runtime_config
from execution.services.trading_type import is_within_trading_window
from execution.services.psychology import get_size_multiplier

logger = logging.getLogger(__name__)


def fanout_orders(decision: Decision, master_qty: str | None) -> List[Tuple[Order, bool]]:
    """
    Single-account mode: create (or reuse) one order for the Bot's broker_account.
    Returns [(order, created)] or [] if no account available.
    """
    runtime_cfg = get_runtime_config()
    bot = decision.bot
    if not bot or not bot.broker_account or not bot.broker_account.is_active:
        return []

    if not is_within_trading_window(bot):
        logger.info("Skipping order: bot %s is outside its trading window", bot.name)
        return []

    params = decision.params or {}

    qty = Decimal(master_qty) if master_qty is not None else Decimal(str(bot.default_qty))
    qty_override = params.get("qty_override") or params.get("qty")
    qty_multiplier = params.get("qty_multiplier")
    asset_min_qty = Decimal("0")
    try:
        if bot and bot.asset:
            asset_min_qty = Decimal(str(bot.asset.min_qty))
    except Exception:
        asset_min_qty = Decimal("0")

    if qty_override is not None:
        try:
            qty = Decimal(str(qty_override))
        except Exception:
            pass  # fall back to default if parsing fails
    elif qty_multiplier is not None:
        try:
            qty = qty * Decimal(str(qty_multiplier))
        except Exception:
            pass
    else:
        # Apply psychology-based size adjustment only when we are not explicitly overriding qty.
        try:
            mult = get_size_multiplier(bot)
            qty = qty * mult
        except Exception:
            # If anything goes wrong, keep original qty.
            pass

    # Hard cap by lot size if configured
    if runtime_cfg.max_order_lot > 0:
        cap = runtime_cfg.max_order_lot
        if asset_min_qty > 0 and cap < asset_min_qty:
            # Avoid capping below the broker's minimum trade size
            logger.warning(
                "max_order_lot %s is below broker min %s for %s; using broker min instead",
                cap,
                asset_min_qty,
                bot.asset.symbol if bot and bot.asset else "unknown",
            )
            cap = asset_min_qty
        if qty > cap:
            qty = cap

    # Ensure we never send smaller than the broker's minimum
    if asset_min_qty > 0 and qty < asset_min_qty:
        qty = asset_min_qty

    if qty <= 0:
        return []

    if decision.action == "close":
        # Close decision should carry a position reference via params["position_id"], else we can't proceed
        pos_id = (decision.params or {}).get("position_id")
        if not pos_id:
            return []
        try:
            from execution.models import Position
            pos = Position.objects.get(id=pos_id, broker_account=bot.broker_account, status="open")
        except Position.DoesNotExist:
            return []
        order, created = create_close_order_for_position(pos, bot.broker_account)
        return [(order, created)]

    # Cooldown: prevent multiple open orders for same bot/symbol within a short window (uses max of config and timeframe)
    def _tf_seconds(tf: str) -> int:
        if not tf:
            return 0
        m = re.match(r"(?i)(\d+)([mh])", tf)
        if not m:
            return 0
        val = int(m.group(1))
        unit = m.group(2).lower()
        return val * 60 if unit == "m" else val * 3600

    cfg_cooldown = int(runtime_cfg.decision_order_cooldown_sec)
    tf_cooldown = _tf_seconds(getattr(decision.signal, "timeframe", ""))
    cooldown = max(cfg_cooldown, tf_cooldown)
    if decision.action == "open" and cooldown > 0:
        # If an order already exists for this decision, reuse it and allow redispatch (e.g., Celery retry).
        client_id = make_client_order_id(decision, bot.broker_account)
        qs = Order.objects.filter(client_order_id=client_id)
        try:
            # Only lock when inside an atomic block; Celery tasks run autocommit so select_for_update would blow up.
            if transaction.get_connection().in_atomic_block:
                qs = qs.select_for_update()
            existing = qs.first()
        except transaction.TransactionManagementError:
            existing = qs.first()

        if existing:
            # Allow redispatch if broker hasn't resolved it or we're retrying after cancel/error.
            if existing.status in ("new", "ack", "canceled", "error"):
                if existing.status in ("canceled", "error"):
                    existing.status = "new"
                    existing.last_error = ""
                    existing.save(update_fields=["status", "last_error"])
                return [(existing, False)]
            return []

        cutoff = timezone.now() - timedelta(seconds=cooldown)
        recent = Order.objects.filter(
            broker_account=bot.broker_account,
            symbol=decision.signal.symbol,
            created_at__gte=cutoff,
            status__in=["new", "ack", "filled", "part_filled"],
        ).exists()
        if recent:
            return []

    order, created = create_order_from_decision(decision, bot.broker_account, str(qty), atr=None)
    return [(order, created)]
