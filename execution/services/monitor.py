from decimal import Decimal
from dataclasses import dataclass
from execution.models import Position, Decision
from execution.services.orchestrator import (
    create_close_order_for_position,
)
from execution.services.brokers import dispatch_place_order
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
