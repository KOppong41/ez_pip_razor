from django.db import transaction
from decimal import Decimal

from execution.models import Signal, Decision, Position, Order
from .strategy import naive_strategy, StrategyDecision
from .risk import RiskConfig, check_risk
from core.metrics import decisions_total
from django.utils import timezone
from bots.models import Bot  # for per-bot configs on the Bot model
from execution.services.prices import get_price
from execution.services.runtime_config import RuntimeConfig, get_runtime_config


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
    allow_scalp = bool(getattr(bot, "allow_opposite_scalp", False))
    flip_threshold = float(cfg.decision_flip_score)

    if existing_dir == new_direction:
        return StrategyDecision(
            action="ignore",
            reason="existing_position_same_direction",
        )

    # Opposite direction exists
    if allow_hedge:
        return None  # allow side-by-side if explicitly enabled

    # Flip on high conviction
    if score >= flip_threshold:
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
        return float(bot.decision_min_score)
    except Exception:
        return None


def _build_scalp_params(signal: Signal, runtime_cfg: RuntimeConfig | None = None) -> dict:
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
            params["sl"] = str(px - sl_offset)
            params["tp"] = str(px + tp_offset)
        else:
            params["sl"] = str(px + sl_offset)
            params["tp"] = str(px - tp_offset)

    return params


@transaction.atomic
def make_decision_from_signal(signal: Signal) -> Decision:
    runtime_cfg = get_runtime_config()

    # 1) Strategy propose
    if signal.source == "engine_v1":
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

    bot = signal.bot

    # 2) Risk check (positions / allowed_symbols)
    if proposed.action == "open":
        if bot:
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
        ok, risk_reason = check_risk(signal, open_symbol, open_total, cfg)

        if not ok:
            proposed = StrategyDecision(
                action="ignore",
                reason=risk_reason,
                params=proposed.params,
                score=proposed.score,
            )

    # 2b) Per-bot minimum score filter (default 0.5 globally)
    if proposed.action == "open":
        min_score = get_min_score_for_bot(bot, symbol=signal.symbol) if bot else None
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

    # 2c) Position conflict guardrail (no stacking/hedging unless enabled; optional flip)
    flip_info = None
    if proposed.action == "open" and bot:
        conflict = detect_position_conflict(
            bot=bot,
            symbol=signal.symbol,
            new_direction=signal.direction,
            score=proposed.score,
            runtime_cfg=runtime_cfg,
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
            elif conflict.action == "open" and conflict.reason == "opposite_scalp":
                # Apply scalp overrides: tighter SL/TP + smaller size while keeping primary position alive.
                params = proposed.params.copy() if proposed.params else {}
                params.update(_build_scalp_params(signal, runtime_cfg=runtime_cfg))
                proposed = StrategyDecision(
                    action="open",
                    reason="opposite_scalp",
                    params=params,
                    score=proposed.score,
                )
            else:
                proposed = StrategyDecision(
                    action=conflict.action,
                    reason=conflict.reason,
                    params=proposed.params,
                    score=conflict.score if conflict.score is not None else proposed.score,
                )

    # 3) Per-bot trade interval + daily max trades (filled-based)
    if proposed.action == "open" and bot:
        now = timezone.now()

        # Daily limit based on *filled* orders
        proposed = apply_daily_limit(proposed, bot=bot, symbol=signal.symbol)

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

    # Optional flip handling: create a paired close decision for the existing position.
    if flip_info:
        from execution.services.positions import prepare_flip_decisions
        prepare_flip_decisions(decision, flip_info)

    return decision
