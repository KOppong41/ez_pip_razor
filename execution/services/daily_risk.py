from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, time, timedelta, timezone as dt_timezone
from decimal import Decimal

from django.conf import settings
from django.db import transaction
from django.utils import timezone

from execution.models import AccountRiskDay, AccountSnapshot
from execution.services.timezones import get_broker_timezone


@dataclass(frozen=True)
class RiskDayWindow:
    risk_date: date
    start: datetime
    end: datetime


def _decimal(value, default="0") -> Decimal:
    try:
        return Decimal(str(value))
    except Exception:
        return Decimal(default)


def risk_day_window(broker_account, observed_at=None) -> RiskDayWindow:
    observed_at = observed_at or timezone.now()
    broker_tz = get_broker_timezone(broker_account)
    local_observed = timezone.localtime(observed_at, broker_tz)
    local_start = datetime.combine(local_observed.date(), time.min, tzinfo=broker_tz)
    local_end = local_start + timedelta(days=1)
    return RiskDayWindow(
        risk_date=local_observed.date(),
        start=local_start.astimezone(dt_timezone.utc),
        end=local_end.astimezone(dt_timezone.utc),
    )


def create_account_snapshot(broker_account, account_info) -> AccountSnapshot:
    return AccountSnapshot.objects.create(
        broker_account=broker_account,
        balance=_decimal(getattr(account_info, "balance", 0)),
        equity=_decimal(getattr(account_info, "equity", 0)),
        margin=_decimal(getattr(account_info, "margin", 0)),
        free_margin=_decimal(getattr(account_info, "margin_free", 0)),
        margin_level=_decimal(getattr(account_info, "margin_level", 0)),
        currency=str(getattr(account_info, "currency", "") or ""),
    )


def _economic_total(deals, *, trading_only=False) -> Decimal:
    fields = ("profit", "commission", "swap", "fee")
    return sum(
        (
            _decimal(getattr(deal, field, 0))
            for deal in deals
            if not trading_only or int(getattr(deal, "position_id", 0) or 0) != 0
            for field in fields
        ),
        Decimal("0"),
    )


def _epoch(value) -> datetime | None:
    try:
        raw = float(value)
    except (TypeError, ValueError):
        return None
    if raw <= 0:
        return None
    return datetime.fromtimestamp(raw, tz=dt_timezone.utc)


def _has_overnight_exposure(deals, broker_positions, day_start: datetime) -> bool:
    for position in broker_positions or ():
        opened_at = _epoch(getattr(position, "time", None))
        if opened_at is None or opened_at < day_start:
            return True

    # MT5 DEAL_ENTRY_IN=0, OUT=1, INOUT=2 and OUT_BY=3. An exit whose
    # position has no same-day entry proves that exposure crossed midnight.
    same_day_entries = {
        int(getattr(deal, "position_id", 0) or 0)
        for deal in deals
        if int(getattr(deal, "entry", -1)) == 0
    }
    for deal in deals:
        entry = int(getattr(deal, "entry", -1))
        position_id = int(getattr(deal, "position_id", 0) or 0)
        if entry in {1, 2, 3} and position_id and position_id not in same_day_entries:
            return True
    return False


def _reconstruct_from_mt5(
    broker_account,
    snapshot: AccountSnapshot,
    connector,
    window: RiskDayWindow,
    broker_positions,
):
    history_loader = getattr(connector, "history_deals_for_account", None)
    if not callable(history_loader):
        return None
    deals = tuple(
        history_loader(
            broker_account,
            window.start,
            min(snapshot.captured_at + timedelta(seconds=1), window.end),
        )
    )
    positions = broker_positions
    if positions is None:
        position_loader = getattr(connector, "positions_for_account", None)
        if not callable(position_loader):
            return None
        positions = tuple(position_loader(broker_account))
    balance_change = _economic_total(deals)
    realized_pnl = _economic_total(deals, trading_only=True)
    if _has_overnight_exposure(deals, positions, window.start):
        return {"realized_pnl": realized_pnl, "trustworthy": False}
    starting_balance = snapshot.balance - balance_change
    if starting_balance <= 0:
        return {"realized_pnl": realized_pnl, "trustworthy": False}
    return {
        "starting_balance": starting_balance,
        "starting_equity": starting_balance,
        "realized_pnl": realized_pnl,
        "source": "mt5_history",
        "trustworthy": True,
    }


@transaction.atomic
def update_account_risk_day(
    broker_account,
    snapshot: AccountSnapshot,
    *,
    connector=None,
    trade_mode=None,
    broker_positions=None,
) -> AccountRiskDay:
    """Create or update a broker-day baseline without ever moving it later.

    Late live-account starts are reconstructed from broker deal history only
    when there is no evidence that a position crossed the day boundary. If a
    trustworthy baseline cannot be established, the row remains unlocked so
    the entry risk gate can fail closed.
    """
    window = risk_day_window(broker_account, snapshot.captured_at)
    AccountRiskDay.objects.filter(
        broker_account=broker_account,
        risk_date__lt=window.risk_date,
        finalized_at__isnull=True,
    ).update(finalized_at=snapshot.captured_at)

    risk_day, _ = AccountRiskDay.objects.select_for_update().get_or_create(
        broker_account=broker_account,
        risk_date=window.risk_date,
        defaults={
            "first_snapshot_at": snapshot.captured_at,
            "high_equity": snapshot.equity,
        },
    )
    changed = []
    if risk_day.first_snapshot_at is None:
        risk_day.first_snapshot_at = snapshot.captured_at
        changed.append("first_snapshot_at")
    if snapshot.equity > risk_day.high_equity:
        risk_day.high_equity = snapshot.equity
        changed.append("high_equity")

    reconstruction = None
    if connector is not None:
        try:
            reconstruction = _reconstruct_from_mt5(
                broker_account,
                snapshot,
                connector,
                window,
                broker_positions,
            )
        except Exception:
            reconstruction = None
    if reconstruction is not None:
        realized_pnl = reconstruction["realized_pnl"]
        if realized_pnl != risk_day.realized_pnl:
            risk_day.realized_pnl = realized_pnl
            changed.append("realized_pnl")

    if not risk_day.baseline_locked:
        if reconstruction and reconstruction.get("trustworthy"):
            risk_day.starting_balance = reconstruction["starting_balance"]
            risk_day.starting_equity = reconstruction["starting_equity"]
            risk_day.baseline_source = reconstruction["source"]
            risk_day.baseline_locked = True
            changed.extend(
                [
                    "starting_balance",
                    "starting_equity",
                    "baseline_source",
                    "baseline_locked",
                ]
            )
        else:
            seconds_after_open = (snapshot.captured_at - window.start).total_seconds()
            grace_seconds = int(
                getattr(settings, "ACCOUNT_RISK_OPENING_SNAPSHOT_GRACE_SECONDS", 300)
            )
            if 0 <= seconds_after_open <= grace_seconds and snapshot.equity > 0:
                risk_day.starting_balance = snapshot.balance
                risk_day.starting_equity = snapshot.equity
                risk_day.baseline_source = "opening_snapshot"
                risk_day.baseline_locked = True
                changed.extend(
                    [
                        "starting_balance",
                        "starting_equity",
                        "baseline_source",
                        "baseline_locked",
                    ]
                )
            elif trade_mode in {0, 1} and snapshot.equity > 0:
                risk_day.starting_balance = snapshot.balance
                risk_day.starting_equity = snapshot.equity
                risk_day.baseline_source = "demo_first_snapshot"
                risk_day.baseline_locked = True
                changed.extend(
                    [
                        "starting_balance",
                        "starting_equity",
                        "baseline_source",
                        "baseline_locked",
                    ]
                )
            elif risk_day.baseline_source != "unavailable":
                risk_day.baseline_source = "unavailable"
                changed.append("baseline_source")

    if changed:
        risk_day.save(update_fields=list(dict.fromkeys(changed + ["updated_at"])))
    return risk_day


def daily_equity_change_pcts(risk_day: AccountRiskDay, equity) -> tuple[Decimal, Decimal]:
    start = risk_day.starting_equity
    current = _decimal(equity)
    if not risk_day.baseline_locked or start is None or start <= 0:
        raise ValueError("Daily risk baseline is unavailable")
    loss = (start - current) / start * Decimal("100")
    profit = (current - start) / start * Decimal("100")
    return loss, profit
