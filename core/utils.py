from .models import Audit, CeleryActivity
import json
import logging
from decimal import Decimal


structured_logger = logging.getLogger("structured")


def _make_json_safe(value):
    """
    Recursively converts values so they can be JSON-serialized by PostgreSQL.
    Decimals are stringified to preserve precision.
    """
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, dict):
        return {k: _make_json_safe(v) for k, v in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [_make_json_safe(v) for v in value]
    return value


def audit_log(action: str, entity: str, entity_id, payload=None, actor=None, extra=None):
    """
    Create a simple audit row. `extra` is merged into payload for callers that
    want to append additional context.
    """
    data = payload or {}
    if extra:
        data = {**data, **extra}

    safe_payload = _make_json_safe(data)

    Audit.objects.create(
        action=action,
        entity=entity,
        entity_id=str(entity_id),
        payload=safe_payload,
        actor=actor,
    )


def structured_log(action: str, **fields):
    """
    Emit a JSON log line with a consistent 'action' field for downstream ingestion.
    """
    record = {"action": action, **fields}
    try:
        structured_logger.info(json.dumps(record, default=str))
    except Exception:
        # Fallback to plain logging if JSON serialization fails
        try:
            structured_logger.info({"action": action, **fields})
        except Exception:
            # last resort: print
            print(record)


def celery_log(component: str, message: str, level: str = "INFO", task_name: str | None = None, task_id: str | None = None, payload=None):
    """
    Persist a lightweight Celery activity row for traceability.
    """
    safe_payload = _make_json_safe(payload or {})
    CeleryActivity.objects.create(
        component=component,
        message=message,
        level=level,
        task_name=task_name or "",
        task_id=str(task_id) if task_id else "",
        payload=safe_payload,
    )
