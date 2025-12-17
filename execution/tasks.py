import logging
import os
from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone as dt_timezone
from decimal import Decimal
from typing import Callable

from celery import shared_task
from django.utils import timezone

from brokers.models import BrokerAccount
from bots.models import STRATEGY_CHOICES
from core.metrics import task_failures_total
from execution.connectors.base import ConnectorError
from execution.connectors.mt5 import MT5Connector, is_mt5_available, mt5
from execution.models import Decision, Order, PnLDaily, Position, ScalperRunLog, Signal
from execution.services.brokers import dispatch_place_order, get_broker_symbol_constraints
from execution.services.decision import make_decision_from_signal
from execution.services.ai_strategy_selector import select_ai_strategies
from execution.services.engine import run_engine_on_candles
from execution.services.fanout import fanout_orders
from execution.services.marketdata import get_candles_for_account, _login_mt5_for_account
from execution.services.monitor import (
    EarlyExitConfig,
    KillSwitchConfig,
    TrailingConfig,
    should_early_exit,
    apply_trailing,
    close_position_now,
    should_trigger_kill_switch,
    unrealized_pnl,
    manage_scalper_position,
)
from execution.services.prices import get_price
from execution.services.psychology import bot_is_available_for_trading
from execution.services.market_hours import (
    get_market_status_for_bot,
    maybe_pause_bot_for_market,
    maybe_unpause_crypto_for_open_market,
    is_crypto_symbol,
)
from execution.tasks_market_guard import apply_market_guard
from execution.services.scalper_config import build_scalper_config
from execution.services.runtime_config import get_runtime_config
from execution.services.journal import log_journal_event
from execution.services.orchestrator import update_order_status
from execution.services.strategies.breakout_retest import (
    BreakoutRetestConfig,
    run_breakout_retest,
)
from execution.services.strategies.doji_breakout import DojiBreakoutConfig, run_doji_breakout
from execution.services.strategies.harami import detect_harami
from execution.services.strategies.momentum_ignition import (
    MomentumIgnitionConfig,
    run_momentum_ignition,
)
from execution.services.strategies.price_action_pinbar import PinBarConfig, run_price_action_pinbar
from execution.services.strategies.range_reversion import RangeReversionConfig, run_range_reversion
from execution.services.strategies.trend_pullback import TrendPullbackConfig, run_trend_pullback
from execution.utils.symbols import canonical_symbol


HTF_MAP = {
    "5m": "30m",
    "15m": "1h",
    "30m": "4h",
    "1h": "4h",
}

def _get_htf(timeframe: str) -> str | None:
    return HTF_MAP.get(timeframe)


logger = logging.getLogger(__name__)

def _json_safe(val):
    """
    Recursively convert values into JSON-serializable forms (e.g., datetime -> iso string,
    Decimal -> string).
    """
    if isinstance(val, (list, tuple)):
        return [_json_safe(v) for v in val]
    if isinstance(val, dict):
        return {k: _json_safe(v) for k, v in val.items()}
    if isinstance(val, datetime):
        return val.isoformat()
    if isinstance(val, Decimal):
        return str(val)
    return val

SESSION_WINDOWS = (
    ("asia", 0, 6),
    ("london", 6, 12),
    ("new_york", 12, 20),
)


def _session_label(moment=None) -> str:
    moment = moment or timezone.now()
    hour = moment.hour
    for label, start, end in SESSION_WINDOWS:
        if start <= hour < end:
            return label
    return "overnight"


def _analyze_htf_bias(candles) -> dict | None:
    if not candles or len(candles) < 30:
        return None

    closes = [c["close"] for c in candles]
    k = Decimal("2") / Decimal("21")
    ema = closes[0]
    ema_values = [ema]
    for c in closes[1:]:
        ema = c * k + ema * (Decimal("1") - k)
        ema_values.append(ema)

    ema_now = ema_values[-1]
    lookback = min(5, len(ema_values) - 1)
    ema_prev = ema_values[-lookback]
    last_close = closes[-1] or Decimal("0")
    slope = (ema_now - ema_prev)
    slope_pct = (slope / last_close) if last_close else Decimal("0")

    atr_points = _atr_like(candles, period=14)
    atr_prev = _atr_like(candles[:-5], period=14) if len(candles) > 35 else atr_points
    atr_ratio = (atr_points / atr_prev) if atr_prev else Decimal("1")

    range_window = candles[-30:]
    highs = [c["high"] for c in range_window]
    lows = [c["low"] for c in range_window]
    range_high = max(highs)
    range_low = min(lows)
    denom = range_high - range_low
    position = ((last_close - range_low) / denom) if denom else Decimal("0.5")

    structure = "range"
    if highs[-1] >= range_high and lows[-1] >= lows[-2]:
        structure = "higher_high"
    elif lows[-1] <= range_low and highs[-1] <= highs[-2]:
        structure = "lower_low"

    bias = None
    slope_threshold = Decimal("0.00008")
    if slope_pct > slope_threshold and position > Decimal("0.55"):
        bias = "buy"
    elif slope_pct < -slope_threshold and position < Decimal("0.45"):
        bias = "sell"

    return {
        "bias": bias,
        "ema_slope_pct": float(slope_pct),
        "atr_points": float(atr_points),
        "atr_ratio": float(atr_ratio),
        "range_high": str(range_high),
        "range_low": str(range_low),
        "position_in_range": float(position),
        "structure": structure,
    }


def _compute_bias_from_htf(candles) -> str | None:
    info = _analyze_htf_bias(candles)
    return info.get("bias") if info else None


@dataclass(frozen=True)
class ScalperStrategyEntry:
    runner: Callable
    config_factory: Callable[[], object]
    requires_symbol: bool = False


SCALPER_STRATEGY_REGISTRY: dict[str, ScalperStrategyEntry] = {
    "price_action_pinbar": ScalperStrategyEntry(
        runner=run_price_action_pinbar,
        config_factory=PinBarConfig,
        requires_symbol=True,
    ),
    "trend_pullback": ScalperStrategyEntry(
        runner=run_trend_pullback,
        config_factory=TrendPullbackConfig,
    ),
    "doji_breakout": ScalperStrategyEntry(
        runner=run_doji_breakout,
        config_factory=DojiBreakoutConfig,
        requires_symbol=True,
    ),
    "range_reversion": ScalperStrategyEntry(
        runner=run_range_reversion,
        config_factory=RangeReversionConfig,
    ),
    "breakout_retest": ScalperStrategyEntry(
        runner=run_breakout_retest,
        config_factory=BreakoutRetestConfig,
    ),
    "momentum_ignition": ScalperStrategyEntry(
        runner=run_momentum_ignition,
        config_factory=MomentumIgnitionConfig,
    ),
}


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
        record_fill(order, order.qty, price, contract_size=Decimal("1"))
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
            if manage_scalper_position(pos, mkt):
                moved_ids.append(pos.id)
                continue
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
                log_journal_event(
                    "signal.ingest.error",
                    severity="warning",
                    message="TradingView email alert validation failed",
                    context={"errors": ser.errors, "payload": payload},
                )
                continue
            signal, created = ser.save()
            signals_ingested_total.labels(signal.source, signal.symbol, signal.timeframe).inc()
            log_journal_event(
                "signal.ingest",
                signal=signal,
                bot=signal.bot,
                owner=getattr(signal, "owner", None),
                symbol=signal.symbol,
                message=f"{signal.symbol} {signal.direction} via email",
                context={"via": "email", "timeframe": signal.timeframe},
            )

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
                        except Exception as e:
                            log_journal_event(
                                "order.dispatch_error",
                                severity="error",
                                order=order,
                                bot=order.bot,
                                broker_account=order.broker_account,
                                symbol=order.symbol,
                                message="Email auto-trade dispatch failed",
                                context={"error": str(e)},
                            )

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

    market_status = get_market_status_for_bot(bot, use_mt5_probe=True)
    if market_status and not market_status.is_open:
        maybe_pause_bot_for_market(bot, market_status)
        logger.info("[EngineTrade] bot=%s symbol=%s skipped: market_closed (%s)", bot.id, getattr(bot.asset, "symbol", None), market_status.reason)
        return {"status": "skipped", "reason": f"market_closed:{market_status.reason}"}
    if market_status and market_status.is_open:
        maybe_unpause_crypto_for_open_market(bot, market_status)

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
    canonical_sym = canonical_symbol(symbol)
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

    htf_bias = _compute_bias_from_htf(htf_candles) if htf_candles else None

    # 2) Build engine context + run engine (auto-trade mode uses asset/profile presets)
    if getattr(bot, "auto_trade", False):
        last_entry = entry_candles[-1]
        atr_points = _atr_like(entry_candles, period=14)
        allowed = select_ai_strategies(
            engine_mode="harami",
            available=STRATEGY_CHOICES,
            symbol=canonical_sym,
            context={
                "atr_points": atr_points,
                "bar_range": last_entry["high"] - last_entry["low"],
                "last_close": last_entry.get("close"),
                "htf_bias": htf_bias,
            },
        )
    else:
        allowed = bot.enabled_strategies or []
    if not allowed:
        log_journal_event(
            "engine.decision",
            bot=bot,
            owner=getattr(bot, "owner", None),
            symbol=symbol,
            message=f"{symbol} {timeframe} skipped (no enabled strategies)",
            context={"action": "skipped", "reason": "no_enabled_strategies"},
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
    log_journal_event(
        "engine.decision",
        bot=bot,
        owner=getattr(bot, "owner", None),
        symbol=symbol,
        message=f"{symbol} {timeframe} action={engine_decision.action}",
        context={
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
                log_journal_event(
                    "order.dispatch_error",
                    severity="error",
                    order=order,
                    bot=order.bot,
                    broker_account=order.broker_account,
                    symbol=order.symbol,
                    message="Engine dispatch failed",
                    context={
                        "bot_id": bot.id if bot else None,
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

    log_journal_event(
        "harami_trade_executed",
        bot=bot,
        owner=getattr(bot, "owner", None),
        symbol=symbol,
        message=f"Placed {len(orders_info)} order(s) for {symbol} {timeframe}",
        context={
            "bot_id": bot.id if bot else None,
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
            log_journal_event(
                "broker.health",
                broker_account=acct,
                owner=acct.owner,
                message=f"Broker {acct.id} healthy",
                symbol=symbol,
                context={"status": "ok"},
            )
            checked.append(acct.id)
        except Exception as e:
            log_journal_event(
                "broker.health.error",
                severity="warning",
                broker_account=acct,
                owner=acct.owner,
                symbol=symbol,
                message="Broker health check failed",
                context={"error": str(e)},
            )
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
            log_journal_event(
                "broker.config.error",
                severity="warning",
                broker_account=acct,
                owner=acct.owner,
                message="Broker configuration invalid",
                context={"errors": errs},
            )
    return {"issues": issues}

    
    

@shared_task(
    bind=True,
    autoretry_for=(Exception,),
    retry_backoff=True,
    retry_backoff_max=300,
    retry_jitter=True,
    retry_kwargs={"max_retries": 3},
)
def run_scalper_engine_for_all_bots(self, timeframe: str = "1m", n_bars: int = 100):
    """
    High-frequency scalper signal generator.
    
    - Picks bots with engine_mode="scalper", auto_trade=True, status="active"
    - For each bot, scans M1 candles and runs bot.enabled_strategies (price_action_pinbar, trend_pullback, etc.)
    - Emits Signal objects for each strategy match
    - Routes through existing decision + execution pipeline
    
    This is the real "scalper brain" that powers high-frequency trading on XAUUSDm and other liquid assets.
    """
    from bots.models import Bot
    
    bots_qs = (
        Bot.objects.select_related("broker_account", "asset")
        .filter(
            auto_trade=True,
            status="active",
            engine_mode="scalper",
        )
    )
    
    dispatched = 0
    skipped_no_broker = 0
    skipped_no_symbols = 0
    skipped_no_strategies = 0
    skipped_not_accepted = 0
    skipped_market_closed = 0
    skipped_unavailable = 0

    for bot in bots_qs:
        broker_account = getattr(bot, "broker_account", None)
        if not broker_account or not getattr(broker_account, "is_active", False):
            skipped_no_broker += 1
            continue

        symbol = getattr(bot.asset, "symbol", None)
        if not symbol:
            skipped_no_symbols += 1
            continue

        market_status = get_market_status_for_bot(bot, use_mt5_probe=False)
        if market_status and not market_status.is_open:
            maybe_pause_bot_for_market(bot, market_status)
            skipped_market_closed += 1
            continue
        if market_status and market_status.is_open:
            maybe_unpause_crypto_for_open_market(bot, market_status)

        if not bot_is_available_for_trading(bot):
            skipped_unavailable += 1
            continue

        # Must have manual strategies when auto-trade is disabled
        enabled_strats = bot.enabled_strategies or []
        if not getattr(bot, "auto_trade", True) and not enabled_strats:
            skipped_no_strategies += 1
            continue
        
        # Prefer bot-specific default timeframe when provided, otherwise fall back to the global default.
        tf = (bot.default_timeframe or timeframe or "1m").lower()
        fallback_tf = (timeframe or "1m").lower()

        if not bot.accepts(symbol, tf):
            # If the bot default is not accepted, attempt the fallback; otherwise skip.
            if tf != fallback_tf and bot.accepts(symbol, fallback_tf):
                tf = fallback_tf
            else:
                skipped_not_accepted += 1
                continue
        
        # Run inline to guarantee scalper cycles execute even if a nested Celery worker is unavailable.
        trade_scalper_strategies_for_bot.apply(
            args=(bot.id,),
            kwargs={"timeframe": tf, "n_bars": n_bars},
        )
        dispatched += 1
    
    logger.info(
        "[ScalperRunner] tf=%s dispatched=%s skipped_no_broker=%s skipped_no_symbols=%s skipped_no_strategies=%s skipped_not_accepted=%s skipped_market_closed=%s skipped_unavailable=%s",
        timeframe,
        dispatched,
        skipped_no_broker,
        skipped_no_symbols,
        skipped_no_strategies,
        skipped_not_accepted,
        skipped_market_closed,
        skipped_unavailable,
    )

    return {
        "status": "ok",
        "timeframe": timeframe,
        "dispatched": dispatched,
        "skipped_no_broker": skipped_no_broker,
        "skipped_no_symbols": skipped_no_symbols,
        "skipped_no_strategies": skipped_no_strategies,
        "skipped_not_accepted": skipped_not_accepted,
        "skipped_market_closed": skipped_market_closed,
        "skipped_unavailable": skipped_unavailable,
    }


@shared_task(
    bind=True,
    autoretry_for=(ConnectorError,),
    retry_backoff=True,
    retry_backoff_max=300,
    retry_jitter=True,
    retry_kwargs={"max_retries": 5},
)
def trade_scalper_strategies_for_bot(self, bot_id: int, timeframe: str = "1m", n_bars: int = 100):
    """
    Runs all enabled scalper strategies for a single bot.

    - Fetches M1 candles
    - Runs price_action_pinbar, trend_pullback, etc. (whatever is enabled for the bot)
    - Creates Signal + Decision + Order for each match
    - Emits to broker if decision passes risk checks
    """
    from bots.models import Bot
    bot = Bot.objects.select_related("broker_account", "asset").get(id=bot_id)

    if not getattr(bot, "auto_trade", False):
        logger.info("[ScalperTrade] bot=%s auto_trade=False, skipping", bot.id)
        return {"status": "skipped", "reason": "bot_auto_trade_disabled"}

    market_status = get_market_status_for_bot(bot, use_mt5_probe=True)
    if market_status and not market_status.is_open:
        maybe_pause_bot_for_market(bot, market_status)
        logger.info(
            "[ScalperTrade] bot=%s symbol=%s skipped: market_closed (%s)",
            bot.id,
            getattr(bot.asset, "symbol", None),
            market_status.reason,
        )
        return {"status": "skipped", "reason": f"market_closed:{market_status.reason}"}
    if market_status and market_status.is_open:
        maybe_unpause_crypto_for_open_market(bot, market_status)

    if not bot_is_available_for_trading(bot):
        logger.info("[ScalperTrade] bot=%s unavailable (status/paused/allocation), skipping", bot.id)
        return {"status": "skipped", "reason": "bot_unavailable"}

    broker_account = getattr(bot, "broker_account", None)
    if not broker_account or not getattr(broker_account, "is_active", False):
        logger.info("[ScalperTrade] bot=%s has no active broker account, skipping", bot.id)
        return {"status": "skipped", "reason": "no_active_broker"}

    if not getattr(bot, "asset", None) or not getattr(bot.asset, "symbol", None):
        logger.info("[ScalperTrade] bot=%s has no asset configured, skipping", bot.id)
        return {"status": "skipped", "reason": "no_symbol"}

    broker_constraints = get_broker_symbol_constraints(broker_account, getattr(bot.asset, "symbol", None))
    broker_min_stop_points = broker_constraints.stops_level_points or Decimal("0")
    broker_point = broker_constraints.point
    broker_lot_step = broker_constraints.lot_step

    scalper_cfg = build_scalper_config(bot)
    scalper_params = bot.scalper_params or {}
    strategy_profile_key = (
        scalper_params.get("strategy_profile") or scalper_cfg.default_strategy_profile
    )
    profile_aliases = {
        "xauusd_standard": "core_standard",
        "xauusd_aggressive": "core_aggressive",
    }
    original_profile_key = strategy_profile_key
    strategy_profile_key = profile_aliases.get(strategy_profile_key, strategy_profile_key)
    if strategy_profile_key != original_profile_key:
        scalper_params["strategy_profile"] = strategy_profile_key
        bot.scalper_params = scalper_params
        bot.save(update_fields=["scalper_params"])
    asset_profile_key = None
    if getattr(bot, "asset", None) and getattr(bot.asset, "symbol", None):
        asset_canon = canonical_symbol(bot.asset.symbol)
        for key, profile in (scalper_cfg.strategy_profiles or {}).items():
            if profile.symbol and canonical_symbol(profile.symbol) == asset_canon:
                asset_profile_key = key
                break
    if getattr(bot, "auto_trade", False) and asset_profile_key:
        strategy_profile_key = asset_profile_key
    strategy_profile = scalper_cfg.strategy_profiles.get(strategy_profile_key)

    symbol = bot.asset.symbol
    canonical_sym = canonical_symbol(symbol)
    auto_mode = bool(getattr(bot, "auto_trade", False))
    manual_strats = list(bot.enabled_strategies or [])
    profile_strats = (
        list(strategy_profile.enabled_strategies)
        if strategy_profile and strategy_profile.enabled_strategies
        else []
    )
    enabled_strats: list[str] = []
    strategy_context: dict[str, object] = {
        "symbol": canonical_sym,
        "mode": "auto_profile" if auto_mode else "manual",
        "profile_key": strategy_profile_key,
        "profile_symbol": getattr(strategy_profile, "symbol", None) if strategy_profile else None,
        "manual_configured": bool(manual_strats),
    }
    if not auto_mode:
        enabled_strats = manual_strats
        if not enabled_strats:
            return {"status": "ok", "reason": "no_enabled_strategies"}
    
    session_label = _session_label()
    tick_snapshot = None
    spread_points = None
    # Get M1 candles
    try:
        # Skip market-closed checks for crypto (24/7); otherwise enforce tradable state.
        tick = None
        if not is_crypto_symbol(symbol):
            try:
                from execution.connectors.mt5 import is_mt5_available, mt5
                if is_mt5_available():
                    info = mt5.symbol_info(symbol)
                    if info is None:
                        try:
                            _login_mt5_for_account(broker_account)
                            info = mt5.symbol_info(symbol)
                        except Exception:
                            info = None
                    if info is None or not info.visible:
                        logger.info("[ScalperTrade] bot=%s symbol=%s skipped: market_closed_or_symbol_not_visible", bot.id, symbol)
                        return {"status": "ok", "reason": "market_closed"}

                    disabled_modes = {mt5.SYMBOL_TRADE_MODE_DISABLED}
                    close_only = getattr(mt5, "SYMBOL_TRADE_MODE_CLOSEONLY", None)
                    if close_only is not None:
                        disabled_modes.add(close_only)
                    if info.trade_mode in disabled_modes:
                        logger.info("[ScalperTrade] bot=%s symbol=%s skipped: market_closed_trade_mode=%s", bot.id, symbol, info.trade_mode)
                        return {"status": "ok", "reason": "market_closed"}

                    try:
                        tick = mt5.symbol_info_tick(symbol)
                    except Exception:
                        tick = None

                    if not tick:
                        logger.warning("[ScalperTrade] bot=%s symbol=%s no_tick_data; continuing with candle snapshot only", bot.id, symbol)
                    else:
                        tick_time = getattr(tick, "time", None)
                        tick_seconds = tick_time
                        bid = getattr(tick, "bid", None)
                        ask = getattr(tick, "ask", None)
                        if bid is not None and ask is not None:
                            try:
                                spread_points = Decimal(str(ask - bid))
                            except Exception:
                                spread_points = None
                        tick_snapshot = {
                            "bid": float(bid) if bid is not None else None,
                            "ask": float(ask) if ask is not None else None,
                            "last": float(getattr(tick, "last", 0.0)),
                            "time": tick_time.isoformat() if isinstance(tick_time, datetime) else tick_time,
                        }
                        if not tick_seconds:
                            tick_millis = getattr(tick, "time_msc", None)
                            if tick_millis:
                                tick_seconds = tick_millis / 1000

                        if isinstance(tick_seconds, datetime):
                            tick_seconds = tick_seconds.timestamp()

                        if tick_seconds:
                            tick_dt = datetime.fromtimestamp(float(tick_seconds), tz=dt_timezone.utc)
                            seconds_since_tick = (timezone.now() - tick_dt).total_seconds()
                            if seconds_since_tick > 600:
                                logger.warning(
                                    "[ScalperTrade] bot=%s symbol=%s stale_tick last_tick=%s age_sec=%s; continuing",
                                    bot.id,
                                    symbol,
                                    tick_dt.isoformat(),
                                    int(seconds_since_tick),
                                )
            except Exception:
                pass

        entry_candles = get_candles_for_account(
            broker_account=broker_account,
            symbol=symbol,
            timeframe=timeframe,
            n_bars=n_bars,
        )
    except Exception as e:
        task_failures_total.labels(task="trade_scalper_strategies_for_bot").inc()
        logger.exception(
            "[ScalperTrade] bot=%s symbol=%s tf=%s failed to fetch candles: %s",
            bot.id,
            symbol,
            timeframe,
            e,
        )
        raise
    
    if not entry_candles or len(entry_candles) < 20:
        logger.debug(
            "[ScalperTrade] bot=%s symbol=%s insufficient candles: %s",
            bot.id,
            symbol,
            len(entry_candles) if entry_candles else 0,
        )
        return {"status": "ok", "reason": "insufficient_candles"}
    
    last_entry = entry_candles[-1]
    entry_atr_points = _atr_like(entry_candles, period=14)
    bar_range = last_entry["high"] - last_entry["low"]

    volatility_snapshot = {
        "atr_points": str(entry_atr_points),
        "bar_range": str(bar_range),
        "tick_volume": last_entry.get("tick_volume"),
        "spread_points": str(spread_points) if spread_points is not None else None,
    }
    broker_snapshot = {
        "min_lot": str(broker_constraints.min_lot) if broker_constraints.min_lot is not None else None,
        "max_lot": str(broker_constraints.max_lot) if broker_constraints.max_lot is not None else None,
        "lot_step": str(broker_lot_step) if broker_lot_step is not None else None,
        "point": str(broker_point) if broker_point is not None else None,
        "stops_level_points": str(broker_min_stop_points) if broker_min_stop_points else None,
        "freeze_level_points": str(broker_constraints.freeze_level_points) if broker_constraints.freeze_level_points else None,
        "max_deviation": str(broker_constraints.max_deviation) if broker_constraints.max_deviation is not None else None,
    }
    market_snapshot = {
        "session": session_label,
        "last_close": str(last_entry["close"]),
        "tick": tick_snapshot,
        "volatility": volatility_snapshot,
        "broker_constraints": broker_snapshot,
    }
    
    signals_created = []
    decisions_made = []
    orders_placed = []
    strategy_events = []

    # Optional HTF bias (15m) to filter countertrend M1 entries
    htf_bias = None
    htf_bias_detail = None
    try:
        htf_candles = get_candles_for_account(
            broker_account=broker_account,
            symbol=symbol,
            timeframe="15m",
            n_bars=120,
        )
        analysis = _analyze_htf_bias(htf_candles)
        if analysis:
            htf_bias = analysis.get("bias")
            htf_bias_detail = analysis
    except Exception:
        htf_bias = None
        htf_bias_detail = None

    # Fallback: reuse last known bias if it is recent
    if htf_bias is None:
        try:
            last = (bot.scalper_params or {}).get("last_htf_bias", {})
            if last:
                ts = last.get("at")
                val = last.get("value")
                detail = last.get("info")
                if ts and val:
                    parsed = datetime.fromisoformat(ts)
                    age_min = (
                        (timezone.now() - timezone.make_aware(parsed, timezone=dt_timezone.utc))
                        if timezone.is_naive(parsed)
                        else (timezone.now() - parsed)
                    ).total_seconds() / 60
                    if age_min <= 60:
                        htf_bias = val
                        htf_bias_detail = detail
        except Exception:
            htf_bias = None
            htf_bias_detail = None

    # If we cannot establish HTF bias, skip this cycle to avoid trading blind.
    if htf_bias is None:
        logger.warning("[ScalperTrade] bot=%s symbol=%s proceeding without HTF bias", bot.id, symbol)

    # Cache latest bias for reuse
    try:
        params = bot.scalper_params or {}
        params["last_htf_bias"] = {
            "value": htf_bias,
            "at": timezone.now().isoformat(),
            "info": htf_bias_detail,
        }
        bot.scalper_params = params
        bot.save(update_fields=["scalper_params"])
    except Exception:
        pass
    
    available_pool: list[str] = []
    if auto_mode:
        available_pool = profile_strats or list(SCALPER_STRATEGY_REGISTRY.keys())
        auto_selected = select_ai_strategies(
            engine_mode="scalper",
            available=available_pool,
            symbol=canonical_sym,
            context={
                "atr_points": entry_atr_points,
                "bar_range": bar_range,
                "last_close": last_entry["close"],
                "spread_points": spread_points,
                "session": session_label,
                "htf_bias": htf_bias,
            },
        )
        enabled_strats = list(auto_selected)
        strategy_context["auto_selected"] = list(auto_selected)

    disabled_profile_strats = set(strategy_profile.disabled_strategies if strategy_profile else [])
    enabled_strats = [
        s
        for s in enabled_strats
        if s in SCALPER_STRATEGY_REGISTRY and s not in disabled_profile_strats
    ]
    if auto_mode:
        if not enabled_strats:
            fallback_pool = [
                s for s in (available_pool or SCALPER_STRATEGY_REGISTRY.keys())
                if s not in disabled_profile_strats
            ]
            enabled_strats = list(fallback_pool)[:3]
            strategy_context["auto_fallback_used"] = True
        strategy_context["active"] = enabled_strats.copy()
    else:
        strategy_context["active"] = enabled_strats.copy()
    if not enabled_strats:
        return {"status": "ok", "reason": "no_active_strategies"}
    
    # Run each enabled strategy
    for strategy_name in enabled_strats:
        strategy_entry = SCALPER_STRATEGY_REGISTRY.get(strategy_name)
        if not strategy_entry:
            logger.debug(
                "[ScalperTrade] bot=%s strategy %s not implemented yet, skipping",
                bot.id,
                strategy_name,
            )
            continue

        engine_decision = None
        try:
            cfg = strategy_entry.config_factory()
            if strategy_entry.requires_symbol:
                engine_decision = strategy_entry.runner(symbol, entry_candles, cfg)
            else:
                engine_decision = strategy_entry.runner(entry_candles, cfg)
        except Exception as e:
            logger.exception(
                "[ScalperTrade] bot=%s strategy=%s candle processing failed: %s",
                bot.id,
                strategy_name,
                e,
            )
            continue
        strategy_events.append(
            {
                "strategy": strategy_name,
                "action": engine_decision.action,
                "reason": engine_decision.reason,
                "score": float(engine_decision.score or 0.0),
                "metadata": engine_decision.metadata or {},
            }
        )
        
        # Safety check - should never happen, but guard against it
        if engine_decision is None:
            logger.warning(
                "[ScalperTrade] bot=%s strategy=%s returned None decision, skipping",
                bot.id,
                strategy_name,
            )
            continue
        
        # Skip if strategy doesn't emit "open"
        if engine_decision.action != "open" or not engine_decision.direction:
            logger.debug(
                "[ScalperTrade] bot=%s strategy=%s action=%s reason=%s",
                bot.id,
                strategy_name,
                engine_decision.action,
                engine_decision.reason,
            )
            continue
        
        # Create deterministic dedupe key per strategy/bar/bot to avoid duplicate signals
        last_bar_time = entry_candles[-1]["time"]
        if hasattr(last_bar_time, "isoformat"):
            time_str = last_bar_time.isoformat()
        else:
            time_str = str(last_bar_time)
        
        dedupe_key = f"scalper:{bot.id}:{symbol}:{timeframe}:{strategy_name}:{time_str}"
        
        # Create or reuse signal
        try:
            strategy_payload = {
                "strategy": strategy_name,
                "sl": str(engine_decision.sl) if engine_decision.sl is not None else None,
                "tp": str(engine_decision.tp) if engine_decision.tp is not None else None,
                "reason": engine_decision.reason,
                "score": float(engine_decision.score or 0.0),
                "generated_at": timezone.now().isoformat(),
                "session": session_label,
                "atr_points": str(entry_atr_points),
                "tick_volume": last_entry.get("tick_volume"),
                "spread_points": str(spread_points) if spread_points is not None else None,
                "point": str(broker_point) if broker_point is not None else None,
                "min_stop_points": str(broker_min_stop_points) if broker_min_stop_points else None,
                "lot_step": str(broker_lot_step) if broker_lot_step is not None else None,
                "broker_constraints": broker_snapshot,
                "market_snapshot": market_snapshot,
                "volatility": volatility_snapshot,
                "strategy_metrics": engine_decision.metadata or {},
                **({"bias_m15": htf_bias} if htf_bias else {}),
            }
            if htf_bias_detail:
                strategy_payload["htf_bias_detail"] = htf_bias_detail
            signal, signal_created = Signal.objects.get_or_create(
                dedupe_key=dedupe_key,
                defaults={
                    "bot": bot,
                    "source": "scalper_engine",
                    "symbol": symbol,
                    "timeframe": timeframe,
                    "direction": engine_decision.direction,
                    "payload": strategy_payload,
                }
            )
            signals_created.append((signal.id, signal_created))
        except Exception as e:
            logger.exception(
                "[ScalperTrade] bot=%s strategy=%s failed to create signal: %s",
                bot.id,
                strategy_name,
                e,
            )
            continue
        
        # Make decision from signal
        try:
            existing_decision = Decision.objects.filter(signal=signal, action="open").first()
            if existing_decision:
                decision = existing_decision
                logger.debug(
                    "[ScalperTrade] bot=%s signal=%s reusing existing decision=%s",
                    bot.id,
                    signal.id,
                    decision.id,
                )
            else:
                decision = make_decision_from_signal(signal)
            decisions_made.append((decision.id, decision.action))
        except Exception as e:
            task_failures_total.labels(task="trade_scalper_strategies_for_bot").inc()
            logger.exception(
                "[ScalperTrade] bot=%s signal=%s decision failed: %s",
                bot.id,
                signal.id,
                e,
            )
            raise
        
        if decision.action != "open":
            logger.debug(
                "[ScalperTrade] decision ignored: bot=%s signal=%s action=%s reason=%s",
                bot.id,
                signal.id,
                decision.action,
                decision.reason,
            )
            continue
        
        # Fanout to orders and dispatch
        try:
            for order, created in fanout_orders(decision, master_qty=None):
                should_dispatch = created or order.status in ("new", "ack")
                if should_dispatch:
                    try:
                        dispatch_place_order(order)
                        orders_placed.append((order.id, order.symbol, order.side))
                    except Exception as e:
                        log_journal_event(
                            "order.dispatch_error",
                            severity="error",
                            order=order,
                            bot=order.bot,
                            broker_account=order.broker_account,
                            symbol=order.symbol,
                            message="Scalper dispatch failed",
                            context={
                                "bot_id": bot.id,
                                "strategy": strategy_name,
                                "error": str(e),
                            },
                        )
                        logger.exception(
                            "[ScalperTrade] bot=%s strategy=%s failed to dispatch order %s: %s",
                            bot.id,
                            strategy_name,
                            order.id,
                            e,
                        )
        except Exception as e:
            logger.exception(
                "[ScalperTrade] bot=%s strategy=%s fanout failed: %s",
                bot.id,
                strategy_name,
                e,
            )
            continue
    
    # Log summary with clearer outcome/context for UI
    if orders_placed:
        outcome = "orders_sent"
    elif decisions_made:
        outcome = "decisions_made_no_orders"
    elif signals_created:
        outcome = "signals_generated_no_decisions"
    else:
        outcome = "no_signals"

    log_journal_event(
        "scalper_engine_run",
        bot=bot,
        owner=bot.owner if bot else None,
        symbol=symbol,
        message=(
            f"Scalper run tf={timeframe} signals={len(signals_created)} "
            f"decisions={len(decisions_made)} orders={len(orders_placed)} "
            f"profile={strategy_profile_key} session={session_label}"
        ),
        context={
            "timeframe": timeframe,
            "session": session_label,
            "auto_trade_active": auto_mode,
            "strategies_enabled": enabled_strats,
            "strategy_profile": strategy_profile_key,
            "outcome": outcome,
            "signals": len(signals_created),
            "decisions": len(decisions_made),
            "orders": len(orders_placed),
            "strategy_context": strategy_context,
        },
    )
    
    logger.info(
        "[ScalperTrade] bot=%s symbol=%s strategies=%s signals=%s decisions=%s orders=%s",
        bot.id,
        symbol,
        len(enabled_strats),
        len(signals_created),
        len(decisions_made),
        len(orders_placed),
    )
    
    if not signals_created:
        summary = {
            "strategies": strategy_events,
            "market": market_snapshot,
            "htf_bias": htf_bias,
            "htf_bias_detail": htf_bias_detail,
            "generated_at": timezone.now().isoformat(),
        }
        try:
            ScalperRunLog.objects.create(
                bot=bot,
                timeframe=timeframe,
                session=session_label,
                summary=_json_safe(summary),
            )
        except Exception:
            logger.exception("[ScalperTrade] failed to persist run log bot=%s", bot.id)

    return {
        "status": "ok",
        "bot_id": bot.id,
        "symbol": symbol,
        "timeframe": timeframe,
        "strategies_enabled": enabled_strats,
        "signals": len(signals_created),
        "decisions": len(decisions_made),
        "orders": len(orders_placed),
    }


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
                log_journal_event(
                    "kill_switch.close",
                    severity="warning",
                    position=pos,
                    broker_account=pos.broker_account,
                    symbol=pos.symbol,
                    message="Kill switch closed position",
                    context={
                        "order_id": order.id,
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
        log_journal_event(
            "order.auto_cancel",
            severity="warning",
            message=f"Auto-canceled {len(canceled)} stale orders",
            context={"orders": canceled, "timeout_sec": timeout},
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
        if not is_mt5_available():
            logger.warning("[Recon] MetaTrader5 library unavailable; skipping acct=%s", acct.id)
            continue
        try:
            connector.login_for_account(acct)
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
        log_journal_event(
            "broker.reconcile",
            severity="info",
            message="Broker reconciliation summary",
            context={
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


@shared_task(
    bind=True,
    autoretry_for=(Exception,),
    retry_backoff=True,
    retry_backoff_max=300,
    retry_jitter=True,
    retry_kwargs={"max_retries": 3},
)
def market_hours_guard_task(self):
    """
    Background guard to auto-stop bots whose market is closed and resume them when open.
    Uses a reversible flag in scalper_params to avoid touching manually stopped bots.
    """
    result = apply_market_guard()
    logger.info(
        "[MarketGuard] stopped=%s resumed=%s skipped_crypto=%s skipped_no_asset=%s errors=%s",
        result["stopped"],
        result["resumed"],
        result["skipped_crypto"],
        result["skipped_no_asset"],
        len(result["errors"]),
    )
    return result
