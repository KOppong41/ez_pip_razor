from __future__ import annotations

from celery import shared_task
from decimal import Decimal
from datetime import datetime, timezone as dt_timezone
from django.utils import timezone

from execution.connectors.mt5 import MT5Connector
from brokers.models import BrokerAccount
from execution.models import (
    AccountSnapshot,
    BrokerPosition,
    BrokerSymbolMapping,
    MT5ConnectionState,
    Order,
    RiskPolicy,
)
from execution.services.brokers import dispatch_place_order
from execution.utils.symbols import canonical_symbol
from bots.models import Asset
from execution.services.equity import update_equity_high_water


@shared_task(
    bind=True,
    queue="mt5_execution",
    autoretry_for=(Exception,),
    retry_backoff=True,
    retry_kwargs={"max_retries": 3},
)
def execute_mt5_order_task(self, order_id: int):
    order = Order.objects.select_related("broker_account", "bot").get(pk=order_id)
    dispatch_place_order(order)
    order.refresh_from_db()
    return {"order_id": order.id, "status": order.status}


@shared_task(
    bind=True,
    queue="mt5_execution",
    autoretry_for=(Exception,),
    retry_backoff=True,
    retry_kwargs={"max_retries": 3},
)
def modify_mt5_position_task(self, broker_position_id: int, *, sl=None, tp=None):
    position = BrokerPosition.objects.select_related("broker_account").get(pk=broker_position_id)
    MT5Connector().modify_broker_position(position, sl=sl, tp=tp)
    return {
        "broker_position_id": position.id,
        "broker_position_ticket": position.broker_position_ticket,
        "sl": str(position.sl) if position.sl is not None else None,
        "tp": str(position.tp) if position.tp is not None else None,
    }


@shared_task(bind=True, queue="mt5_execution")
def check_mt5_account_task(self, broker_account_id: int):
    account = BrokerAccount.objects.get(pk=broker_account_id, connector="mt5_local")
    connector = MT5Connector()
    try:
        info = connector.account_info_for_account(account)
        trade_mode = getattr(info, "trade_mode", None)
        mode = {0: "demo", 1: "contest", 2: "live"}.get(trade_mode, "unknown")
        MT5ConnectionState.objects.update_or_create(
            broker_account=account,
            defaults={
                "connected": True,
                "active_login": str(getattr(info, "login", "") or ""),
                "active_server": str(getattr(info, "server", "") or ""),
                "company": str(getattr(info, "company", "") or ""),
                "currency": str(getattr(info, "currency", "") or ""),
                "leverage": int(getattr(info, "leverage", 0) or 0),
                "account_mode": mode,
                "last_error": "",
            },
        )
        snapshot = AccountSnapshot.objects.create(
            broker_account=account,
            balance=Decimal(str(getattr(info, "balance", 0) or 0)),
            equity=Decimal(str(getattr(info, "equity", 0) or 0)),
            margin=Decimal(str(getattr(info, "margin", 0) or 0)),
            free_margin=Decimal(str(getattr(info, "margin_free", 0) or 0)),
            margin_level=Decimal(str(getattr(info, "margin_level", 0) or 0)),
            currency=str(getattr(info, "currency", "") or ""),
        )
        policy, _ = RiskPolicy.objects.get_or_create(broker_account=account)
        update_equity_high_water(
            policy,
            snapshot.equity,
            observed_at=snapshot.captured_at,
        )
        if not account.is_verified:
            account.is_verified = True
            account.save(update_fields=["is_verified"])
        return {"connected": True, "account_mode": mode}
    except Exception as exc:
        MT5ConnectionState.objects.update_or_create(
            broker_account=account,
            defaults={"connected": False, "last_error": str(exc)[:255]},
        )
        if account.is_verified:
            account.is_verified = False
            account.save(update_fields=["is_verified"])
        return {"connected": False, "error": str(exc)}


@shared_task(bind=True, queue="mt5_execution")
def refresh_mt5_markets_task(self, broker_account_id: int):
    account = BrokerAccount.objects.get(pk=broker_account_id, connector="mt5_local")
    connector = MT5Connector()
    default_enabled = {"EURUSD", "USDJPY"}
    canonical_assets = {
        canonical_symbol(symbol)
        for symbol in Asset.objects.filter(is_active=True).values_list(
            "symbol", flat=True
        )
        if canonical_symbol(symbol)
    }
    refreshed = []
    for canonical in sorted(canonical_assets):
        mapping, _ = BrokerSymbolMapping.objects.get_or_create(
            broker_account=account,
            canonical_symbol=canonical,
            defaults={"enabled": canonical in default_enabled},
        )
        try:
            resolved = connector.resolve_symbol_for_account(account, canonical)
            info = connector.symbol_info_for_account(account, resolved)
            tick = connector.tick_for_account(account, resolved)
            bid = Decimal(str(getattr(tick, "bid", 0) or 0))
            ask = Decimal(str(getattr(tick, "ask", 0) or 0))
            tick_at = None
            if getattr(tick, "time", None):
                tick_at = datetime.fromtimestamp(float(tick.time), tz=dt_timezone.utc)
            mapping.broker_symbol = resolved
            mapping.bid = bid
            mapping.ask = ask
            mapping.spread = ask - bid
            mapping.trading_status = "open" if getattr(info, "trade_mode", 0) not in {0, 1} else "closed"
            mapping.last_tick_at = tick_at
            mapping.last_resolved_at = timezone.now()
            mapping.last_error = ""
            mapping.save()
            refreshed.append(canonical)
        except Exception as exc:
            mapping.trading_status = "unavailable"
            mapping.last_error = str(exc)[:255]
            mapping.save(update_fields=["trading_status", "last_error"])
    return {"refreshed": refreshed}


@shared_task(bind=True, queue="mt5_execution")
def test_mt5_account_task(self, broker_account_id: int):
    """Test one account, then refresh markets only after a valid connection."""
    health = check_mt5_account_task.run(broker_account_id)
    if not health.get("connected"):
        return {"health": health, "markets": None}
    markets = refresh_mt5_markets_task.run(broker_account_id)
    return {"health": health, "markets": markets}


@shared_task(bind=True, queue="mt5_execution")
def reconcile_mt5_order_task(self, order_id: int):
    order = Order.objects.select_related("broker_account", "bot").get(pk=order_id)
    found = MT5Connector().reconcile_order(order)
    order.refresh_from_db()
    return {"order_id": order.id, "found": found, "status": order.status}
