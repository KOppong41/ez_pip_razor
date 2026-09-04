"""Read-only health report for the MT5 scalper pipeline.

Run from the repository root with::

    python diagnostic_scalper.py

The command never creates signals, decisions, orders, or positions.
"""

from __future__ import annotations

import os
from datetime import datetime, timedelta, timezone as dt_timezone

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")

import django

django.setup()

from django.conf import settings
from django.db import connection
from django.db.migrations.executor import MigrationExecutor
from django.db.models import Count
from django.utils import timezone

from bots.models import Bot
from execution.connectors.mt5 import MT5Connector
from execution.models import (
    BrokerPosition,
    Decision,
    JournalEntry,
    Order,
    ScalperRunLog,
    Signal,
)
from execution.services.brokers import (
    get_broker_symbol_constraints,
    missing_entry_constraint_fields,
)
from execution.services.marketdata import get_candles_for_account


WINDOW_HOURS = 24


def _heading(title: str) -> None:
    print(f"\n{title}")
    print("-" * len(title))


def _pending_migrations() -> list[str]:
    executor = MigrationExecutor(connection)
    plan = executor.migration_plan(executor.loader.graph.leaf_nodes())
    return [f"{migration.app_label}.{migration.name}" for migration, _backward in plan]


def _timestamp_from_tick(tick) -> datetime | None:
    raw = getattr(tick, "time", None)
    if isinstance(raw, datetime):
        return raw if raw.tzinfo else raw.replace(tzinfo=dt_timezone.utc)
    if raw:
        return datetime.fromtimestamp(float(raw), tz=dt_timezone.utc)
    raw_millis = getattr(tick, "time_msc", None)
    if raw_millis:
        return datetime.fromtimestamp(float(raw_millis) / 1000, tz=dt_timezone.utc)
    return None


def _print_pipeline_totals(since: datetime) -> None:
    scalper_signals = Signal.objects.filter(
        source="scalper_engine",
        received_at__gte=since,
    )
    scalper_decisions = Decision.objects.filter(
        signal__source="scalper_engine",
        decided_at__gte=since,
    )
    scalper_orders = Order.objects.filter(
        bot__engine_mode="scalper",
        created_at__gte=since,
    )
    print(f"Signals:   {scalper_signals.count()}")
    print(f"Decisions: {scalper_decisions.count()}")
    print(f"Orders:    {scalper_orders.count()}")
    print(
        "Order statuses:",
        list(
            scalper_orders.values("intent", "status")
            .annotate(count=Count("id"))
            .order_by("intent", "status")
        ),
    )


def _print_bot_health(bot: Bot, *, now: datetime, since: datetime) -> None:
    account = bot.broker_account
    symbol = bot.asset.symbol if bot.asset else None
    print(f"\nBot {bot.id}: {bot.name}")
    print(
        "Config:",
        {
            "symbol": symbol,
            "status": bot.status,
            "auto_trade": bot.auto_trade,
            "broker_account_id": bot.broker_account_id,
            "connector": getattr(account, "connector", None),
            "account_active": getattr(account, "is_active", False),
        },
    )

    skip_counts = list(
        JournalEntry.objects.filter(
            bot=bot,
            event_type="scalper_engine_run",
            created_at__gte=since,
        )
        .values("context__outcome", "context__reason")
        .annotate(count=Count("id"))
        .order_by("-count")
    )
    print("Cycle outcomes:", skip_counts)

    strategy_counts: dict[tuple[str, str, str], int] = {}
    session_strategy_counts: dict[tuple[str, str, str, str], int] = {}
    recent_logs = ScalperRunLog.objects.filter(
        bot=bot,
        created_at__gte=since,
    ).order_by("-created_at")[:1000]
    for run in recent_logs:
        for event in (run.summary or {}).get("strategies") or []:
            key = (
                str(event.get("strategy") or "unknown"),
                str(event.get("action") or "unknown"),
                str(event.get("reason") or "unspecified"),
            )
            strategy_counts[key] = strategy_counts.get(key, 0) + 1
            session_key = (
                str(run.session or "unknown"),
                key[0],
                key[1],
                key[2],
            )
            session_strategy_counts[session_key] = session_strategy_counts.get(session_key, 0) + 1
    print(
        "Strategy outcomes:",
        [
            {
                "strategy": strategy,
                "action": action,
                "reason": reason,
                "count": count,
            }
            for (strategy, action, reason), count in sorted(
                strategy_counts.items(),
                key=lambda item: (-item[1], item[0]),
            )
        ],
    )
    print(
        "Strategy outcomes by session:",
        [
            {
                "session": session,
                "strategy": strategy,
                "action": action,
                "reason": reason,
                "count": count,
            }
            for (session, strategy, action, reason), count in sorted(
                session_strategy_counts.items(),
                key=lambda item: (-item[1], item[0]),
            )
        ],
    )

    if not account or not symbol:
        print("Market data: BLOCKED (account or symbol missing)")
        return

    constraints = get_broker_symbol_constraints(account, symbol)
    missing = missing_entry_constraint_fields(constraints)
    print("Broker constraints:", constraints)
    if missing:
        print("Constraint gate: BLOCKED", list(missing))
    else:
        print("Constraint gate: OK")

    if getattr(account, "connector", None) != "mt5_local":
        print("MT5 clock: not applicable for connector")
        return

    try:
        connector = MT5Connector()
        tick = connector.tick_for_account(account, symbol)
        tick_at = _timestamp_from_tick(tick)
        if tick_at is None:
            print("MT5 clock: BLOCKED (timestamp missing)")
        else:
            drift = (tick_at - now).total_seconds()
            tolerance = int(
                getattr(settings, "MT5_TICK_FUTURE_TOLERANCE_SECONDS", 120)
            )
            status = "BLOCKED" if drift > tolerance else "OK"
            print(
                "MT5 clock:",
                status,
                {
                    "app_utc": now.isoformat(),
                    "tick_utc": tick_at.isoformat(),
                    "tick_minus_app_seconds": round(drift, 1),
                    "future_tolerance_seconds": tolerance,
                },
            )

        candles = get_candles_for_account(account, symbol, "1m", n_bars=10)
        latest_candle = candles[-1]["time"] if candles else None
        print(
            "Latest completed M1:",
            latest_candle.isoformat() if latest_candle else "unavailable",
        )
    except Exception as exc:
        print("MT5 market-data check: BLOCKED", type(exc).__name__, str(exc)[:160])


def main() -> None:
    now = timezone.now()
    since = now - timedelta(hours=WINDOW_HOURS)
    print("=" * 72)
    print("SCALPER BOT READ-ONLY DIAGNOSTIC")
    print("=" * 72)
    print("Application UTC:", now.isoformat())
    print("Window hours:", WINDOW_HOURS)

    _heading("Deployment state")
    schedule = settings.CELERY_BEAT_SCHEDULE.get("scalper-engine-45s")
    print("Scalper schedule:", schedule or "MISSING")
    pending = _pending_migrations()
    print("Pending migrations:", pending or "none")

    _heading("Pipeline totals")
    _print_pipeline_totals(since)

    _heading("Authoritative live positions")
    positions = BrokerPosition.objects.filter(status="open")
    print("Open broker positions:", positions.count())
    print(
        list(
            positions.values(
                "id",
                "bot_id",
                "broker_account_id",
                "broker_position_ticket",
                "ownership",
                "symbol",
                "side",
                "volume",
                "sl",
                "tp",
                "last_reconciled_at",
            )[:20]
        )
    )

    _heading("Per-bot health")
    bots = Bot.objects.filter(engine_mode="scalper").select_related(
        "asset",
        "broker_account",
    )
    print("Scalper bots:", bots.count())
    for bot in bots:
        _print_bot_health(bot, now=now, since=since)

    print("\nDiagnostic complete. No trading actions were performed.")


if __name__ == "__main__":
    main()
