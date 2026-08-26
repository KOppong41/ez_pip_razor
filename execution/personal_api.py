from __future__ import annotations

from decimal import Decimal

from django.db import transaction
from django.utils import timezone
from rest_framework import status
from rest_framework.decorators import api_view, permission_classes
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response

from bots.models import Asset, Bot, Strategy
from brokers.models import BrokerAccount
from execution.models import (
    AccountSnapshot,
    BrokerPosition,
    BrokerSymbolMapping,
    Execution,
    JournalEntry,
    MT5ConnectionState,
    Order,
    RiskPolicy,
    Signal,
    ScalperRunLog,
    TradeLog,
)
from execution.mt5_tasks import (
    check_mt5_account_task,
    execute_mt5_order_task,
    modify_mt5_position_task,
    refresh_mt5_markets_task,
)
from execution.services.orchestrator import create_close_order_for_position
from execution.utils.symbols import canonical_symbol


def _accounts_for(user):
    queryset = BrokerAccount.objects.filter(connector="mt5_local")
    return queryset if user.is_superuser else queryset.filter(owner=user)


def _account_for(request):
    queryset = _accounts_for(request.user)
    account_id = request.data.get("broker_account_id") if request.method != "GET" else request.query_params.get("broker_account_id")
    if account_id:
        return queryset.get(pk=account_id)
    if queryset.count() == 1:
        return queryset.get()
    raise ValueError("broker_account_id is required when zero or multiple MT5 accounts exist")


def _position_dict(position: BrokerPosition) -> dict:
    return {
        "id": position.id,
        "broker_position_ticket": position.broker_position_ticket,
        "ownership": position.ownership,
        "symbol": position.symbol,
        "side": position.side,
        "volume": position.volume,
        "entry": position.open_price,
        "current_price": position.current_price,
        "sl": position.sl,
        "tp": position.tp,
        "floating_pnl": position.profit,
        "swap": position.swap,
        "opened_at": position.opened_at,
        "strategy": position.strategy_name,
        "manageable": position.is_manageable,
        "status": position.status,
    }


def _personal_market_rows(account: BrokerAccount) -> list[dict]:
    assets_by_canonical = {}
    for asset in Asset.objects.filter(is_active=True).order_by("category", "symbol"):
        canonical = canonical_symbol(asset.symbol)
        if canonical:
            assets_by_canonical.setdefault(canonical, asset)

    mappings = {
        row.canonical_symbol: row
        for row in BrokerSymbolMapping.objects.filter(broker_account=account)
    }
    rows = []
    for canonical in sorted(set(assets_by_canonical) | set(mappings)):
        asset = assets_by_canonical.get(canonical)
        mapping = mappings.get(canonical)
        rows.append(
            {
                "id": mapping.id if mapping else None,
                "asset_id": asset.id if asset else None,
                "canonical_symbol": canonical,
                "symbol": asset.symbol if asset else canonical,
                "display_name": asset.display_name if asset else canonical,
                "category": asset.category if asset else "other",
                "min_qty": asset.min_qty if asset else None,
                "recommended_qty": asset.recommended_qty if asset else None,
                "max_spread": asset.max_spread if asset else None,
                "broker_symbol": mapping.broker_symbol if mapping else "",
                "enabled": mapping.enabled if mapping else False,
                "bid": mapping.bid if mapping else None,
                "ask": mapping.ask if mapping else None,
                "spread": mapping.spread if mapping else None,
                "trading_status": mapping.trading_status if mapping else "not_synced",
                "last_error": mapping.last_error if mapping else "",
                "last_tick_at": mapping.last_tick_at if mapping else None,
            }
        )
    return rows


@api_view(["GET"])
@permission_classes([IsAuthenticated])
def personal_dashboard(request):
    try:
        account = _account_for(request)
    except (BrokerAccount.DoesNotExist, ValueError) as exc:
        return Response({"detail": str(exc)}, status=status.HTTP_400_BAD_REQUEST)

    state = MT5ConnectionState.objects.filter(broker_account=account).first()
    snapshot = AccountSnapshot.objects.filter(broker_account=account).first()
    risk, _ = RiskPolicy.objects.get_or_create(broker_account=account)
    positions = BrokerPosition.objects.filter(broker_account=account, status="open")
    today = timezone.localdate()
    entries = Order.objects.filter(
        broker_account=account,
        intent="entry",
        submitted_at__date=today,
        status__in=["ack", "part_filled", "filled"],
    ).count()
    completed = TradeLog.objects.filter(broker_account=account, closed_at__date=today)
    realized = sum((row.pnl or Decimal("0") for row in completed), Decimal("0"))
    floating = sum((row.profit for row in positions), Decimal("0"))
    start_equity = None
    drawdown = Decimal("0")
    if snapshot:
        day_values = list(
            AccountSnapshot.objects.filter(
                broker_account=account,
                captured_at__date=today,
            ).order_by("captured_at").values_list("equity", flat=True)
        )
        if day_values:
            start_equity = day_values[0]
            peak = max(day_values)
            drawdown = ((peak - snapshot.equity) / peak * 100) if peak > 0 else Decimal("0")

    bots = Bot.objects.filter(broker_account=account)
    return Response(
        {
            "bot": {
                "running": bots.filter(status="active").exists() and risk.entries_enabled and not risk.emergency_stop,
                "statuses": list(bots.values("id", "name", "status", "engine_mode")),
                "emergency_stop": risk.emergency_stop,
            },
            "mt5": {
                "connected": bool(state and state.connected),
                "checked_at": state.checked_at if state else None,
                "last_error": state.last_error if state else "not checked",
                "account_mode": state.account_mode if state else "unknown",
            },
            "account": {
                "id": account.id,
                "alias": account.name,
                "login": state.active_login if state else account.mt5_login,
                "broker": state.company if state else account.broker,
                "server": state.active_server if state else account.mt5_server,
                "currency": snapshot.currency if snapshot else account.base_ccy,
                "leverage": state.leverage if state else account.leverage,
            },
            "financial": {
                "balance": snapshot.balance if snapshot else None,
                "equity": snapshot.equity if snapshot else None,
                "floating_pnl": floating,
                "realized_pnl_today": realized,
                "drawdown_pct": drawdown,
                "start_equity": start_equity,
                "margin": snapshot.margin if snapshot else None,
                "free_margin": snapshot.free_margin if snapshot else None,
                "margin_level": snapshot.margin_level if snapshot else None,
            },
            "trading": {
                "active_positions": positions.count(),
                "today_entries": entries,
                "winning_trades_today": completed.filter(pnl__gt=0).count(),
                "losing_trades_today": completed.filter(pnl__lt=0).count(),
                "enabled_symbols": list(
                    BrokerSymbolMapping.objects.filter(broker_account=account, enabled=True).values_list(
                        "canonical_symbol", flat=True
                    )
                ),
            },
        }
    )


@api_view(["POST"])
@permission_classes([IsAuthenticated])
@transaction.atomic
def personal_control(request):
    try:
        account = _account_for(request)
    except (BrokerAccount.DoesNotExist, ValueError) as exc:
        return Response({"detail": str(exc)}, status=status.HTTP_400_BAD_REQUEST)
    action = str(request.data.get("action", "")).lower()
    policy, _ = RiskPolicy.objects.select_for_update().get_or_create(broker_account=account)
    bots = Bot.objects.filter(broker_account=account)
    if action == "start":
        state = MT5ConnectionState.objects.filter(broker_account=account).first()
        if not state or not state.connected:
            return Response({"detail": "MT5 must be connected before starting"}, status=409)
        if state.account_mode == "live" and not policy.live_trading_confirmed:
            return Response({"detail": "Live trading has not been explicitly confirmed"}, status=409)
        policy.emergency_stop = False
        policy.entries_enabled = True
        policy.save(update_fields=["emergency_stop", "entries_enabled", "updated_at"])
        bots.update(status="active")
    elif action == "stop":
        policy.entries_enabled = False
        policy.save(update_fields=["entries_enabled", "updated_at"])
        bots.update(status="stopped")
    elif action == "emergency_stop":
        policy.entries_enabled = False
        policy.emergency_stop = True
        policy.save(update_fields=["entries_enabled", "emergency_stop", "updated_at"])
        bots.update(status="stopped")
        if bool(request.data.get("close_owned_positions")) and policy.emergency_close_owned_positions:
            for position in BrokerPosition.objects.filter(
                broker_account=account,
                ownership="ez_trade",
                status="open",
            ):
                order, _ = create_close_order_for_position(position, account)
                execute_mt5_order_task.apply_async(args=[order.id], queue="mt5_execution")
    else:
        return Response({"detail": "action must be start, stop, or emergency_stop"}, status=400)
    return Response({"action": action, "entries_enabled": policy.entries_enabled, "emergency_stop": policy.emergency_stop})


@api_view(["GET", "PATCH"])
@permission_classes([IsAuthenticated])
def personal_markets(request):
    try:
        account = _account_for(request)
    except (BrokerAccount.DoesNotExist, ValueError) as exc:
        return Response({"detail": str(exc)}, status=400)
    if request.method == "PATCH":
        canonical = str(request.data.get("canonical_symbol", "")).upper()
        supported = {
            canonical_symbol(symbol)
            for symbol in Asset.objects.filter(is_active=True).values_list(
                "symbol", flat=True
            )
        }
        if canonical not in supported:
            return Response(
                {"detail": "This asset is not enabled by the platform."}, status=400
            )
        mapping, _ = BrokerSymbolMapping.objects.get_or_create(
            broker_account=account,
            canonical_symbol=canonical,
        )
        mapping.enabled = bool(request.data.get("enabled"))
        mapping.save(update_fields=["enabled"])
    return Response(_personal_market_rows(account))


@api_view(["GET"])
@permission_classes([IsAuthenticated])
def personal_strategies(request):
    try:
        account = _account_for(request)
    except (BrokerAccount.DoesNotExist, ValueError) as exc:
        return Response({"detail": str(exc)}, status=400)
    data = []
    for bot in Bot.objects.filter(broker_account=account).prefetch_related("strategies"):
        latest = Signal.objects.filter(bot=bot).order_by("-received_at").first()
        decision = latest.decisions.order_by("-decided_at").first() if latest else None
        data.append(
            {
                "bot_id": bot.id,
                "bot_name": bot.name,
                "enabled": bot.status == "active",
                "engine_mode": bot.engine_mode,
                "timeframe": bot.default_timeframe,
                "priority": list(bot.strategies.filter(enabled=True).values("id", "name", "version", "params")),
                "latest_signal": latest.direction if latest else None,
                "last_signal_time": latest.received_at if latest else None,
                "accepted": decision.action == "open" if decision else None,
                "rejection_reason": decision.reason if decision and decision.action != "open" else "",
            }
        )
    return Response(data)


@api_view(["GET"])
@permission_classes([IsAuthenticated])
def personal_positions(request):
    try:
        account = _account_for(request)
    except (BrokerAccount.DoesNotExist, ValueError) as exc:
        return Response({"detail": str(exc)}, status=400)
    return Response([_position_dict(row) for row in BrokerPosition.objects.filter(broker_account=account)])


@api_view(["POST"])
@permission_classes([IsAuthenticated])
def personal_position_action(request, position_id: int):
    queryset = BrokerPosition.objects.select_related("broker_account")
    if not request.user.is_superuser:
        queryset = queryset.filter(owner=request.user)
    try:
        position = queryset.get(pk=position_id)
    except BrokerPosition.DoesNotExist:
        return Response({"detail": "Position not found"}, status=404)
    if not position.is_manageable:
        return Response({"detail": "Manual/external positions are read-only"}, status=403)
    action = str(request.data.get("action", "")).lower()
    if action == "close":
        order, _ = create_close_order_for_position(position, position.broker_account)
        task = execute_mt5_order_task.apply_async(args=[order.id], queue="mt5_execution")
    elif action in {"modify_sl", "modify_tp", "modify_protection"}:
        sl = request.data.get("sl")
        tp = request.data.get("tp")
        if sl is None and tp is None:
            return Response({"detail": "sl or tp is required"}, status=400)
        task = modify_mt5_position_task.apply_async(
            args=[position.id],
            kwargs={"sl": sl, "tp": tp},
            queue="mt5_execution",
        )
    else:
        return Response({"detail": "Unsupported position action"}, status=400)
    return Response({"task_id": task.id}, status=status.HTTP_202_ACCEPTED)


@api_view(["GET", "PATCH"])
@permission_classes([IsAuthenticated])
def personal_risk(request):
    try:
        account = _account_for(request)
    except (BrokerAccount.DoesNotExist, ValueError) as exc:
        return Response({"detail": str(exc)}, status=400)
    policy, _ = RiskPolicy.objects.get_or_create(broker_account=account)
    editable = {
        "risk_per_trade_pct",
        "max_daily_loss_pct",
        "max_account_drawdown_pct",
        "max_positions",
        "max_positions_per_symbol",
        "max_entry_trades_per_day",
        "max_lot",
        "max_spread_points",
        "deviation_points",
        "stop_after_daily_profit_pct",
        "emergency_close_owned_positions",
        "live_trading_confirmed",
    }
    if request.method == "PATCH":
        for field in editable:
            if field in request.data:
                setattr(policy, field, request.data[field])
        policy.full_clean()
        policy.save()
    fields = ["id", "broker_account_id", *sorted(editable), "entries_enabled", "emergency_stop", "updated_at"]
    return Response({field: getattr(policy, field) for field in fields})


@api_view(["GET"])
@permission_classes([IsAuthenticated])
def personal_history(request):
    try:
        account = _account_for(request)
    except (BrokerAccount.DoesNotExist, ValueError) as exc:
        return Response({"detail": str(exc)}, status=400)
    rows = TradeLog.objects.filter(broker_account=account).order_by("-closed_at", "-created_at")[:1000]
    values = list(
        rows.values(
            "id", "created_at", "closed_at", "symbol", "side", "qty", "price", "exit_price", "pnl", "status", "broker_ticket"
        )
    )
    wins = sum(1 for row in values if (row["pnl"] or 0) > 0)
    losses = sum(1 for row in values if (row["pnl"] or 0) < 0)
    gross_profit = sum((row["pnl"] or Decimal("0") for row in values if (row["pnl"] or 0) > 0), Decimal("0"))
    gross_loss = sum((row["pnl"] or Decimal("0") for row in values if (row["pnl"] or 0) < 0), Decimal("0"))
    return Response(
        {
            "summary": {
                "total_trades": len(values),
                "wins": wins,
                "losses": losses,
                "win_rate": Decimal(wins * 100) / len(values) if values else Decimal("0"),
                "gross_profit": gross_profit,
                "gross_loss": gross_loss,
                "net_profit": gross_profit + gross_loss,
                "profit_factor": gross_profit / abs(gross_loss) if gross_loss else None,
            },
            "trades": values,
        }
    )


@api_view(["GET"])
@permission_classes([IsAuthenticated])
def personal_logs(request):
    queryset = JournalEntry.objects.select_related("broker_account")
    if not request.user.is_superuser:
        queryset = queryset.filter(owner=request.user)
    level = request.query_params.get("level")
    if level:
        queryset = queryset.filter(severity=level.lower())
    return Response(list(queryset[:500].values("id", "created_at", "event_type", "severity", "message", "symbol", "context")))


@api_view(["GET"])
@permission_classes([IsAuthenticated])
def personal_backtesting(request):
    """Expose persisted engine/backtest evidence without running MT5 in a request."""
    queryset = ScalperRunLog.objects.select_related("bot").order_by("-created_at")
    if not request.user.is_superuser:
        queryset = queryset.filter(bot__owner=request.user)
    return Response(
        list(
            queryset[:250].values(
                "id",
                "bot_id",
                "bot__name",
                "timeframe",
                "session",
                "summary",
                "created_at",
            )
        )
    )


@api_view(["GET", "POST", "PATCH"])
@permission_classes([IsAuthenticated])
def personal_accounts(request):
    queryset = _accounts_for(request.user)
    if request.method == "GET":
        return Response(
            list(
                queryset.values(
                    "id", "name", "broker", "mt5_login", "mt5_server", "mt5_path", "base_ccy", "leverage", "is_active", "is_verified"
                )
            )
        )
    account_id = request.data.get("id")
    account = queryset.filter(pk=account_id).first() if account_id else BrokerAccount(owner=request.user)
    if account is None:
        return Response({"detail": "Account not found"}, status=404)
    account.name = request.data.get("name", account.name or "MT5 Account")
    account.broker = "mt5"
    account.connector = "mt5_local"
    account.mt5_login = str(request.data.get("mt5_login", account.mt5_login or ""))
    account.account_ref = account.mt5_login
    account.mt5_server = request.data.get("mt5_server", account.mt5_server or "")
    account.mt5_path = request.data.get("mt5_path", account.mt5_path or "")
    if request.data.get("password"):
        account.set_mt5_password(str(request.data["password"]))
    account.full_clean()
    account.save()
    return Response({"id": account.id, "name": account.name, "mt5_login": account.mt5_login, "mt5_server": account.mt5_server})


@api_view(["POST"])
@permission_classes([IsAuthenticated])
def personal_account_test(request):
    try:
        account = _account_for(request)
    except (BrokerAccount.DoesNotExist, ValueError) as exc:
        return Response({"detail": str(exc)}, status=400)
    if not account.get_mt5_password():
        return Response(
            {
                "detail": (
                    "The saved MT5 password cannot be decrypted. "
                    "Edit this account and enter the password again."
                )
            },
            status=status.HTTP_400_BAD_REQUEST,
        )
    queued_at = timezone.now()
    health = check_mt5_account_task.apply_async(args=[account.id], queue="mt5_execution")
    markets = refresh_mt5_markets_task.apply_async(args=[account.id], queue="mt5_execution")
    return Response(
        {
            "health_task_id": health.id,
            "markets_task_id": markets.id,
            "queued_at": queued_at,
        },
        status=status.HTTP_202_ACCEPTED,
    )
