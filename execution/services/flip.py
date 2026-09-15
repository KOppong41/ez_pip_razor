"""Durable reversal workflow. The caller holds the broker session lock."""
from datetime import timedelta

from django.utils import timezone

from execution.connectors.base import ConnectorError
from execution.models import BrokerPosition, Decision, Order
from execution.services.journal import log_journal_event
from execution.services.orchestrator import create_close_order_for_position, update_order_status
from execution.services.runtime_config import get_runtime_config


def is_flip_order(order):
    return bool(order.intent == "entry" and order.decision_id and order.decision.params.get("flip_requested"))


def _state(order, state, *, reason=None, **details):
    decision = order.decision
    decision.refresh_from_db()
    params = {**decision.params, **details, "flip_state": state}
    history = list(params.get("flip_history") or [])
    history.append({"state": state, "at": timezone.now().isoformat(), **({"reason": reason} if reason else {})})
    params["flip_history"] = history[-30:]
    decision.params = params
    fields = ["params"]
    if state in {"preflight_rejected", "close_failed", "reverse_aborted_after_close"}:
        decision.action, decision.reason = "ignore", reason or f"flip_{state}"
        fields += ["action", "reason"]
    decision.save(update_fields=fields)
    log_journal_event(
        f"position.flip_{state}", bot=order.bot, decision=decision, order=order,
        broker_account=order.broker_account, symbol=order.symbol,
        severity="warning" if reason else "info", message=reason or f"Flip {state.replace('_', ' ')}",
        context={"state": state, **details},
    )


def _reject(order, state, reason, error):
    _state(order, state, reason=reason, flip_error=str(error))
    order.refresh_from_db()
    Order.objects.filter(pk=order.pk).update(risk_reserved_at=None)
    if order.status == "new":
        update_order_status(order, "rejected", error_msg=f"{reason}: {error}")


def _flip_limits(order):
    config = get_runtime_config()
    prior = Decision.objects.filter(
        bot=order.bot, signal__symbol=order.symbol, action="close", reason="flip_close",
    ).exclude(signal=order.decision.signal)
    if config.decision_flip_cooldown_min > 0 and prior.filter(
        decided_at__gte=timezone.now() - timedelta(minutes=config.decision_flip_cooldown_min),
    ).exists():
        raise ValueError("flip_cooldown_active")
    count = prior.filter(decided_at__date=timezone.now().date()).values("signal_id").distinct().count()
    if config.decision_max_flips_per_day > 0 and count >= config.decision_max_flips_per_day:
        raise ValueError("flip_daily_cap")


def _group(order):
    from execution.services.opposite_scalp import is_overlay
    expected = set(order.decision.params.get("flip_position_ids") or [])
    if not expected:
        raise ValueError("flip_group_missing")
    if order.broker_account.connector == "paper":
        from execution.models import Position
        positions = list(Position.objects.filter(pk__in=expected, broker_account=order.broker_account, symbol=order.symbol))
        if len(expected) != 1 or len(positions) != 1:
            raise ValueError("flip_group_ownership_changed")
        position = positions[0]
        if position.status == "open" and ("buy" if position.qty > 0 else "sell") == order.side:
            raise ValueError("flip_group_changed")
        position.broker_position_ticket = position.pk
        return positions
    positions = list(BrokerPosition.objects.filter(
        pk__in=expected, bot=order.bot, broker_account=order.broker_account,
        symbol=order.symbol, ownership="ez_trade",
    ).select_related("originating_order__decision"))
    if {position.pk for position in positions} != expected:
        raise ValueError("flip_group_ownership_changed")
    primaries = [position for position in positions if not is_overlay(position)]
    if len(primaries) != 1 or primaries[0].side == order.side or len(positions) > 2:
        raise ValueError("flip_group_ambiguous")
    primary = primaries[0]
    for child in positions:
        if child.pk == primary.pk:
            continue
        if child.side == primary.side or child.originating_order.decision.params.get("primary_position_id") != primary.pk:
            raise ValueError("flip_overlay_parent_mismatch")
    if BrokerPosition.objects.filter(
        bot=order.bot, broker_account=order.broker_account, symbol=order.symbol,
        ownership="ez_trade", status="open",
    ).exclude(pk__in=expected).exists():
        raise ValueError("flip_group_changed")
    return sorted(positions, key=lambda position: position.pk == primary.pk)


def _conditions(order):
    from execution.services.brokers import validate_order_conditions
    from execution.services.orchestrator import validate_order_account_scope
    from execution.services.trading_type import is_within_trading_window
    order.bot.refresh_from_db()
    order.broker_account.refresh_from_db()
    validate_order_account_scope(order)
    if not is_within_trading_window(order.bot):
        raise ValueError("outside_trading_window")
    valid, reason = validate_order_conditions(order)
    if not valid:
        raise ValueError(reason)


def execute_flip(order, connector, submit):
    """Preflight, close linked group, confirm flat, revalidate and submit.

    submit bypasses this wrapper. Retries reconcile the existing close/reverse
    order identities, never turn an ambiguous request into another send.
    """
    order.refresh_from_db()
    order.decision.refresh_from_db()
    state = order.decision.params.get("flip_state", "pending")
    if order.status == "filled":
        if state != "completed":
            _state(order, "completed")
        return
    if order.status in {"canceled", "rejected", "error"}:
        return
    if state in {"preflight_rejected", "close_failed", "reverse_aborted_after_close"}:
        return
    if state not in {"close_confirmed", "reverse_pending", "completed"}:
        try:
            positions = _group(order)
            if state == "pending":
                _flip_limits(order)
                _conditions(order)
                if any(position.status != "open" for position in positions):
                    raise ValueError("flip_group_changed")
                if Order.objects.filter(
                    broker_account=order.broker_account, intent="exit",
                    broker_position_ticket__in=[position.broker_position_ticket for position in positions],
                    status__in=["new", "ack", "part_filled"],
                ).exists():
                    raise ValueError("flip_group_already_closing")
                connector.preflight_flip(order, positions)
                _state(order, "preflight_passed")
        except Exception as exc:
            _reject(order, "preflight_rejected" if state == "pending" else "close_failed",
                    "flip_preflight_rejected" if state == "pending" else "flip_group_close_incomplete", exc)
            return
        for position in positions:
            if position.status == "closed":
                continue
            close_order = None
            try:
                close_order, _ = create_close_order_for_position(position, order.broker_account)
                if not close_order.decision_id:
                    close_order.decision = Decision.objects.create(
                        bot=order.bot, signal=order.decision.signal, action="close", reason="flip_close",
                        score=order.decision.score,
                        params={"position_id" if order.broker_account.connector == "paper" else "broker_position_id": position.pk,
                                "flip_entry_order_id": order.pk},
                    )
                    close_order.save(update_fields=["decision"])
                close_ids = list(order.decision.params.get("flip_close_order_ids") or [])
                if close_order.pk not in close_ids:
                    close_ids.append(close_order.pk)
                _state(order, "closing", flip_close_order_ids=close_ids)
                submit(close_order)
                close_order.refresh_from_db()
                position.refresh_from_db()
                if close_order.status != "filled" or position.status != "closed":
                    raise ConnectorError("flip_close_confirmation_pending")
            except Exception as exc:
                if close_order is None:
                    _reject(order, "close_failed", "flip_group_close_incomplete", exc)
                    return
                close_order.refresh_from_db()
                ambiguous = close_order.attempts.filter(status__in=["submitting", "ambiguous"]).exists()
                if close_order.status in {"rejected", "error", "canceled"} and not ambiguous:
                    _reject(order, "close_failed", "flip_group_close_incomplete", exc)
                    return
                _state(order, "close_pending", reason="flip_close_confirmation_pending", flip_error=str(exc))
                raise ConnectorError("Flip close is not confirmed; reverse entry held") from exc
        raw = connector.positions_for_account(order.broker_account)
        tickets = {position.broker_position_ticket for position in positions}
        if raw is None or any(int(position.ticket) in tickets for position in raw):
            _state(order, "close_pending", reason="flip_broker_flat_unconfirmed")
            raise ConnectorError("Broker has not confirmed the flip group is flat")
        _state(order, "close_confirmed")
        from execution.services.decision import _record_scalper_flip
        _record_scalper_flip(order.bot, order.symbol)
    try:
        if state != "reverse_pending":
            _conditions(order)
        submit(order)  # No preflight exposure exclusions on final submission.
        order.refresh_from_db()
        if order.status in {"rejected", "error", "canceled", "new"}:
            raise ConnectorError(order.last_error or "Reverse order was not submitted")
    except Exception as exc:
        order.refresh_from_db()
        uncertain = order.status in {"ack", "part_filled"} or order.attempts.filter(status__in=["submitting", "ambiguous"]).exists()
        if uncertain:
            _state(order, "reverse_pending", reason="flip_reverse_confirmation_pending", flip_error=str(exc))
            raise
        _reject(order, "reverse_aborted_after_close", "flip_reverse_aborted_after_close", exc)
        return
    _state(order, "completed" if order.status == "filled" else "reverse_pending")
