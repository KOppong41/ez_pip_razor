from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, time, timedelta, timezone as dt_timezone
from typing import Optional

from django.utils import timezone

from execution.connectors.mt5 import is_mt5_available, mt5
from execution.services.marketdata import _login_mt5_for_account

logger = logging.getLogger(__name__)

# Default FX/CFD trading window (UTC).
# Most MT5 symbols follow a 24x5 schedule: Sunday ~22:00 UTC through Friday ~22:00 UTC.
FX_WEEKLY_OPEN_UTC = time(22, 0)
FX_WEEKLY_CLOSE_UTC = time(22, 0)


def _is_crypto_symbol(symbol: str | None) -> bool:
    if not symbol:
        return False
    s = symbol.lower()
    crypto_keys = ("btc", "eth", "xbt")
    return any(s.startswith(k) or f"{k}" in s for k in crypto_keys)


@dataclass(frozen=True)
class MarketStatus:
    is_open: bool
    reason: str
    checked_at: datetime
    next_open: Optional[datetime] = None
    source: str = "calendar"
    details: Optional[dict] = None


def _next_weekly_open(now: datetime) -> datetime:
    """
    Compute the next weekly open (Sunday evening UTC).
    """
    # Next Sunday (weekday=6)
    days_ahead = (6 - now.weekday()) % 7
    candidate_date = (now + timedelta(days=days_ahead)).date()
    candidate_dt = datetime.combine(candidate_date, FX_WEEKLY_OPEN_UTC, tzinfo=dt_timezone.utc)
    if candidate_dt <= now:
        candidate_dt += timedelta(days=7)
    return candidate_dt


def _calendar_status(now: datetime, asset_category: str | None, symbol: str | None) -> MarketStatus:
    """
    Fast, calendar-only guard:
    - Crypto is treated as 24/7.
    - Other assets are treated as 24x5 with a Friday close and Sunday open.
    """
    category = (asset_category or "").lower()
    if category == "crypto" or _is_crypto_symbol(symbol):
        return MarketStatus(is_open=True, reason="crypto_24_7", checked_at=now, source="calendar")

    # Weekend closure for FX/CFDs/indices/metals.
    wd = now.weekday()  # Monday=0 ... Sunday=6
    t = now.time()
    if wd == 5:  # Saturday
        return MarketStatus(
            is_open=False,
            reason="weekend",
            checked_at=now,
            next_open=_next_weekly_open(now),
        )
    if wd == 6 and t < FX_WEEKLY_OPEN_UTC:  # Sunday pre-open
        return MarketStatus(
            is_open=False,
            reason="pre_open",
            checked_at=now,
            next_open=_next_weekly_open(now),
        )
    if wd == 4 and t >= FX_WEEKLY_CLOSE_UTC:  # Friday post-close
        return MarketStatus(
            is_open=False,
            reason="friday_close",
            checked_at=now,
            next_open=_next_weekly_open(now),
        )

    return MarketStatus(is_open=True, reason="session_open", checked_at=now, source="calendar")


def _probe_mt5_market(symbol: str, broker_account, now: datetime) -> Optional[MarketStatus]:
    """
    Optional MT5-level probe to refine market state using live symbol info/ticks.
    Returns MarketStatus if a definitive state was determined, otherwise None.
    """
    if not is_mt5_available():
        return None

    try:
        _login_mt5_for_account(broker_account)

        info = mt5.symbol_info(symbol)
        if info is None:
            return MarketStatus(
                is_open=False,
                reason="symbol_info_unavailable",
                checked_at=now,
                next_open=_next_weekly_open(now),
                source="mt5",
            )

        if not info.visible:
            mt5.symbol_select(symbol, True)
            info = mt5.symbol_info(symbol)
            if info is None or not info.visible:
                return MarketStatus(
                    is_open=False,
                    reason="symbol_not_visible",
                    checked_at=now,
                    next_open=_next_weekly_open(now),
                    source="mt5",
                )

        disabled_modes = {mt5.SYMBOL_TRADE_MODE_DISABLED}
        close_only = getattr(mt5, "SYMBOL_TRADE_MODE_CLOSEONLY", None)
        if close_only is not None:
            disabled_modes.add(close_only)

        trade_mode = getattr(info, "trade_mode", None)
        if trade_mode in disabled_modes:
            return MarketStatus(
                is_open=False,
                reason="trade_mode_closed",
                checked_at=now,
                next_open=_next_weekly_open(now),
                source="mt5",
                details={"trade_mode": trade_mode},
            )

        # Treat very stale ticks as closed/illiquid.
        try:
            tick = mt5.symbol_info_tick(symbol)
        except Exception:
            tick = None

        if tick:
            tick_time = getattr(tick, "time", None)
            if isinstance(tick_time, datetime):
                tick_dt = tick_time if tick_time.tzinfo else tick_time.replace(tzinfo=dt_timezone.utc)
            elif tick_time:
                tick_dt = datetime.fromtimestamp(float(tick_time), tz=dt_timezone.utc)
            else:
                tick_dt = None

            if tick_dt:
                age = (now - tick_dt).total_seconds()
                if age > 1800:  # >30 minutes without ticks
                    return MarketStatus(
                        is_open=False,
                        reason="stale_tick",
                        checked_at=now,
                        next_open=_next_weekly_open(now),
                        source="mt5",
                        details={"tick_age_seconds": int(age)},
                    )

        return MarketStatus(is_open=True, reason="mt5_session_open", checked_at=now, source="mt5")

    except Exception as e:
        # Don't block trading if the probe fails; just log.
        logger.warning(
            "[MarketHours] MT5 probe failed for symbol=%s broker_account=%s: %s",
            symbol,
            getattr(broker_account, "id", None),
            e,
        )
        return None
    finally:
        try:
            mt5.shutdown()
        except Exception:
            pass


def get_market_status(
    symbol: str | None,
    asset_category: str | None = None,
    broker_account=None,
    *,
    now: datetime | None = None,
    use_mt5_probe: bool = False,
) -> MarketStatus:
    """
    Determine whether a symbol's market is open.
    - Calendar heuristics to avoid weekend trading.
    - Optional MT5 probe for finer-grained status (trade_mode, stale ticks).
    """
    current = now or timezone.now()
    current_utc = (
        current.astimezone(dt_timezone.utc)
        if current.tzinfo
        else current.replace(tzinfo=dt_timezone.utc)
    )

    if not symbol:
        return MarketStatus(is_open=False, reason="no_symbol", checked_at=current_utc)

    status = _calendar_status(current_utc, asset_category, symbol)
    if not status.is_open:
        return status

    if use_mt5_probe and broker_account:
        probe = _probe_mt5_market(symbol, broker_account, current_utc)
        if probe:
            return probe

    return MarketStatus(is_open=True, reason="open", checked_at=current_utc)


def get_market_status_for_bot(bot, *, now: datetime | None = None, use_mt5_probe: bool = False) -> MarketStatus:
    """
    Convenience wrapper to fetch market status using Bot attributes.
    """
    asset = getattr(bot, "asset", None)
    category = getattr(asset, "category", None) if asset else None
    symbol = getattr(asset, "symbol", None)
    broker_account = getattr(bot, "broker_account", None)
    return get_market_status(
        symbol=symbol,
        asset_category=category,
        broker_account=broker_account,
        now=now,
        use_mt5_probe=use_mt5_probe,
    )


def is_crypto_symbol(symbol: str | None) -> bool:
    """Public helper to check if a symbol should be treated as 24/7 crypto."""
    return _is_crypto_symbol(symbol)


def maybe_unpause_crypto_for_open_market(bot, status: MarketStatus) -> Optional[datetime]:
    """
    If a crypto bot was previously auto-paused by mistake, clear paused_until when the market is open.
    Only acts on active bots with crypto symbols.
    """
    symbol = getattr(getattr(bot, "asset", None), "symbol", None)
    if not status.is_open or not _is_crypto_symbol(symbol):
        return None
    if getattr(bot, "status", None) != "active":
        return None
    paused_until = getattr(bot, "paused_until", None)
    if not paused_until:
        return None
    try:
        bot.paused_until = None
        bot.save(update_fields=["paused_until"])
        logger.info("[MarketHours] bot=%s unpaused (crypto market open)", getattr(bot, "id", None))
        return None
    except Exception:
        logger.exception("[MarketHours] failed to clear paused_until for crypto bot=%s", getattr(bot, "id", None))
        return None


def maybe_pause_bot_for_market(bot, status: MarketStatus) -> Optional[datetime]:
    """
    Optionally set paused_until when we detect a closed market.
    Does not override an existing future pause to respect manual/user pauses.
    Returns the pause-until timestamp if applied.
    """
    # Never auto-pause crypto bots (24/7).
    symbol = getattr(getattr(bot, "asset", None), "symbol", None)
    if _is_crypto_symbol(symbol):
        return None

    if status.is_open:
        return None

    pause_until = status.next_open or (timezone.now() + timedelta(minutes=45))
    existing = getattr(bot, "paused_until", None)
    if existing:
        try:
            if existing > pause_until:
                return None
        except Exception:
            # Fall through and set a safe pause if comparison fails (e.g., naive vs aware).
            pass

    bot.paused_until = pause_until
    try:
        bot.save(update_fields=["paused_until"])
        logger.info(
            "[MarketHours] bot=%s paused_until=%s because %s",
            getattr(bot, "id", None),
            pause_until,
            status.reason,
        )
    except Exception:
        logger.exception(
            "[MarketHours] failed to set paused_until for bot=%s", getattr(bot, "id", None)
        )
    return pause_until
