from __future__ import annotations

import logging
from decimal import Decimal
from typing import Any, Mapping, MutableMapping

from django.apps import apps
from django.db import connections, transaction, DEFAULT_DB_ALIAS

logger = logging.getLogger(__name__)
_JOURNAL_TABLE_READY = False


def _sanitize(value: Any) -> Any:
    """
    Convert common types so they can be JSON-serialized.
    """
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    if isinstance(value, (list, tuple)):
        return [_sanitize(v) for v in value]
    if isinstance(value, Mapping):
        return {str(k): _sanitize(v) for k, v in value.items()}
    return str(value)


def log_journal_event(
    event_type: str,
    *,
    severity: str = "info",
    message: str | None = "",
    bot=None,
    broker_account=None,
    order=None,
    position=None,
    signal=None,
    decision=None,
    owner=None,
    symbol: str | None = None,
    context: Mapping[str, Any] | None = None,
):
    """
    Record a unified journal entry. Fail-soft so trading flows never break.
    """
    if event_type.startswith(("celery.", "audit.")):
        return None
    severity = severity if severity in {"info", "warning", "error"} else "info"
    message = message or ""

    derived_symbol = (
        symbol
        or getattr(order, "symbol", None)
        or getattr(position, "symbol", None)
        or getattr(signal, "symbol", None)
    ) or ""

    derived_bot = bot or getattr(order, "bot", None) or getattr(signal, "bot", None)
    derived_broker = broker_account or getattr(order, "broker_account", None) or getattr(position, "broker_account", None)

    derived_owner = (
        owner
        or getattr(derived_bot, "owner", None)
        or getattr(order, "owner", None)
        or getattr(derived_broker, "owner", None)
    )

    sanitized_context: MutableMapping[str, Any] = {}
    for key, value in (context or {}).items():
        try:
            sanitized_context[str(key)] = _sanitize(value)
        except Exception:
            sanitized_context[str(key)] = str(value)

    JournalEntry = apps.get_model("execution", "JournalEntry")

    global _JOURNAL_TABLE_READY
    if not _JOURNAL_TABLE_READY:
        try:
            tables = connections[DEFAULT_DB_ALIAS].introspection.table_names()
            if JournalEntry._meta.db_table in tables:
                _JOURNAL_TABLE_READY = True
            else:
                return None
        except Exception:
            return None

    try:
        with transaction.atomic():
            entry = JournalEntry.objects.create(
                event_type=event_type,
                severity=severity,
                message=message,
                context=sanitized_context,
                symbol=derived_symbol,
                owner=derived_owner,
                bot=derived_bot,
                broker_account=derived_broker,
                order=order,
                position=position,
                signal=signal,
                decision=decision,
            )
            return entry
    except Exception:
        logger.exception("Failed to write journal entry for %s", event_type)
        return None


__all__ = ["log_journal_event"]
