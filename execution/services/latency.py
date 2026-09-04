from __future__ import annotations

import logging
from datetime import datetime

from django.utils import timezone

from core.metrics import execution_latency_seconds
from execution.models import Order


logger = logging.getLogger(__name__)

ORDER_TIMESTAMP_FIELDS = {
    "execution_queued_at",
    "mt5_worker_started_at",
    "risk_validation_completed_at",
    "order_send_called_at",
    "broker_response_received_at",
    "execution_recorded_at",
}


def mark_order_timestamp(
    order_or_id: Order | int,
    field: str,
    *,
    at: datetime | None = None,
) -> bool:
    """Persist the first observation of an execution milestone."""
    if field not in ORDER_TIMESTAMP_FIELDS:
        raise ValueError(f"Unsupported execution timestamp: {field}")
    order_id = order_or_id.pk if isinstance(order_or_id, Order) else int(order_or_id)
    observed_at = at or timezone.now()
    updated = Order.objects.filter(
        pk=order_id,
        **{f"{field}__isnull": True},
    ).update(**{field: observed_at})
    if updated and isinstance(order_or_id, Order):
        setattr(order_or_id, field, observed_at)
    return bool(updated)


def _milliseconds(start: datetime | None, end: datetime | None) -> float | None:
    if start is None or end is None:
        return None
    return max(0.0, round((end - start).total_seconds() * 1000, 3))


def execution_latency_snapshot(order: Order) -> dict:
    """Return persisted milestones and derived stage latencies in milliseconds."""
    decision = getattr(order, "decision", None)
    signal = getattr(decision, "signal", None) if decision else None
    timestamps = {
        "signal_received_at": getattr(signal, "received_at", None),
        "strategy_evaluation_started_at": getattr(decision, "evaluation_started_at", None),
        "decision_created_at": getattr(decision, "decided_at", None),
        "execution_queued_at": order.execution_queued_at,
        "mt5_worker_started_at": order.mt5_worker_started_at,
        "risk_validation_completed_at": order.risk_validation_completed_at,
        "order_send_called_at": order.order_send_called_at,
        "broker_response_received_at": order.broker_response_received_at,
        "execution_recorded_at": order.execution_recorded_at,
    }
    latency_ms = {
        "signal_to_decision": _milliseconds(
            timestamps["signal_received_at"], timestamps["decision_created_at"]
        ),
        "strategy_evaluation": _milliseconds(
            timestamps["strategy_evaluation_started_at"], timestamps["decision_created_at"]
        ),
        "decision_to_queue": _milliseconds(
            timestamps["decision_created_at"], timestamps["execution_queued_at"]
        ),
        "queue_wait": _milliseconds(
            timestamps["execution_queued_at"], timestamps["mt5_worker_started_at"]
        ),
        "worker_to_risk_validation": _milliseconds(
            timestamps["mt5_worker_started_at"], timestamps["risk_validation_completed_at"]
        ),
        "risk_to_order_send": _milliseconds(
            timestamps["risk_validation_completed_at"], timestamps["order_send_called_at"]
        ),
        "mt5_execution": _milliseconds(
            timestamps["order_send_called_at"], timestamps["broker_response_received_at"]
        ),
        "signal_to_broker": _milliseconds(
            timestamps["signal_received_at"], timestamps["broker_response_received_at"]
        ),
        "signal_to_execution_recorded": _milliseconds(
            timestamps["signal_received_at"], timestamps["execution_recorded_at"]
        ),
    }
    return {
        "timestamps": {
            name: value.isoformat() if value is not None else None
            for name, value in timestamps.items()
        },
        "latency_ms": latency_ms,
    }


def mark_execution_recorded(order: Order) -> None:
    """Complete telemetry once and publish every available stage histogram."""
    if not mark_order_timestamp(order, "execution_recorded_at"):
        return
    current = Order.objects.select_related("decision__signal").get(pk=order.pk)
    snapshot = execution_latency_snapshot(current)
    for stage, milliseconds in snapshot["latency_ms"].items():
        if milliseconds is not None:
            execution_latency_seconds.labels(stage=stage).observe(milliseconds / 1000)
    logger.info("Execution latency order=%s telemetry=%s", order.pk, snapshot)
