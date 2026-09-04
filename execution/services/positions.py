from datetime import timedelta
from django.utils import timezone
from execution.models import BrokerPosition, Position, Decision
from execution.services.orchestrator import create_close_order_for_position
from execution.services.brokers import dispatch_place_order
from execution.services.fanout import fanout_orders
from execution.services.runtime_config import RuntimeConfig, get_runtime_config
from execution.services.journal import log_journal_event


def _within_flip_cooldown(bot, symbol: str, runtime_cfg: RuntimeConfig | None = None) -> bool:
    cfg = runtime_cfg or get_runtime_config()
    cooldown_min = int(cfg.decision_flip_cooldown_min)
    if cooldown_min <= 0:
        return False
    since = timezone.now() - timedelta(minutes=cooldown_min)
    return Decision.objects.filter(
        bot=bot,
        signal__symbol=symbol,
        action="close",
        reason="flip_close",
        decided_at__gte=since,
    ).exists()


def _flip_count_today(bot, symbol: str) -> int:
    today = timezone.now().date()
    return Decision.objects.filter(
        bot=bot,
        signal__symbol=symbol,
        action="close",
        reason="flip_close",
        decided_at__date=today,
    ).count()


def prepare_flip_decisions(open_decision: Decision, flip_info: dict) -> bool:
    """
    Given an 'open' decision that wants to flip, create and dispatch a close order
    for the existing position, honoring cooldown and daily flip caps.
    """
    bot = open_decision.bot
    symbol = flip_info.get("symbol")
    if not bot or not symbol or not bot.broker_account_id:
        return False

    runtime_cfg = get_runtime_config()

    # Cooldown/daily cap check
    if _within_flip_cooldown(bot, symbol, runtime_cfg=runtime_cfg):
        return False
    max_flips = int(runtime_cfg.decision_max_flips_per_day)
    if max_flips > 0 and _flip_count_today(bot, symbol) >= max_flips:
        return False

    if bot.broker_account.connector == "mt5_local":
        positions = BrokerPosition.objects.filter(
            broker_account=bot.broker_account,
            symbol=symbol,
            status="open",
            ownership="ez_trade",
        )
        if positions.count() != 1:
            return False
        pos = positions.get()
        close_params = {"broker_position_id": pos.id}
    else:
        try:
            pos = Position.objects.get(
                broker_account=bot.broker_account,
                symbol=symbol,
                status="open",
            )
        except Position.DoesNotExist:
            return False
        close_params = {"position_id": pos.id}

    # Create a synthetic "close" decision tied to the open decision's signal
    close_decision = Decision.objects.create(
        bot=bot,
        signal=open_decision.signal,
        action="close",
        reason="flip_close",
        score=open_decision.score,
        params=close_params,
    )

    # Create close order idempotently and dispatch
    orders = fanout_orders(close_decision, master_qty=None)
    if not orders:
        log_journal_event(
            "position.flip_failed",
            severity="warning",
            bot=bot,
            decision=open_decision,
            symbol=symbol,
            message="Flip close order could not be created",
            context={"state": "close_requested"},
        )
        return False
    for order, created in orders:
        try:
            dispatch_place_order(order)
        except Exception as exc:
            log_journal_event(
                "position.flip_failed",
                severity="error",
                bot=bot,
                decision=open_decision,
                order=order,
                symbol=symbol,
                message="Flip close failed; reverse entry blocked",
                context={"state": "close_failed", "error": str(exc)},
            )
            return False
        order.refresh_from_db()
        if order.status != "filled":
            log_journal_event(
                "position.flip_pending",
                severity="warning",
                bot=bot,
                decision=open_decision,
                order=order,
                symbol=symbol,
                message="Flip close is not broker-confirmed; reverse entry blocked",
                context={"state": "close_submitted", "order_status": order.status},
            )
            return False

    still_open = bot.broker_account.broker_positions.filter(
        symbol=symbol,
        ownership="ez_trade",
        status="open",
    ).exists()
    if still_open:
        return False
    log_journal_event(
        "position.flip_close_confirmed",
        bot=bot,
        decision=open_decision,
        symbol=symbol,
        message="Original position closed; reverse entry permitted",
        context={"state": "close_confirmed"},
    )
    return True
