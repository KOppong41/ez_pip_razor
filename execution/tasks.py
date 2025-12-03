import logging
import os
from celery import shared_task
from decimal import Decimal
from django.utils import timezone
from datetime import timedelta
from brokers.models import BrokerAccount
from execution.connectors.base import ConnectorError
from execution.connectors.mt5 import MT5Connector
from execution.services.orchestrator import update_order_status
from execution.services.portfolio import record_fill
from execution.services.monitor import (
    EarlyExitConfig,
    KillSwitchConfig,
    TrailingConfig,
    should_early_exit,
    apply_trailing,
    close_position_now,
    should_trigger_kill_switch,
    unrealized_pnl,
)
from execution.services.prices import get_price
from core.metrics import task_failures_total
from core.utils import audit_log
from execution.services.marketdata import get_candles_for_account
from execution.services.strategies.harami import detect_harami
from execution.services.brokers import dispatch_place_order
from execution.services.decision import make_decision_from_signal
from execution.services.fanout import fanout_orders
from execution.services.runtime_config import get_runtime_config
from execution.models import Order, Position, PnLDaily, Signal, Decision  
from execution.services.engine import run_engine_on_candles
from collections import defaultdict
from execution.services.psychology import bot_is_available_for_trading


HTF_MAP = {
    "5m": "30m",
    "15m": "1h",
    "30m": "4h",
    "1h": "4h",
}

def _get_htf(timeframe: str) -> str | None:
    return HTF_MAP.get(timeframe)


logger = logging.getLogger(__name__)


def _atr_like(candles, period: int = 14):
    """Simple ATR-like mean of high-low for sizing/filters."""
    from decimal import Decimal  # local to avoid circulars in tests

    if not candles or len(candles) < period:
        return Decimal("0")
    window = candles[-period:]
    total = sum((c["high"] - c["low"] for c in window), Decimal("0"))
    return total / Decimal(str(period))


@shared_task(
    bind=True,
    autoretry_for=(Exception,),
    retry_backoff=True,      # exponential: 1s, 2s, 4s, ...
    retry_backoff_max=300,   # cap at 5 minutes
    retry_jitter=True,       # add randomness
    retry_kwargs={"max_retries": 5},
)
def simulate_fill_task(self, order_id: int):
    try:
        order = Order.objects.get(id=order_id)
        if order.status not in ("ack", "new"):
            return
        # Simple deterministic mock price: 1.1000 for buys, 1.1005 for sells
        price = Decimal("1.1000") if order.side == "buy" else Decimal("1.1005")
        # Transition to filled + create execution/position
        update_order_status(order, "filled", price=price)
        record_fill(order, order.qty, price)
        return {"status": "filled", "order_id": order.id, "price": str(price)}
    except Exception as e:
        task_failures_total.labels(task="simulate_fill_task").inc()
        raise
    

@shared_task(
    bind=True,
    autoretry_for=(Exception,),
    retry_backoff=True,      # exponential: 1s, 2s, 4s, ...
    retry_backoff_max=300,   # cap at 5 minutes
    retry_jitter=True,       # add randomness
    retry_kwargs={"max_retries": 5},
)
def monitor_positions_task(self):
    """
    Checks open positions and triggers early exits if unrealized loss exceeds threshold.
    """
    try:
        runtime_cfg = get_runtime_config()
        cfg = EarlyExitConfig(max_unrealized_pct=runtime_cfg.early_exit_max_unrealized_pct)
        for pos in Position.objects.filter(status="open").select_related("broker_account"):
            mkt = get_price(pos.symbol)
            if should_early_exit(pos, mkt, cfg):
                # create close signal+decision+order and send immediately
                close_position_now(pos)
        return {"status": "ok"}
    except Exception as e:
        task_failures_total.labels(task="monitor_positions_task").inc()
        raise


@shared_task(
    bind=True,
    autoretry_for=(Exception,),
    retry_backoff=True,      # exponential: 1s, 2s, 4s, ...
    retry_backoff_max=300,   # cap at 5 minutes
    retry_jitter=True,       # add randomness
    retry_kwargs={"max_retries": 5},
)
def trail_positions_task(self):
    """
    Applies a simple trailing stop to profitable positions.
    """
    try:
        runtime_cfg = get_runtime_config()
        tcfg = TrailingConfig(
            trigger=runtime_cfg.trailing_trigger,
            distance=runtime_cfg.trailing_distance,
        )
        moved_ids = []
        for pos in Position.objects.filter(status="open"):
            mkt = get_price(pos.symbol)
            if apply_trailing(pos, mkt, tcfg):
                pos.save(update_fields=["sl"])
                moved_ids.append(pos.id)
        return {"moved": moved_ids}
    except Exception as e:
        task_failures_total.labels(task="trail_positions_task").inc()
        raise


@shared_task(
    bind=True,
    autoretry_for=(Exception,),
    retry_backoff=True,      # exponential: 1s, 2s, 4s, ...
    retry_backoff_max=300,   # cap at 5 minutes
    retry_jitter=True,       # add randomness
    retry_kwargs={"max_retries": 5},
)
def reconcile_daily_task(self):
    """
    Rolls executions into PnLDaily per broker account/symbol for today.
    MVP: realized = sum(exec.qty * (sign * (exec.price - order.price))) won't be accurate without side accounting,
    so we store counts and totals as a stub and leave real reconciliation for later.
    """
    try:
        today = timezone.now().date()
        unrealized_totals = {}
        for pos in Position.objects.filter(status="open"):
            mkt = get_price(pos.symbol)
            # stub unrealized PnL: (mkt - 1.0) * qty
            unrealized = (mkt - Decimal("1.0")) * pos.qty
            key = (pos.broker_account_id, pos.symbol)
            unrealized_totals[key] = unrealized_totals.get(key, Decimal("0")) + unrealized

        for (acct_id, symbol), unrealized in unrealized_totals.items():
            pnl_daily, _ = PnLDaily.objects.get_or_create(
                broker_account_id=acct_id,
                symbol=symbol,
                date=today,
                defaults={"realized": Decimal("0"), "unrealized": Decimal("0")},
            )
            pnl_daily.unrealized = unrealized
            pnl_daily.save(update_fields=["unrealized"])
        return {"status": "ok"}
    except Exception as e:
        task_failures_total.labels(task="reconcile_daily_task").inc()
        raise


@shared_task(
    bind=True, autoretry_for=(Exception,),
    retry_backoff=True, retry_backoff_max=300, retry_jitter=True, retry_kwargs={"max_retries": 3},
)
def ingest_tradingview_email(self):
    """
    Poll IMAP inbox, parse TradingView alert emails (JSON in body),
    then reuse AlertWebhookSerializer + auto-trade flow via the same code path used by the webhook.
    """
    try:
        from execution.integrations.tradingview_email import fetch_emails_and_parse
        from execution.serializers import AlertWebhookSerializer
        from execution.services.decision import make_decision_from_signal
        from execution.services.fanout import fanout_orders
        from execution.services.brokers import dispatch_place_order
        from core.metrics import signals_ingested_total

        alerts = fetch_emails_and_parse()
        created_count = 0
        sent_count = 0

        for payload in alerts:
            # validate & create/update Signal
            ser = AlertWebhookSerializer(data=payload)
            if not ser.is_valid():
                audit_log("signal.ingest.error", "EmailAlert", "-", {"errors": ser.errors})
                continue
            signal, created = ser.save()
            signals_ingested_total.labels(signal.source, signal.symbol, signal.timeframe).inc()
            audit_log("signal.ingest", "Signal", signal.id, {"via": "email"})

            if created:
                created_count += 1

            # AUTO-TRADE (identical to webhook's guard)
            if signal.bot and bot_is_available_for_trading(signal.bot) and getattr(signal.bot, "auto_trade", False):
                decision = make_decision_from_signal(signal)
                if decision.action == "open":
                    orders = fanout_orders(decision, master_qty=None)  # default bot qty
                    for order, _c in orders:
                        try:
                            dispatch_place_order(order)
                            sent_count += 1
                            audit_log("order.send", "Order", order.id, {"via": "email"})
                        except Exception as e:
                            audit_log("order.send.error", "Order", order.id, {"err": str(e)})

        return {"alerts": len(alerts), "signals_new": created_count, "orders_sent": sent_count}
    except Exception as e:
        task_failures_total.labels(task="ingest_tradingview_email").inc()
        raise
    
    
@shared_task(
    bind=True,
    autoretry_for=(Exception,),
    retry_backoff=True,
    retry_backoff_max=300,
    retry_jitter=True,
    retry_kwargs={"max_retries": 3},
)
def scan_harami_for_bot(self, bot_id: int, timeframe: str = "15m", n_bars: int = 200):
    """
    Internal engine prototype: fetch candles for a bot's symbol/timeframe,
    run the Harami strategy, and log the decision.

    ⚠️ IMPORTANT: This task does NOT create Signals, Decisions, or Orders yet.
    It is read-only + logging so it cannot affect live trading.
    """
    from bots.models import Bot  # local import to avoid any circulars

    try:
        bot = Bot.objects.select_related("broker_account").get(id=bot_id)
    except Bot.DoesNotExist:
        logger.warning(f"[HaramiScan] bot_id={bot_id} not found")
        return {"status": "error", "reason": "bot_not_found"}

    if getattr(bot, "status", None) != "active":
        logger.info(f"[HaramiScan] bot={bot.id} inactive, skipping")
        return {"status": "skipped", "reason": "bot_inactive"}

    broker_account = getattr(bot, "broker_account", None)
    if not broker_account or not getattr(broker_account, "is_active", False):
        logger.info(f"[HaramiScan] bot={bot.id} has no active broker account, skipping")
        return {"status": "skipped", "reason": "no_active_broker"}

    symbol = getattr(bot, "symbol", None)
    if not symbol:
        logger.info(f"[HaramiScan] bot={bot.id} has no symbol configured, skipping")
        return {"status": "skipped", "reason": "no_symbol"}

    try:
        tf = bot.default_timeframe or "5m"
        candles = get_candles_for_account(
            broker_account=broker_account,
            symbol=symbol,
            timeframe=tf,
            n_bars=200,
        )
        if not candles:
            logger.info(f"[HaramiScan] bot={bot.id} symbol={symbol} tf={timeframe} -> no candles")
            return {"status": "skipped", "reason": "no_candles"}

        decision = detect_harami(candles)

        logger.info(
            "[HaramiScan] bot=%s symbol=%s tf=%s action=%s direction=%s sl=%s tp=%s reason=%s",
            bot.id,
            symbol,
            timeframe,
            decision.action,
            decision.direction,
            decision.sl,
            decision.tp,
            decision.reason,
        )

        return {
            "status": "ok",
            "action": decision.action,
            "direction": decision.direction,
            "sl": str(decision.sl) if decision.sl is not None else None,
            "tp": str(decision.tp) if decision.tp is not None else None,
            "reason": decision.reason,
        }
    except Exception as e:
        task_failures_total.labels(task="scan_harami_for_bot").inc()
        logger.exception(f"[HaramiScan] bot={bot.id} symbol={symbol} tf={timeframe} failed: %s", e)
        raise


@shared_task(
    bind=True,
    autoretry_for=(ConnectorError,),  # only retry on broker connectivity issues
    retry_backoff=True,
    retry_backoff_max=300,
    retry_jitter=True,
    retry_kwargs={"max_retries": 3},
)
def trade_harami_for_bot(self, bot_id: int, timeframe: str = "15m", n_bars: int = 200):
    
    from execution.services.engine import run_engine, EngineContext
    """
    Internal engine task (v1):

    1) Fetch candles for the bot's symbol/timeframe (+ optional HTF).
    2) Run the internal engine (harami + engulfing + trend).
    3) If action='open', create a synthetic Signal (source='engine_v1').
    4) Run it through make_decision_from_signal -> Decision.
    5) Fanout to Orders and send them via dispatch_place_order.
    """
    from bots.models import Bot
    from django.utils import timezone

    try:
        bot = Bot.objects.select_related("broker_account").get(id=bot_id)
    except Bot.DoesNotExist:
        logger.warning("[EngineTrade] bot_id=%s not found", bot_id)
        return {"status": "error", "reason": "bot_not_found"}

    if not getattr(bot, "auto_trade", False):
        logger.info("[EngineTrade] bot=%s auto_trade=False, skipping", bot.id)
        return {"status": "skipped", "reason": "bot_auto_trade_disabled"}

    if not bot_is_available_for_trading(bot):
        logger.info("[EngineTrade] bot=%s unavailable (status/paused), skipping", bot.id)
        return {"status": "skipped", "reason": "bot_unavailable"}

    broker_account = getattr(bot, "broker_account", None)
    if not broker_account or not getattr(broker_account, "is_active", False):
        logger.info("[EngineTrade] bot=%s has no active broker account, skipping", bot.id)
        return {"status": "skipped", "reason": "no_active_broker"}

    symbol = getattr(bot.asset, "symbol", None)
    if not symbol:
        logger.info("[EngineTrade] bot=%s has no asset configured, skipping", bot.id)
        return {"status": "skipped", "reason": "no_symbol"}
    if not bot.accepts(symbol, timeframe):
        logger.info(
            "[EngineTrade] bot=%s does not accept symbol=%s tf=%s, skipping",
            bot.id,
            symbol,
            timeframe,
        )
        return {"status": "skipped", "reason": "bot_not_accept_symbol_timeframe"}

    # 1) Fetch candles (entry TF)
    try:
        entry_candles = get_candles_for_account(
            broker_account=broker_account,
            symbol=symbol,
            timeframe=timeframe,
            n_bars=n_bars,
        )
    except Exception as e:
        task_failures_total.labels(task="trade_harami_for_bot").inc()
        logger.exception(
            "[EngineTrade] bot=%s symbol=%s tf=%s marketdata failed: %s",
            bot.id,
            symbol,
            timeframe,
            e,
        )
        raise

    if not entry_candles:
        logger.info(
            "[EngineTrade] bot=%s symbol=%s tf=%s -> no candles",
            bot.id,
            symbol,
            timeframe,
        )
        return {"status": "skipped", "reason": "no_candles"}

    # Optional HTF candles
    htf = _get_htf(timeframe)
    htf_candles = None
    if htf:
        try:
            htf_candles = get_candles_for_account(
                broker_account=broker_account,
                symbol=symbol,
                timeframe=htf,
                n_bars=200,
            )
        except Exception as e:
            logger.exception(
                "[EngineTrade] bot=%s symbol=%s htf=%s marketdata failed (ignored): %s",
                bot.id,
                symbol,
                htf,
                e,
            )
            htf_candles = None

    # 2) Build engine context + run engine (requires explicit strategy opt-in)
    allowed = bot.enabled_strategies or []
    if not allowed:
        audit_log(
            "engine.decision",
            "EngineTrade",
            bot.id,
            {
                "symbol": symbol,
                "timeframe": timeframe,
                "action": "skipped",
                "reason": "no_enabled_strategies",
            },
        )
        return {"status": "skipped", "reason": "no_enabled_strategies"}

    ctx = EngineContext(
        symbol=symbol,
        timeframe=timeframe,
        entry_candles=entry_candles,
        htf_candles=htf_candles,
        allowed_strategies=allowed,
    )
    engine_decision = run_engine(ctx)
    # Trace every engine decision so we can see why trades are skipped.
    audit_log(
        "engine.decision",
        "EngineTrade",
        bot.id,
        {
            "symbol": symbol,
            "timeframe": timeframe,
            "action": engine_decision.action,
            "reason": engine_decision.reason,
            "score": float(engine_decision.score or 0.0),
            "strategy": engine_decision.strategy,
        },
    )

    logger.info(
        "[EngineTrade] bot=%s symbol=%s tf=%s action=%s dir=%s sl=%s tp=%s reason=%s strategy=%s",
        bot.id,
        symbol,
        timeframe,
        engine_decision.action,
        engine_decision.direction,
        engine_decision.sl,
        engine_decision.tp,
        engine_decision.reason,
        engine_decision.strategy,
    )

    if engine_decision.action != "open" or not engine_decision.direction:
        return {
            "status": "ok",
            "action": engine_decision.action,
            "reason": engine_decision.reason,
        }

    # 3) Create a synthetic Signal for this engine decision
    # deterministic dedupe per bot/symbol/timeframe/last bar to avoid duplicate signals
    dedupe_key = f"engine:{bot.id}:{symbol}:{timeframe}:{entry_candles[-1]['time'].isoformat()}"
    atr_val = _atr_like(entry_candles)

    # Use get_or_create for idempotency - prevents UNIQUE constraint violations on dedupe_key
    signal, signal_created = Signal.objects.get_or_create(
        dedupe_key=dedupe_key,
        defaults={
            "bot": bot,
            "source": "engine_v1",
            "symbol": symbol,
            "timeframe": timeframe,
            "direction": engine_decision.direction,
            "payload": {
                "engine": engine_decision.strategy,
                "sl": str(engine_decision.sl) if engine_decision.sl is not None else None,
                "tp": str(engine_decision.tp) if engine_decision.tp is not None else None,
                "reason": engine_decision.reason,
                "score": engine_decision.score,
                "generated_at": timezone.now().isoformat(),
                "atr": str(atr_val),
            },
        }
    )

    # 4) Run through existing decision + risk pipeline
    try:
        # Idempotency: if this signal already produced a decision, reuse it to avoid duplicate orders on retries.
        existing_decision = Decision.objects.filter(signal=signal, action="open").first()
        if existing_decision:
            decision = existing_decision
            logger.info("[EngineTrade] bot=%s signal=%s reusing existing decision=%s", bot.id, signal.id, decision.id)
        else:
            decision = make_decision_from_signal(signal)
    except Exception as e:
        task_failures_total.labels(task="trade_harami_for_bot").inc()
        logger.exception(
            "[EngineTrade] bot=%s signal=%s decision failed: %s",
            bot.id,
            signal.id,
            e,
        )
        raise

    if decision.action != "open":
        logger.info(
            "[EngineTrade] decision ignored: bot=%s signal=%s action=%s reason=%s",
            bot.id,
            signal.id,
            decision.action,
            decision.reason,
        )
        return {
            "status": "ok",
            "decision_action": decision.action,
            "decision_reason": decision.reason,
        }

    # 5) Fanout to orders and send
    orders_info = []
    dispatch_errors = []
    for order, created in fanout_orders(decision, master_qty=None):
        should_dispatch = created or order.status in ("new", "ack")
        if should_dispatch:
            try:
                dispatch_place_order(order)
            except Exception as e:
                dispatch_errors.append(e)
                audit_log(
                    "order.dispatch_error",
                    "Order",
                    order.id,
                    {
                        "bot_id": bot.id if bot else None,
                        "symbol": order.symbol,
                        "side": order.side,
                        "status": order.status,
                        "error": str(e),
                    },
                )
                logger.exception(f"[EngineTrade] Failed to dispatch order {order.id}: {e}")
                # Continue processing other orders
        orders_info.append(
            {
                "order_id": order.id,
                "created": created,
                "status": order.status,
                "symbol": order.symbol,
                "side": order.side,
            }
        )

    # If any broker dispatch failed, surface a ConnectorError to trigger retry/backoff.
    if dispatch_errors:
        raise ConnectorError(f"{len(dispatch_errors)} order dispatch failure(s); see logs for details")

    audit_log(
        "harami_trade_executed",
        "EngineTrade",
        bot.id,
        payload={
            "summary": f"placed {len(orders_info)} order(s) for bot={bot.id}, symbol={symbol}, tf={timeframe}",
            "bot_id": bot.id,
            "signal_id": signal.id,
            "decision_id": decision.id,
            "orders": orders_info,
        },
    )

    return {
        "status": "ok",
        "decision_id": decision.id,
        "orders": orders_info,
    }


@shared_task
def check_broker_health_task(symbol_hint: str = "EURUSDm"):
    """
    Connectivity check for active broker accounts (MT5).
    Attempts a login + symbol select + ready check and logs audit events.
    """
    connector = MT5Connector()
    checked = []
    for acct in BrokerAccount.objects.filter(is_active=True, broker__in=["mt5", "exness_mt5", "icmarket_mt5"]):
        creds = acct.get_creds()
        symbol = symbol_hint
        try:
            connector.check_health(creds, symbol)
            audit_log("broker.health", "BrokerAccount", acct.id, {"status": "ok", "symbol": symbol})
            checked.append(acct.id)
        except Exception as e:
            audit_log("broker.health.error", "BrokerAccount", acct.id, {"err": str(e), "symbol": symbol})
            checked.append(f"{acct.id}:error")
    return {"checked": checked}


@shared_task
def validate_broker_configs_task():
    """
    Basic validation of broker credentials to catch misconfig early.
    """
    issues = []
    for acct in BrokerAccount.objects.filter(is_active=True):
        errs = []
        creds = acct.get_creds()
        login = creds.get("login")
        path = creds.get("path")
        if acct.broker in ["mt5", "exness_mt5", "icmarket_mt5"]:
            if login is None or str(login).strip() == "" or not str(login).isdigit():
                errs.append("login_missing_or_invalid")
            if not path or not os.path.exists(path):
                errs.append("terminal_path_missing")
            missing = [k for k in ("password", "server", "path") if not creds.get(k)]
            if missing:
                errs.append(f"missing_fields:{','.join(missing)}")
        if errs:
            issues.append((acct.id, errs))
            audit_log("broker.config.error", "BrokerAccount", acct.id, {"errors": errs})
    return {"issues": issues}

    
    

@shared_task(
    bind=True,
    autoretry_for=(Exception,),
    retry_backoff=True,
    retry_backoff_max=300,
    retry_jitter=True,
    retry_kwargs={"max_retries": 3},
)
def run_harami_engine_for_all_bots(self, timeframe: str = "5m", n_bars: int = 200):
    """
    Periodic runner for the internal engine.

    - Picks bots with engine_mode="harami", auto_trade=True, status="active"
    - Uses bot.default_timeframe unless overridden by `timeframe`
    - Uses bot.asset (required) and respects Bot.accepts(...)
    """
    from bots.models import Bot  # local import to avoid circulars

    bots_qs = (
        Bot.objects.select_related("broker_account")
        .filter(
            auto_trade=True,
            status="active",
            engine_mode="harami",
        )
    )

    dispatched = 0
    skipped_no_broker = 0
    skipped_no_symbols = 0
    skipped_not_accepted = 0

    for bot in bots_qs:
        broker_account = getattr(bot, "broker_account", None)
        if not broker_account or not getattr(broker_account, "is_active", False):
            skipped_no_broker += 1
            continue

        symbol = getattr(bot.asset, "symbol", None)
        if not symbol:
            skipped_no_symbols += 1
            continue

        tf = bot.default_timeframe or timeframe

        if not bot.accepts(symbol, tf):
            skipped_not_accepted += 1
            continue

        trade_harami_for_bot.delay(bot.id, timeframe=tf, n_bars=n_bars)
        dispatched += 1

    logger.info(
        "[HaramiRunner] default_tf=%s dispatched=%s skipped_no_broker=%s skipped_no_symbols=%s skipped_not_accepted=%s",
        timeframe,
        dispatched,
        skipped_no_broker,
        skipped_no_symbols,
        skipped_not_accepted,
    )

    return {
        "status": "ok",
        "default_timeframe": timeframe,
        "dispatched": dispatched,
        "skipped_no_broker": skipped_no_broker,
        "skipped_no_symbols": skipped_no_symbols,
        "skipped_not_accepted": skipped_not_accepted,
    }




@shared_task(
    bind=True,
    autoretry_for=(Exception,),
    retry_backoff=True,
    retry_backoff_max=300,
    retry_jitter=True,
    retry_kwargs={"max_retries": 5},
)
def kill_switch_monitor_task(self):
    """
    Kill-switch monitor:

    - For each open Position:
      - Check if there is any Bot on that broker_account+symbol
        with kill_switch_enabled=True and auto_trade=True.
      - If so, evaluate kill-switch logic (risk-based for now).
      - If triggered, close the position immediately.

    This runs alongside monitor_positions_task / trail_positions_task.
    """
    from bots.models import Bot  # local import to avoid circulars

    cfg = KillSwitchConfig()
    closed = 0
    skipped_no_price = 0

    positions = Position.objects.filter(status="open").select_related("broker_account")

    for pos in positions:
        # Find a bot on this account that:
        # - is active + auto_trade
        # - has kill switch enabled
        # - accepts this symbol + its own default timeframe
        candidate_bots = Bot.objects.filter(
            broker_account=pos.broker_account,
            auto_trade=True,
            status="active",
            kill_switch_enabled=True,
        )

        bot = None
        for b in candidate_bots:
            tf = b.default_timeframe or "5m"
            if b.accepts(pos.symbol, tf):
                bot = b
                break

        if not bot:
            continue

        raw_pct = bot.kill_switch_max_unrealized_pct or Decimal("0.01")
        pct = Decimal(str(raw_pct))
        if pct > 1:
            pct = pct / Decimal("100")  # stored as percentage in DB

        cfg = KillSwitchConfig(max_unrealized_pct=pct)

        mkt = get_price(pos.symbol)
        if mkt is None:
            skipped_no_price += 1
            continue

        # --- engine-based prediction piece ---
        engine_opposite = False
        try:
            tf = bot.default_timeframe or "5m"
            candles = get_candles_for_account(
                broker_account=pos.broker_account,
                symbol=pos.symbol,
                timeframe=tf,
                n_bars=200,
            )
            if candles:
                engine_decision = run_engine_on_candles(candles)
                is_long = pos.qty > 0
                engine_opposite = (
                    engine_decision.action == "open"
                    and (
                        (is_long and engine_decision.direction == "sell")
                        or (not is_long and engine_decision.direction == "buy")
                    )
                )
        except Exception as e:
            logger.exception("[KillSwitch] engine check failed for pos=%s: %s", pos.id, e)
            engine_opposite = False
        # --- end engine piece ---

        trigger_risk = should_trigger_kill_switch(pos, mkt, cfg)
        loss = -unrealized_pnl(pos, mkt)
        trigger_engine = engine_opposite and loss > 0

        if trigger_risk or trigger_engine:
            try:
                order = close_position_now(pos)
                closed += 1
                audit_log(
                    "kill_switch.close",
                    "Position",
                    pos.id,
                    {
                        "order_id": order.id,
                        "symbol": pos.symbol,
                        "qty": str(pos.qty),
                        "avg_price": str(pos.avg_price),
                        "mkt": str(mkt),
                        "trigger_risk": trigger_risk,
                        "trigger_engine": trigger_engine,
                    },
                )
            except Exception as e:
                task_failures_total.labels(task="kill_switch_monitor_task").inc()
                logger.exception("[KillSwitch] failed to close pos=%s: %s", pos.id, e)

    logger.info(
        "[KillSwitch] closed=%s, skipped_no_price=%s",
        closed,
        skipped_no_price,
    )
    return {"closed": closed, "skipped_no_price": skipped_no_price}


@shared_task(
    bind=True,
    autoretry_for=(Exception,),
    retry_backoff=True,
    retry_backoff_max=300,
    retry_jitter=True,
    retry_kwargs={"max_retries": 3},
)
def cancel_stale_orders_task(self, max_age_seconds: int | None = None):
    """
    Auto-cancel orders stuck in new/ack longer than the configured threshold.
    """
    runtime_cfg = get_runtime_config()
    timeout = max_age_seconds or int(runtime_cfg.order_ack_timeout_seconds)
    cutoff = timezone.now() - timedelta(seconds=timeout)
    # Use updated_at to catch orders that were recently touched by broker transitions.
    stale_qs = Order.objects.filter(status__in=["new", "ack"], updated_at__lt=cutoff)

    canceled = []
    for order in stale_qs:
        try:
            update_order_status(order, "canceled", error_msg="auto-cancel: stale new/ack")
            canceled.append(order.id)
        except Exception as e:
            logger.exception("[StaleCancel] failed for order %s: %s", order.id, e)
            task_failures_total.labels(task="cancel_stale_orders_task").inc()

    if canceled:
        audit_log(
            "order.auto_cancel",
            "Order",
            "-",
            {"orders": canceled, "timeout_sec": timeout},
        )
    return {"canceled": canceled, "timeout_sec": timeout}


@shared_task(
    bind=True,
    autoretry_for=(Exception,),
    retry_backoff=True,
    retry_backoff_max=300,
    retry_jitter=True,
    retry_kwargs={"max_retries": 3},
)
def reconcile_broker_positions_task(self):
    """
    Detects broker positions that are not reflected in the DB and force-closes them by symbol.

    - If an MT5 account has positions for a symbol with no open DB Position rows,
      we create a close|reconcile order per symbol and dispatch it (MT5 close-by-ticket logic).
    - If DB shows open positions but broker has none, we only log (no action).
    """
    from django.utils import timezone
    from datetime import timedelta

    # Grace window: avoid flattening if a recent order exists for the symbol/account.
    recent_cutoff = timezone.now() - timedelta(minutes=5)

    connector = MT5Connector()
    flattened = []
    skipped = []
    errors = []
    skipped_recent = []

    accounts = BrokerAccount.objects.filter(is_active=True, broker__in=["mt5", "exness_mt5", "icmarket_mt5"])
    for acct in accounts:
        try:
            connector.login_for_account(acct)
            import MetaTrader5 as mt5  # type: ignore
            mt5_positions = mt5.positions_get()
        except Exception as e:
            errors.append((acct.id, str(e)))
            logger.exception("[Recon] login failed for acct=%s: %s", acct.id, e)
            continue

        db_positions = Position.objects.filter(broker_account=acct, status="open")
        db_by_symbol = {p.symbol for p in db_positions}

        mt5_by_symbol = defaultdict(list)
        for pos in mt5_positions or []:
            sym = getattr(pos, "symbol", None)
            if sym:
                mt5_by_symbol[sym].append(pos)

        # Symbols present on broker but not in DB: force close
        for sym, pos_list in mt5_by_symbol.items():
            if sym in db_by_symbol:
                continue

            # If a recent order exists for this symbol/account, skip flattening this cycle.
            if Order.objects.filter(
                broker_account=acct,
                symbol=sym,
                created_at__gte=recent_cutoff,
            ).exists():
                skipped_recent.append(sym)
                continue
            try:
                # Create/reuse a reconcile close order; quantity/side are ignored by MT5 close-by-ticket path.
                order, _ = Order.objects.get_or_create(
                    client_order_id=f"close|reconcile|{acct.id}|{sym}",
                    defaults={
                        "bot": acct.bots.first(),
                        "broker_account": acct,
                        "symbol": sym,
                        "side": "sell",
                        "qty": Decimal("0"),
                        "status": "new",
                    },
                )
                dispatch_place_order(order)
                flattened.append(sym)
            except Exception as e:
                errors.append((acct.id, sym, str(e)))
                logger.exception("[Recon] failed to close stray positions acct=%s sym=%s: %s", acct.id, sym, e)

        # Symbols present in DB but missing on broker: log only
        for sym in db_by_symbol:
            if sym not in mt5_by_symbol:
                skipped.append(sym)

    if flattened or errors or skipped_recent:
        audit_log(
            "broker.reconcile",
            "BrokerAccount",
            "-",
            {
                "flattened": flattened,
                "errors": errors,
                "skipped_db_only": skipped,
                "skipped_recent": skipped_recent,
            },
        )
    return {
        "flattened": flattened,
        "errors": errors,
        "skipped_db_only": skipped,
        "skipped_recent": skipped_recent,
    }
