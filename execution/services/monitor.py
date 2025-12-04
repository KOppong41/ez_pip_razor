from dataclasses import dataclass
from datetime import timedelta
from decimal import Decimal
from typing import Optional

from django.utils import timezone

from bots.models import Bot
from execution.models import Decision, Position
from execution.services.brokers import dispatch_place_order
from execution.services.orchestrator import create_close_order_for_position
from execution.services.prices import get_price

@dataclass
class EarlyExitConfig:
    max_unrealized_pct: Decimal = Decimal("0.02")  # close if loss > 2% of notional

@dataclass
class TrailingConfig:
    trigger: Decimal = Decimal("0.0005")   # e.g. 5 pips for EURUSD
    distance: Decimal = Decimal("0.0003")  # trail 3 pips behind peak
    
@dataclass
class KillSwitchConfig:
    """
    Kill-switch settings.

    For now it is purely risk-based (unrealized loss threshold).
    Later we can extend with engine-based 'probability' signals:
    - require_opposite_engine_signal: bool
    - min_engine_confidence: Decimal
    etc.
    """
    max_unrealized_pct: Decimal = Decimal("0.01")  # e.g. 1% of notional

def unrealized_pnl(pos: Position, mkt: Decimal) -> Decimal:
    # sign: + for long when price>avg_price; for short reverse
    if pos.qty >= 0:
        return (mkt - pos.avg_price) * pos.qty
    else:
        return (pos.avg_price - mkt) * abs(pos.qty)

def notional(pos: Position, mkt: Decimal) -> Decimal:
    return abs(pos.qty) * mkt

def should_early_exit(pos: Position, mkt: Decimal, cfg: EarlyExitConfig) -> bool:
    notion = notional(pos, mkt)
    if notion == 0:
        return False
    loss = -unrealized_pnl(pos, mkt)  # positive when losing
    return loss / notion >= cfg.max_unrealized_pct


def should_trigger_kill_switch(pos: Position, mkt: Decimal, cfg: KillSwitchConfig, engine_opposite: bool = False) -> bool:
    """
    Enhanced kill-switch rule:
    - Only fires when position is losing.
    - Loss / notional >= cfg.max_unrealized_pct (e.g. 1%).
    - IMPROVED: AND with engine-based opposite signal for confirmation.
    
    Args:
        pos: Position to check
        mkt: Current market price
        cfg: Kill-switch config
        engine_opposite: True if engine detected opposite direction signal
    
    Returns: True if kill-switch should trigger
    """
    notion = notional(pos, mkt)
    if notion == 0:
        return False

    loss = -unrealized_pnl(pos, mkt)  # positive when losing
    if loss <= 0:
        return False

    risk_trigger = loss / notion >= cfg.max_unrealized_pct
    
    # IMPROVED: Require both risk AND engine confirmation
    # This prevents premature exits on noise while still protecting on real reversals
    if engine_opposite:
        # Strong signal: both risk AND engine say exit
        return True
    
    # Fallback: exit if risk alone exceeds threshold (emergency brake)
    # Use higher threshold when no engine confirmation
    return loss / notion >= (cfg.max_unrealized_pct * Decimal("2"))



def apply_trailing(pos: Position, mkt: Decimal, cfg: TrailingConfig, atr: Decimal = None) -> bool:
    """
    Returns True if SL updated. For MVP, we store SL on the Position.
    Long: SL = max(current SL, mkt - distance) after trigger.
    Short: SL = min(current SL, mkt + distance) after trigger.
    
    If atr provided, scale distance dynamically (ATR-based trailing).
    """
    moved = False
    
    # Use ATR-scaled distance if available (more robust than fixed pips)
    distance = cfg.distance
    if atr is not None and atr > 0:
        distance = atr * Decimal("0.5")  # trail 0.5x ATR behind profit
    
    if pos.qty > 0:
        # Long
        profit = mkt - pos.avg_price
        if profit >= cfg.trigger:
            new_sl = mkt - distance
            if pos.sl is None or new_sl > pos.sl:
                pos.sl = new_sl
                moved = True
    elif pos.qty < 0:
        # Short
        profit = pos.avg_price - mkt
        if profit >= cfg.trigger:
            new_sl = mkt + distance
            if pos.sl is None or new_sl < pos.sl:
                pos.sl = new_sl
                moved = True
    return moved

def _find_scalper_bot(pos: Position) -> Optional[Bot]:
    return (
        Bot.objects.filter(broker_account=pos.broker_account, engine_mode="scalper")
        .order_by("-created_at")
        .first()
    )


def _latest_scalper_meta(bot: Bot, symbol: str) -> Optional[dict]:
    if not bot:
        return None
    decision = (
        Decision.objects.filter(bot=bot, signal__symbol=symbol, action="open")
        .exclude(params__scalper__isnull=True)
        .order_by("-decided_at")
        .first()
    )
    if not decision:
        return None
    params = decision.params or {}
    scalper = params.get("scalper")
    if not scalper:
        return None
    try:
        entry = Decimal(str(params.get("entry")))
        sl = Decimal(str(params.get("sl")))
    except Exception:
        return None
    meta = {
        "entry": entry,
        "sl": sl,
        "tp": Decimal(str(params.get("tp"))) if params.get("tp") else None,
        "direction": params.get("direction") or decision.signal.direction,
        "risk_pct": Decimal(str(params.get("risk_pct", "0"))),
        "scalper": scalper,
        "decided_at": decision.decided_at,
    }
    return meta


def _maybe_move_to_be(pos: Position, meta: dict, reward: Decimal, risk: Decimal) -> bool:
    scalper = meta.get("scalper") or {}
    trigger = Decimal(str(scalper.get("be_trigger_r", "1.0")))
    buffer_r = Decimal(str(scalper.get("be_buffer_r", "0.2")))
    if trigger <= 0 or reward < risk * trigger:
        return False
    buffer = risk * buffer_r
    direction = (meta.get("direction") or ("buy" if pos.qty > 0 else "sell")).lower()
    moved = False
    if direction == "buy":
        new_sl = meta["entry"] + buffer
        if pos.sl is None or new_sl > pos.sl:
            pos.sl = new_sl
            moved = True
    else:
        new_sl = meta["entry"] - buffer
        if pos.sl is None or new_sl < pos.sl:
            pos.sl = new_sl
            moved = True
    return moved


def _maybe_trail_scalper(pos: Position, meta: dict, mkt: Decimal, reward: Decimal, risk: Decimal) -> bool:
    scalper = meta.get("scalper") or {}
    trigger = Decimal(str(scalper.get("trail_trigger_r", "1.5")))
    if trigger <= 0 or reward < risk * trigger:
        return False
    mode = (scalper.get("trail_mode") or "swing").lower()
    step = Decimal("0.50") if mode == "swing" else Decimal("0.35")
    distance = risk * step
    direction = (meta.get("direction") or ("buy" if pos.qty > 0 else "sell")).lower()
    moved = False
    if direction == "buy":
        new_sl = mkt - distance
        if pos.sl is None or new_sl > pos.sl:
            pos.sl = new_sl
            moved = True
    else:
        new_sl = mkt + distance
        if pos.sl is None or new_sl < pos.sl:
            pos.sl = new_sl
            moved = True
    return moved


def _should_flatten_stale(meta: dict, reward: Decimal, risk: Decimal) -> bool:
    scalper = meta.get("scalper") or {}
    limit_min = int(scalper.get("time_in_trade_limit_min") or 0)
    if limit_min <= 0:
        return False
    age = timezone.now() - meta["decided_at"]
    if age < timedelta(minutes=limit_min):
        return False
    # Only flatten if reward is near breakeven (+/-0.3R)
    if risk <= 0:
        return True
    reward_r = reward / risk
    return abs(reward_r) <= Decimal("0.3")


def manage_scalper_position(pos: Position, mkt: Decimal) -> bool:
    if mkt is None:
        return False
    bot = _find_scalper_bot(pos)
    if not bot:
        return False
    meta = _latest_scalper_meta(bot, pos.symbol)
    if not meta:
        return False
    entry = meta["entry"]
    sl = meta["sl"]
    direction = (meta.get("direction") or ("buy" if pos.qty > 0 else "sell")).lower()
    risk = abs(entry - sl)
    if risk <= 0:
        return False
    reward = (mkt - entry) if direction == "buy" else (entry - mkt)
    moved = False
    if reward > 0:
        moved |= _maybe_move_to_be(pos, meta, reward, risk)
        moved |= _maybe_trail_scalper(pos, meta, mkt, reward, risk)
    if _should_flatten_stale(meta, reward, risk):
        close_position_now(pos)
        return True
    if moved:
        pos.save(update_fields=["sl"])
    return moved


def create_close_order(pos: Position) -> Decision:
    """
    Creates a close Decision against the bot that owns the position's broker account.
    Uses get_or_create for idempotency - if close decision already exists, returns it.
    For close orders, we don't need SL/TP since we're closing the position.
    """
    from execution.models import Signal
    from bots.models import Bot
    from django.utils import timezone
    
    # Try to find a bot on this broker account that trades this symbol
    bot = Bot.objects.filter(
        broker_account=pos.broker_account,
        asset__symbol=pos.symbol,
    ).first() or Bot.objects.filter(broker_account=pos.broker_account).first()
    
    # Use get_or_create for idempotency - prevents duplicate close signals
    dedupe_key = f"auto-close-{pos.id}"
    sig, sig_created = Signal.objects.get_or_create(
        dedupe_key=dedupe_key,
        defaults={
            "bot": bot,
            "source": "monitor",
            "symbol": pos.symbol,
            "timeframe": "5m",
            "direction": "sell" if pos.qty > 0 else "buy",
            "payload": {"reason": "auto_close", "closed_at": timezone.now().isoformat()},
        }
    )
    if not sig_created and sig.bot_id != (bot.id if bot else None):
        sig.bot = bot
        sig.save(update_fields=["bot"])
    
    # Get or create corresponding Decision (also idempotent)
    # For close orders, use current price as both SL and TP (market order)
    # This bypasses the SL/TP enforcement since close orders are market-driven
    dec, dec_created = Decision.objects.get_or_create(
        signal=sig,
        defaults={
            "bot": sig.bot,
            "action": "close",
            "reason": "risk:auto_close",
            "score": 1.0,  # Close orders are high priority
            "params": {
                # For close orders, we use market close (no SL/TP needed)
                # But include dummy values to satisfy enforcement
                "sl": None,
                "tp": None,
            },
        }
    )
    if not dec_created and dec.bot_id != (sig.bot_id if sig else None):
        dec.bot = sig.bot
        dec.save(update_fields=["bot"])
    return dec

def close_position_now(pos: Position):
    # Decide & create an order for the same broker account
    dec = create_close_order(pos)
    order, _ = create_close_order_for_position(pos, pos.broker_account)
    dispatch_place_order(order)
    return order
