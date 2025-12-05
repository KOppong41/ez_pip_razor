from datetime import timedelta, datetime
from decimal import Decimal

from django.db import transaction

from execution.models import Signal, Decision, Position, Order
from .strategy import naive_strategy, StrategyDecision
from .risk import RiskConfig, check_risk, ScalperRiskContext
from core.metrics import decisions_total
from django.utils import timezone
from bots.models import Bot  # for per-bot configs on the Bot model
from execution.services.prices import get_price
from execution.services.runtime_config import RuntimeConfig, get_runtime_config
from execution.services.scalper_config import ScalperConfig, build_scalper_config
from execution.services.strategies.scalper import plan_scalper_trade


def _record_scalper_flip(bot: Bot | None, symbol: str | None):
    if not bot or not symbol:
        return
    params = bot.scalper_params or {}
    history = params.get("flip_history") or {}
    history[symbol.upper()] = {"last_at": timezone.now().isoformat()}
    params["flip_history"] = history
    bot.scalper_params = params
    Bot.objects.filter(pk=bot.pk).update(scalper_params=params)


def _log_scalper_trace(signal: Signal | None, stage: str, action: str, reason: str, extra: dict | None = None):
    if not signal:
        return
    payload = signal.payload or {}
    trace = payload.get("decision_trace") or []
    entry = {
        "stage": stage,
        "action": action,
        "reason": reason,
        "at": timezone.now().isoformat(),
    }
    if extra:
        entry["meta"] = extra
    trace.append(entry)
    payload["decision_trace"] = trace[-30:]
    signal.payload = payload
    signal.save(update_fields=["payload"])


def count_open_positions(symbol: str) -> int:
    return Position.objects.filter(symbol=symbol, status="open").count()


def count_total_open_positions() -> int:
    return Position.objects.filter(status="open").count()


def count_total_open_positions_for_bot(bot) -> int:
    qs = Position.objects.filter(status="open")
    if bot and bot.broker_account_id:
        qs = qs.filter(broker_account=bot.broker_account)
    return qs.count()

def count_open_positions_for_bot(bot, symbol: str | None = None) -> int:
    qs = Position.objects.filter(status="open")
    if bot and bot.broker_account_id:
        qs = qs.filter(broker_account=bot.broker_account)
    if symbol:
        qs = qs.filter(symbol=symbol)
    return qs.count()


def _position_direction(qty) -> str | None:
    if qty is None or qty == 0:
        return None
    return "buy" if qty > 0 else "sell"


def detect_position_conflict(
    bot,
    symbol: str,
    new_direction: str,
    score: float,
    runtime_cfg: RuntimeConfig | None = None,
    scalper_cfg: ScalperConfig | None = None,
    scalper_ctx: ScalperRiskContext | None = None,
) -> StrategyDecision | None:
    """
    If a position already exists on this bot's broker account for the symbol,
    decide whether to ignore or allow a new trade.
    Default: no hedging, no stacking in same direction (uses existing SL/TP management instead).
    Also block opposite unless score >= flip threshold.
    """
    cfg = runtime_cfg or get_runtime_config()

    if not bot or not bot.broker_account_id:
        return None

    positions = Position.objects.filter(
        broker_account=bot.broker_account,
        symbol=symbol,
        status="open",
    )
    if not positions.exists():
        return None

    # If multiple exist, just look at aggregate sign for simplicity
    total_qty = sum((p.qty for p in positions), Decimal("0"))
    existing_dir = _position_direction(total_qty)
    if existing_dir is None:
        return None

    allow_hedge = cfg.decision_allow_hedging
    flip_threshold = float(cfg.decision_flip_score)
    allow_scalp = bool(getattr(bot, "allow_opposite_scalp", False)) if bot else False
    allow_scale_in = False
    if scalper_ctx:
        allow_scale_in = bool(scalper_ctx.scale_in_allowed or scalper_ctx.allow_scale_in_default)

    if existing_dir == new_direction:
        if allow_scale_in:
            # scalper risk context already enforces per-symbol caps, so allow stacking.
            return None
        return StrategyDecision(
            action="ignore",
            reason="existing_position_same_direction",
        )

    # Opposite direction exists
    if allow_hedge:
        return None  # allow side-by-side if explicitly enabled

    # Flip on high conviction
    min_flip_score = flip_threshold
    flip_cooldown = 0
    last_flip_at = None
    if scalper_cfg and scalper_cfg.flip_settings:
        min_flip_score = max(min_flip_score, float(scalper_cfg.flip_settings.min_score))
        flip_cooldown = scalper_cfg.flip_settings.cooldown_minutes
    if scalper_ctx:
        last_flip_at = scalper_ctx.last_flip_at or last_flip_at
        if scalper_ctx.flip_cooldown_minutes:
            flip_cooldown = max(flip_cooldown, scalper_ctx.flip_cooldown_minutes)
    cooldown_ok = True
    if last_flip_at and flip_cooldown:
        cooldown_ok = (timezone.now() - last_flip_at).total_seconds() >= flip_cooldown * 60
    if score >= min_flip_score and cooldown_ok:
        return StrategyDecision(
            action="flip",
            reason="flip_triggered",
            score=score,
        )

    # Otherwise, allow a small scalp in the opposite direction to keep the primary position intact.
    if allow_scalp:
        return StrategyDecision(
            action="open",
            reason="opposite_scalp",
            params={"scalp": True},
            score=score,
        )

    return StrategyDecision(
        action="ignore",
        reason="existing_position_opposite_blocked",
        score=score,
    )


def count_bot_trades_today(bot) -> int:
    """
    Legacy helper: counts decisions with action='open' for this bot today.
    Kept for analytics; not used for daily trade limit anymore.
    """
    today = timezone.now().date()
    return Decision.objects.filter(
        bot=bot,
        action="open",
        decided_at__date=today,
    ).count()


def get_last_bot_trade(bot):
    return (
        Decision.objects.filter(bot=bot, action="open")
        .order_by("-decided_at")
        .first()
    )


def get_today_filled_trades(bot, symbol: str | None = None) -> int:
    """
    Count today's *filled* orders for this bot (optionally per symbol).
    This is what we use for daily trade limits.
    """
    today = timezone.now().date()
    qs = Order.objects.filter(
        bot=bot,
        status="filled",
        created_at__date=today,
    )
    if symbol:
        qs = qs.filter(symbol=symbol)
    return qs.count()


def apply_daily_limit(
    proposed: StrategyDecision,
    bot,
    symbol: str,
) -> StrategyDecision:
    """
    Apply per-bot daily limit based on *filled* orders, not just decisions.
    """
    if not bot or not bot.max_trades_per_day:
        return proposed

    filled_today = get_today_filled_trades(bot, symbol=symbol)
    if filled_today >= bot.max_trades_per_day:
        return StrategyDecision(
            action="ignore",
            reason="daily_trade_limit_reached",
            params=proposed.params,
            score=proposed.score,
        )
    return proposed


def get_min_score_for_bot(bot: Bot, symbol: str) -> float | None:
    """
    Optional per-bot minimum score stored directly on Bot.decision_min_score.
    """
    if not bot:
        return None
    try:
        value = float(bot.decision_min_score)
    except Exception:
        return None
    if value is None:
        return None
    if value <= 0:
        # Treat zero/negative values as "inherit profile/engine defaults".
        return None
    return value


def _resolve_scalper_profile_min_score(bot: Bot | None, scalper_cfg: ScalperConfig | None) -> float | None:
    if not bot or not scalper_cfg:
        return None
    params = getattr(bot, "scalper_params", {}) or {}
    profile_key = None
    if isinstance(params, dict):
        profile_key = params.get("score_profile") or params.get("score_profile_key")
    if not profile_key:
        profile_key = scalper_cfg.default_score_profile
    if not profile_key:
        return None
    threshold = scalper_cfg.score_profiles.get(profile_key)
    if threshold is None:
        threshold = scalper_cfg.score_profiles.get(str(profile_key).lower())
    if threshold is None:
        return None
    return float(threshold)


def _build_scalp_params(
    signal: Signal,
    runtime_cfg: RuntimeConfig | None = None,
    scalper_cfg: ScalperConfig | None = None,
) -> dict:
    """
    Prepare tight SL/TP + size multiplier for an opposite-direction scalp.
    Uses small configurable offsets around the current price.
    """
    cfg = runtime_cfg or get_runtime_config()
    # Defaults: a quick in/out scalp
    sl_offset = Decimal(str(cfg.decision_scalp_sl_offset))
    tp_offset = Decimal(str(cfg.decision_scalp_tp_offset))
    qty_multiplier = Decimal(str(cfg.decision_scalp_qty_multiplier))

    price = None
    try:
        price = get_price(signal.symbol)
    except Exception:
        price = None

    # Respect instrument-specific minimum stop distance.
    point = Decimal("0.0001")
    sl_distance = sl_offset
    if scalper_cfg:
        symbol_cfg = scalper_cfg.resolve_symbol(signal.symbol)
        if symbol_cfg:
            point = Decimal("0.10") if symbol_cfg.key.startswith("XAU") else Decimal("0.0001")
            min_points = symbol_cfg.sl_points_min
            if min_points and min_points > 0:
                min_distance = point * min_points
                if sl_distance < min_distance:
                    sl_distance = min_distance

    tp_distance = tp_offset
    if tp_distance < sl_distance:
        tp_distance = sl_distance

    params = {
        "symbol": signal.symbol,
        "timeframe": signal.timeframe,
        "direction": signal.direction,
        "scalp": True,
        "qty_multiplier": str(qty_multiplier),
    }

    if price is not None:
        px = Decimal(str(price))
        if signal.direction == "buy":
            params["sl"] = str(px - sl_distance)
            params["tp"] = str(px + tp_distance)
        else:
            params["sl"] = str(px + sl_distance)
            params["tp"] = str(px - tp_distance)

    return params


def _parse_decimal(payload: dict | None, *keys: str) -> Decimal | None:
    payload = payload or {}
    for key in keys:
        if payload.get(key) is None:
            continue
        try:
            return Decimal(str(payload[key]))
        except Exception:
            continue
    return None


def _build_scalper_risk_context(bot: Bot, signal: Signal, scalper_cfg: ScalperConfig) -> ScalperRiskContext:
    payload = signal.payload or {}
    now = timezone.now()

    trades_symbol = get_today_filled_trades(bot, symbol=signal.symbol)
    trades_total = get_today_filled_trades(bot, symbol=None)

    reentry_window = max(scalper_cfg.time_in_trade_limit_min, 5)
    window_since = now - timedelta(minutes=reentry_window)
    reentry_qs = Decision.objects.filter(
        bot=bot,
        signal__symbol=signal.symbol,
        action="open",
        decided_at__gte=window_since,
    )
    reentry_count = reentry_qs.count()

    last_same = reentry_qs.filter(signal__direction=signal.direction).order_by("-decided_at").first()
    minutes_since_last_same = None
    if last_same:
        minutes_since_last_same = max(
            0,
            int((now - last_same.decided_at).total_seconds() // 60),
        )

    last_loss = (
        Decision.objects.filter(
            bot=bot,
            signal__symbol=signal.symbol,
            action="close",
            reason__icontains="loss",
            decided_at__gte=now - timedelta(hours=8),
        )
        .order_by("-decided_at")
        .first()
    )
    minutes_since_last_loss = None
    if last_loss:
        minutes_since_last_loss = max(0, int((now - last_loss.decided_at).total_seconds() // 60))

    spread_points = _parse_decimal(payload, "spread_points", "spread")
    slippage_points = _parse_decimal(payload, "slippage_points", "slippage")
    floating_risk_pct = _parse_decimal(payload, "floating_symbol_risk_pct", "symbol_risk_pct")

    floating_pnl_points = _parse_decimal(payload, "floating_pnl_points")
    scale_in_allowed = bool(payload.get("scale_in_allowed", False))
    if not scale_in_allowed and floating_pnl_points is not None and floating_pnl_points > 0:
        scale_in_allowed = True
    countertrend = bool(payload.get("countertrend") or payload.get("is_countertrend"))

    last_flip_at = None
    flip_history = (bot.scalper_params or {}).get("flip_history") if bot else None
    if flip_history:
        entry = flip_history.get(signal.symbol.upper())
        if entry and entry.get("last_at"):
            try:
                last_flip_at = datetime.fromisoformat(entry["last_at"])
                if timezone.is_naive(last_flip_at):
                    last_flip_at = timezone.make_aware(last_flip_at, timezone=timezone.utc)
            except Exception:
                last_flip_at = None

    return ScalperRiskContext(
        config=scalper_cfg,
        symbol=signal.symbol,
        direction=signal.direction,
        trades_today_symbol=trades_symbol,
        trades_today_total=trades_total,
        reentry_count=reentry_count,
        minutes_since_last_same_direction=minutes_since_last_same,
        minutes_since_last_loss=minutes_since_last_loss,
        spread_points=spread_points,
        slippage_points=slippage_points,
        floating_symbol_risk_pct=floating_risk_pct,
        scale_in_allowed=scale_in_allowed,
        allow_scale_in_default=bool(scalper_cfg.risk and scalper_cfg.risk.max_trades_per_symbol > 1),
        countertrend=countertrend,
        payload_snapshot=payload,
        last_flip_at=last_flip_at,
        flip_cooldown_minutes=int(scalper_cfg.flip_settings.cooldown_minutes) if scalper_cfg.flip_settings else 0,
    )


@transaction.atomic
def make_decision_from_signal(signal: Signal) -> Decision:
    runtime_cfg = get_runtime_config()

    bot = signal.bot
    is_scalper_bot = bool(bot and getattr(bot, "engine_mode", "") == "scalper")
    scalper_cfg = build_scalper_config(bot) if is_scalper_bot else None

    # 1) Strategy propose
    # NOTE: If signal is ALREADY from scalper_engine, skip re-planning (avoid double-filtering)
    if scalper_cfg and signal.source != "scalper_engine":
        proposed = plan_scalper_trade(signal, bot, scalper_cfg)
        if proposed.action != "open":
            _log_scalper_trace(signal, "strategy", proposed.action, proposed.reason)
    elif signal.source == "engine_v1" or signal.source == "scalper_engine":
        payload = signal.payload or {}
        params = {
            "symbol": signal.symbol,
            "timeframe": signal.timeframe,
            "direction": signal.direction,
        }
        if payload.get("sl") is not None:
            params["sl"] = payload["sl"]
        if payload.get("tp") is not None:
            params["tp"] = payload["tp"]
        if payload.get("atr") is not None:
            params["atr"] = payload["atr"]

        proposed = StrategyDecision(
            action="open",
            reason=payload.get("reason", "engine_v1"),
            params=params,
            score=float(payload.get("score", 0.0)),  # read from engine
        )


    else:
        # external / naive strategy
        proposed = naive_strategy(signal)

    # 2) Risk check (positions / allowed_symbols)
    scalper_ctx = None
    if proposed.action == "open":
        if bot:
            if scalper_cfg and scalper_cfg.risk:
                allowed = tuple(scalper_cfg.alias_map.keys())
                cfg = RiskConfig(
                    max_positions_per_symbol=scalper_cfg.risk.max_trades_per_symbol,
                    max_concurrent_positions=scalper_cfg.risk.max_concurrent_trades,
                    allowed_symbols=allowed,
                )
                scalper_ctx = _build_scalper_risk_context(bot, signal, scalper_cfg)
            else:
                sym = bot.asset.symbol if bot.asset else None
                cfg = RiskConfig(
                    max_positions_per_symbol=(bot.risk_max_positions_per_symbol or 1),
                    max_concurrent_positions=(bot.risk_max_concurrent_positions or 5),
                    allowed_symbols=(sym,) if sym else (),
                )
        else:
            cfg = RiskConfig()

        open_symbol = (
            count_open_positions_for_bot(bot, symbol=signal.symbol)
            if bot
            else count_open_positions(symbol=signal.symbol)
        )
        open_total = count_total_open_positions_for_bot(bot) if bot else count_total_open_positions()
        ok, risk_reason = check_risk(signal, open_symbol, open_total, cfg, scalper_ctx=scalper_ctx)

        if not ok:
            proposed = StrategyDecision(
                action="ignore",
                reason=risk_reason,
                params=proposed.params,
                score=proposed.score,
            )
            if is_scalper_bot:
                _log_scalper_trace(signal, "risk", proposed.action, risk_reason, {"open_symbol": open_symbol, "open_total": open_total})

    # 2b) Per-bot minimum score filter (default 0.5 globally)
    if proposed.action == "open":
        min_score = get_min_score_for_bot(bot, symbol=signal.symbol) if bot else None
        if min_score is None and is_scalper_bot:
            min_score = _resolve_scalper_profile_min_score(bot, scalper_cfg)
        # Use per-bot config if available, otherwise default from runtime settings (admin-tunable, default 0.5)
        if min_score is None:
            min_score = float(runtime_cfg.decision_min_score)
        
        if proposed.score < min_score:
            proposed = StrategyDecision(
                action="ignore",
                reason="score_below_min",
                params=proposed.params,
                score=proposed.score,
            )
            if is_scalper_bot:
                _log_scalper_trace(signal, "score", proposed.action, "score_below_min", {"score": proposed.score, "min_score": min_score})

    # 2c) Position conflict guardrail (no stacking/hedging unless enabled; optional flip)
    flip_info = None
    if proposed.action == "open" and bot:
        conflict = detect_position_conflict(
            bot=bot,
            symbol=signal.symbol,
            new_direction=signal.direction,
            score=proposed.score,
            runtime_cfg=runtime_cfg,
            scalper_cfg=scalper_cfg if is_scalper_bot else None,
            scalper_ctx=scalper_ctx,
        )
        if callable(conflict):
            conflict = conflict(proposed.score)
        if conflict:
            if conflict.action == "flip":
                flip_info = {
                    "symbol": signal.symbol,
                    "direction": signal.direction,
                    "score": proposed.score,
                }
                _log_scalper_trace(signal, "conflict", "flip", conflict.reason)
            elif conflict.action == "open" and conflict.reason == "opposite_scalp":
                # Apply scalp overrides: tighter SL/TP + smaller size while keeping primary position alive.
                params = proposed.params.copy() if proposed.params else {}
                params.update(_build_scalp_params(signal, runtime_cfg=runtime_cfg, scalper_cfg=scalper_cfg))
                proposed = StrategyDecision(
                    action="open",
                    reason="opposite_scalp",
                    params=params,
                    score=proposed.score,
                )
                _log_scalper_trace(signal, "conflict", "opposite_scalp", conflict.reason)
            else:
                proposed = StrategyDecision(
                    action=conflict.action,
                    reason=conflict.reason,
                    params=proposed.params,
                    score=conflict.score if conflict.score is not None else proposed.score,
                )
                _log_scalper_trace(signal, "conflict", proposed.action, conflict.reason)

    # 3) Per-bot trade interval + daily max trades (filled-based)
    if proposed.action == "open" and bot:
        now = timezone.now()

        # Daily limit based on *filled* orders
        tmp = proposed
        proposed = apply_daily_limit(proposed, bot=bot, symbol=signal.symbol)
        if proposed.action != "open" and tmp.action == "open":
            _log_scalper_trace(signal, "daily_limit", proposed.action, proposed.reason)

        # min interval between trades (still based on last 'open' decision)
        if proposed.action == "open" and bot.trade_interval_minutes:
            last_trade = get_last_bot_trade(bot)
            if last_trade:
                delta = now - last_trade.decided_at
                if delta.total_seconds() < bot.trade_interval_minutes * 60:
                    proposed = StrategyDecision(
                        action="ignore",
                        reason="min_trade_interval_not_elapsed",
                        params=proposed.params,
                        score=proposed.score,
                    )
                    _log_scalper_trace(signal, "interval", proposed.action, "min_trade_interval_not_elapsed", {"minutes": bot.trade_interval_minutes})

    # 4) Persist decision
    decision = Decision.objects.create(
        bot=signal.bot,
        owner=(signal.bot.owner if signal.bot and getattr(signal.bot, "owner", None) else getattr(signal, "owner", None)),
        signal=signal,
        action=proposed.action,
        reason=proposed.reason,
        score=proposed.score,
        params=proposed.params or {},
    )
    decisions_total.labels(action=decision.action).inc()
    if is_scalper_bot:
        _log_scalper_trace(signal, "final", decision.action, decision.reason, {"score": decision.score})

    # Optional flip handling: create a paired close decision for the existing position.
    if flip_info:
        from execution.services.positions import prepare_flip_decisions
        prepare_flip_decisions(decision, flip_info)
        _record_scalper_flip(bot, flip_info.get("symbol"))

    return decision
