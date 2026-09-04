
from __future__ import annotations
from decimal import Decimal, InvalidOperation
from datetime import datetime, timedelta, timezone as dt_timezone
import hashlib
import json
import threading
import logging
import time
from execution.services.portfolio import record_fill
from django.db import transaction
from django.db.models import Max
from django.utils import timezone
from execution.models import BrokerPosition, ExecutionAttempt, Order
from execution.services.orchestrator import update_order_status
from execution.services.journal import log_journal_event
from .base import BaseConnector, ConnectorError
from django.conf import settings

from core.metrics import mt5_errors_total
from execution.services.runtime_config import get_runtime_config
from execution.services.mt5_session import MT5SessionService
from execution.services.latency import mark_execution_recorded, mark_order_timestamp

try:
    import MetaTrader5 as _mt5_module  # type: ignore
except Exception:
    _mt5_module = None


def is_mt5_available() -> bool:
    return _mt5_module is not None


def _timed_order_send(order: Order, request):
    mark_order_timestamp(order, "order_send_called_at")
    result = mt5.order_send(request)
    if result is not None:
        mark_order_timestamp(order, "broker_response_received_at")
    return result


class _MT5Proxy:
    """
    Lightweight proxy so the rest of the code can reference `mt5` even when the
    MetaTrader5 package is not installed (e.g., in Docker/Linux environments).
    """

    def __getattr__(self, item):
        # Python tooling probes private protocol attributes (for example
        # ``__func__`` and ``_is_coroutine`` when unittest.mock determines
        # whether an object is async). Missing protocol attributes must follow
        # normal attribute semantics.
        if item.startswith("_"):
            raise AttributeError(item)
        if _mt5_module is None:
            raise ConnectorError(
                "MetaTrader5 Python package is not installed. "
                "Install it on the host MT5 terminal machine to enable live trading."
            )
        return getattr(_mt5_module, item)

    def __bool__(self):
        return is_mt5_available()


mt5 = _MT5Proxy()

logger = logging.getLogger(__name__)


def _coerce_ticket(value):
    if value in (None, "", 0):
        return None
    try:
        return int(Decimal(str(value)))
    except (InvalidOperation, ValueError, TypeError):
        try:
            return int(value)
        except Exception:
            return None


def _maybe_store_broker_ticket(order: Order, result) -> None:
    """
    Persist the MT5 order/deal ticket for downstream reporting.
    """
    if result is None or order is None:
        return

    values = {
        "broker_order_ticket": _coerce_ticket(getattr(result, "order", None)),
        "broker_deal_ticket": _coerce_ticket(getattr(result, "deal", None)),
        "broker_position_ticket": _coerce_ticket(getattr(result, "position", None)),
    }
    updates = []
    for field, value in values.items():
        if value and getattr(order, field, None) != value:
            setattr(order, field, value)
            updates.append(field)
    legacy_ticket = values["broker_order_ticket"] or values["broker_deal_ticket"] or values["broker_position_ticket"]
    if legacy_ticket and order.broker_ticket != legacy_ticket:
        order.broker_ticket = legacy_ticket
        updates.append("broker_ticket")
    if updates:
        try:
            order.save(update_fields=updates)
        except Exception:
            logger.warning("[MT5] unable to persist broker identifiers for order %s", order.id, exc_info=True)


def _safe_mt5_metadata(value) -> dict:
    if value is None:
        return {}
    if hasattr(value, "_asdict"):
        raw = value._asdict()
    elif isinstance(value, dict):
        raw = value
    else:
        return {"value": str(value)}
    blocked = {"password", "authorization", "token", "secret"}
    return {
        str(key): item
        for key, item in raw.items()
        if str(key).lower() not in blocked and isinstance(item, (str, int, float, bool, type(None)))
    }


def _request_fingerprint(request: dict) -> str:
    safe = {
        key: value
        for key, value in request.items()
        if key not in {"password"}
    }
    encoded = json.dumps(safe, sort_keys=True, default=str, separators=(",", ":")).encode()
    return hashlib.sha256(encoded).hexdigest()

# MT5 only allows one login per process. We'll keep a singleton session.
class _MT5Session:
    _lock = threading.RLock()
    _active_login = None
    _active_server = None
    _initialized = False

    @classmethod
    def ensure_login(cls, *, path: str, login: int, password: str, server: str, allow_switch: bool = True):
        """
        Ensure MT5 is initialized and logged into the given account.

        - First call: initialize + login using the provided credentials.
        - Subsequent calls with the same login: do nothing.
        - If login changes: call mt5.login(...) again.
        """
        log_ctx = {"login": login, "server": server}
        with cls._lock:
            # If we think we're initialized but MT5 IPC is gone, reset and re-init.
            if cls._initialized:
                try:
                    term = mt5.terminal_info()
                    acct = mt5.account_info()
                    if term is None or acct is None:
                        logger.warning("[MT5] session stale; reinitializing %s", log_ctx)
                        mt5.shutdown()
                        cls._initialized = False
                        cls._active_login = None
                        cls._active_server = None
                except Exception:
                    mt5.shutdown()
                    cls._initialized = False
                    cls._active_login = None
                    cls._active_server = None

            # First time: initialize + login in one shot
            if not cls._initialized:
                try:
                    mt5.shutdown()  # clear stale IPC before initializing
                    if not mt5.initialize(
                        path=path,
                        login=login,
                        password=password,
                        server=server,
                    ):
                        raise ConnectorError(f"MT5 initialize failed: {mt5.last_error()}")
                    if mt5.terminal_info() is None:
                        raise ConnectorError("MT5 terminal_info unavailable after initialize")
                except ConnectorError as e:
                    if "No IPC connection" in str(e) or "-10004" in str(e):
                        logger.warning("[MT5] init retry after IPC error %s", log_ctx)
                        mt5.shutdown()
                        if not mt5.initialize(
                            path=path,
                            login=login,
                            password=password,
                            server=server,
                        ):
                            raise ConnectorError(f"MT5 initialize failed: {mt5.last_error()}")
                        if mt5.terminal_info() is None:
                            raise ConnectorError("MT5 terminal_info unavailable after initialize")
                    else:
                        raise
                cls._initialized = True
                cls._active_login = login
                cls._active_server = server
                return

            # Already initialized with this login – nothing to do
            if cls._active_login == login and cls._active_server == server:
                return

            # If MT5 is already logged into the requested account (even if _active_* is stale), skip relogin.
            try:
                acct_info = mt5.account_info()
                if acct_info and getattr(acct_info, "login", None) == login and getattr(acct_info, "server", None) == server:
                    cls._active_login = login
                    cls._active_server = server
                    return
            except Exception:
                pass

            # Initialized but different account or server → relogin (unless disallowed)
            if not allow_switch:
                raise ConnectorError(
                    f"MT5 already logged into login={cls._active_login} server={cls._active_server}; refusing to switch"
                )
            try:
                if not mt5.login(login=login, password=password, server=server):
                    raise ConnectorError(f"MT5 login failed: {mt5.last_error()}")
            except ConnectorError as e:
                if "No IPC connection" in str(e) or "-10004" in str(e):
                    logger.warning("[MT5] login IPC reset %s", log_ctx)
                    mt5.shutdown()
                    cls._initialized = False
                    cls._active_login = None
                    cls._active_server = None
                    return cls.ensure_login(path=path, login=login, password=password, server=server)
                raise
            cls._active_login = login
            cls._active_server = server

    @classmethod
    def account_equity(cls) -> Decimal:
        info = mt5.account_info()
        if info is None:
            raise ConnectorError(f"MT5 account_info failed: {mt5.last_error()}")
        return Decimal(str(info.equity))


# Keep the compatibility name used by existing tests while delegating active
# session ownership to the dedicated service module.
_MT5Session = MT5SessionService
_MT5Session.configure_api(lambda: mt5)


def _check_ready(symbol: str):
    term = mt5.terminal_info()
    acct = mt5.account_info()
    if not term:
        raise ConnectorError("MT5 not initialized or terminal not found")
    if not acct:
        raise ConnectorError("MT5 not logged in to a trading account")

    if hasattr(term, "trade_allowed") and not term.trade_allowed:
        raise ConnectorError("MT5 terminal: trading disabled (enable Algo Trading in toolbar & Options>Expert Advisors)")

    if hasattr(acct, "trade_allowed") and not acct.trade_allowed:
        raise ConnectorError("Account trading not allowed (check account permissions)")

    sinfo = mt5.symbol_info(symbol)
    if not sinfo or not sinfo.visible:
        mt5.symbol_select(symbol, True)
        sinfo = mt5.symbol_info(symbol)
    if not sinfo or getattr(sinfo, "trade_mode", 0) == 0:
        raise ConnectorError(f"Symbol not tradable or not visible: {symbol}")


class MT5Connector(BaseConnector):
    broker_code = "mt5"
    _account_locks = {}
    _failure_counts = {}
    _circuit_threshold = 3
    _circuit_cooldown_sec = 300

    @classmethod
    def _account_key(cls, login: int | None, server: str | None) -> str:
        return f"{login}:{server}"

    @classmethod
    def _get_account_lock(cls, key: str):
        if key not in cls._account_locks:
            cls._account_locks[key] = threading.RLock()
        return cls._account_locks[key]

    @classmethod
    def _record_failure(cls, key: str, action: str):
        mt5_errors_total.labels(action=action).inc()
        now = time.monotonic()
        count, _ts = cls._failure_counts.get(key, (0, now))
        cls._failure_counts[key] = (count + 1, now)

    @classmethod
    def _reset_failure(cls, key: str):
        if key in cls._failure_counts:
            del cls._failure_counts[key]

    @classmethod
    def _circuit_open(cls, key: str) -> bool:
        if key not in cls._failure_counts:
            return False
        count, ts = cls._failure_counts[key]
        if count < cls._circuit_threshold:
            return False
        if time.monotonic() - ts < cls._circuit_cooldown_sec:
            return True
        # cooldown passed, reset
        del cls._failure_counts[key]
        return False

    def login_for_account(self, broker_account) -> bool:
        """
        Public login helper used by admin/balance fetchers.
        Relies on stored MT5 creds (login/server/path/password).
        """
        creds = broker_account.get_creds()
        self._login_from_creds(creds)
        return True

    def _login_from_creds(self, creds: dict, *, allow_switch: bool = True):
        login = int(creds.get("login"))
        password = creds.get("password")
        server = creds.get("server")
        raw_path = creds.get("path")  # terminal64.exe
        if not all([login, password, server]):
            raise ConnectorError("Missing MT5 creds: need login, password, server, path")

        # Resolve terminal path; if stored path is missing, try common defaults.
        path = raw_path
        try:
            from pathlib import Path

            def _resolve_mt5_path(candidate: str | None) -> str | None:
                if candidate:
                    p = Path(candidate)
                    if p.exists():
                        return str(p)
                return None

            resolved = _resolve_mt5_path(raw_path)
            if not resolved:
                fallback_paths = [
                    r"C:\Program Files\MetaTrader 5\terminal64.exe",
                    r"C:\Program Files (x86)\MetaTrader 5\terminal64.exe",
                ]
                for fp in fallback_paths:
                    resolved = _resolve_mt5_path(fp)
                    if resolved:
                        logger.warning(
                            "[MT5] using fallback terminal path %s (stored path missing: %s)",
                            resolved,
                            raw_path,
                        )
                        break
            path = resolved or raw_path
            if not path:
                raise ConnectorError("MT5 terminal path not found (no valid paths discovered)")
            if not Path(path).exists():
                raise ConnectorError(f"MT5 terminal path not found: {path}")
        except ConnectorError:
            raise
        except Exception:
            # If pathlib fails for any reason, keep original behavior.
            pass

        key = self._account_key(login, server)
        if self._circuit_open(key):
            raise ConnectorError(f"MT5 circuit open for login={login} server={server}")

        lock = self._get_account_lock(key)
        with lock:
            try:
                _MT5Session.ensure_login(
                    path=path,
                    login=login,
                    password=password,
                    server=server,
                    allow_switch=allow_switch,
                )
                self._reset_failure(key)
            except ConnectorError as e:
                # Record and re-raise; caller decides retry/skip
                action = "login"
                if "initialize" in str(e).lower():
                    action = "initialize"
                elif "IPC" in str(e) or "-10004" in str(e):
                    action = "ipc"
                self._record_failure(key, action)
                raise

    def _ensure_symbol(self, symbol: str):
        # Make sure symbol is selected in Market Watch
        if not mt5.symbol_select(symbol, True):
            mt5_errors_total.labels(action="symbol_select").inc()
            raise ConnectorError(f"MT5 symbol_select failed for {symbol}: {mt5.last_error()}")

    def _login_from_order(self, order: Order):
        creds = order.broker_account.get_creds()
        self._login_from_creds(creds)

    def check_health(self, creds: dict, symbol: str):
        """Lightweight connectivity check: login + symbol select + ready check."""
        with _MT5Session.serialized():
            try:
                self._login_from_creds(creds, allow_switch=False)
                self._ensure_symbol(symbol)
                _check_ready(symbol)
            except ConnectorError as e:
                if "No IPC connection" in str(e) or "-10004" in str(e):
                    _MT5Session.reset(reason="health_check_ipc_failure")
                    self._login_from_creds(creds, allow_switch=False)
                    self._ensure_symbol(symbol)
                    _check_ready(symbol)
                else:
                    raise

    def _call_for_account(self, broker_account, operation, *, allow_switch: bool = True):
        """Run one account-scoped MT5 command while holding session ownership."""
        with _MT5Session.serialized():
            creds = broker_account.get_mt5_creds()
            self._login_from_creds(creds, allow_switch=allow_switch)
            info = mt5.account_info()
            expected_login = int(creds.get("login"))
            expected_server = str(creds.get("server"))
            if info is None:
                raise ConnectorError(f"MT5 account_info failed: {mt5.last_error()}")
            actual_login = int(getattr(info, "login", expected_login) or 0)
            actual_server = str(getattr(info, "server", expected_server) or "")
            if actual_login != expected_login or actual_server != expected_server:
                raise ConnectorError("MT5 active account changed during broker operation")
            return operation()

    def account_info_for_account(self, broker_account):
        return self._call_for_account(broker_account, lambda: mt5.account_info())

    def symbol_info_for_account(self, broker_account, symbol: str):
        def operation():
            info = mt5.symbol_info(symbol)
            if info is None or not getattr(info, "visible", False):
                if not mt5.symbol_select(symbol, True):
                    raise ConnectorError(f"MT5 symbol_select failed for {symbol}: {mt5.last_error()}")
                info = mt5.symbol_info(symbol)
            if info is None:
                raise ConnectorError(f"MT5 symbol_info failed for {symbol}: {mt5.last_error()}")
            return info

        return self._call_for_account(broker_account, operation)

    def tick_for_account(self, broker_account, symbol: str):
        def operation():
            if not mt5.symbol_select(symbol, True):
                raise ConnectorError(f"MT5 symbol_select failed for {symbol}: {mt5.last_error()}")
            tick = mt5.symbol_info_tick(symbol)
            if tick is None:
                raise ConnectorError(f"MT5 tick unavailable for {symbol}: {mt5.last_error()}")
            return tick

        return self._call_for_account(broker_account, operation)

    def rates_for_account(self, broker_account, symbol: str, timeframe_constant, start_pos: int, count: int):
        def operation():
            resolved_timeframe = (
                getattr(mt5, timeframe_constant)
                if isinstance(timeframe_constant, str)
                else timeframe_constant
            )
            if not mt5.symbol_select(symbol, True):
                raise ConnectorError(f"MT5 symbol_select failed for {symbol}: {mt5.last_error()}")
            rates = mt5.copy_rates_from_pos(symbol, resolved_timeframe, start_pos, count)
            if rates is None:
                raise ConnectorError(f"MT5 rates unavailable for {symbol}: {mt5.last_error()}")
            return rates

        return self._call_for_account(broker_account, operation)

    def positions_for_account(self, broker_account, *, symbol: str | None = None, ticket: int | None = None):
        def operation():
            if ticket is not None:
                result = mt5.positions_get(ticket=int(ticket))
            elif symbol is not None:
                result = mt5.positions_get(symbol=symbol)
            else:
                result = mt5.positions_get()
            if result is None:
                raise ConnectorError(f"MT5 positions_get failed: {mt5.last_error()}")
            return result

        return self._call_for_account(broker_account, operation)

    def orders_for_account(self, broker_account, **filters):
        def operation():
            result = mt5.orders_get(**filters)
            if result is None:
                raise ConnectorError(f"MT5 orders_get failed: {mt5.last_error()}")
            return result

        return self._call_for_account(broker_account, operation)

    def history_orders_for_account(self, broker_account, date_from, date_to, **filters):
        return self._call_for_account(
            broker_account,
            lambda: mt5.history_orders_get(date_from, date_to, **filters) or (),
        )

    def history_deals_for_account(self, broker_account, date_from, date_to, **filters):
        def operation():
            result = mt5.history_deals_get(date_from, date_to, **filters)
            if result is None:
                raise ConnectorError(f"MT5 history_deals_get failed: {mt5.last_error()}")
            return result

        return self._call_for_account(broker_account, operation)

    def history_deals_for_position_account(self, broker_account, broker_position_ticket: int):
        """Load a position's complete deal chain without relying on broker clock alignment."""
        return self._call_for_account(
            broker_account,
            lambda: mt5.history_deals_get(position=int(broker_position_ticket)) or (),
        )

    def calc_profit_for_account(self, broker_account, side: str, symbol: str, volume, open_price, close_price):
        def operation():
            order_type = mt5.ORDER_TYPE_BUY if side == "buy" else mt5.ORDER_TYPE_SELL
            value = mt5.order_calc_profit(order_type, symbol, float(volume), float(open_price), float(close_price))
            if value is None:
                raise ConnectorError(f"MT5 order_calc_profit failed: {mt5.last_error()}")
            return Decimal(str(value))

        return self._call_for_account(broker_account, operation)

    def calc_margin_for_account(self, broker_account, side: str, symbol: str, volume, price):
        def operation():
            order_type = mt5.ORDER_TYPE_BUY if side == "buy" else mt5.ORDER_TYPE_SELL
            value = mt5.order_calc_margin(order_type, symbol, float(volume), float(price))
            if value is None:
                raise ConnectorError(f"MT5 order_calc_margin failed: {mt5.last_error()}")
            return Decimal(str(value))

        return self._call_for_account(broker_account, operation)

    def resolve_symbol_for_account(self, broker_account, canonical: str) -> str:
        canonical = str(canonical or "").upper()
        if not canonical:
            raise ConnectorError("Canonical symbol is required")

        def operation():
            exact = mt5.symbol_info(canonical)
            if exact is not None:
                mt5.symbol_select(canonical, True)
                return canonical
            symbols = mt5.symbols_get() or ()
            matches = []
            for item in symbols:
                name = str(getattr(item, "name", ""))
                normalized = name.upper()
                if normalized == canonical:
                    return name
                if normalized.startswith(canonical):
                    matches.append(name)
            if not matches:
                raise ConnectorError(f"No MT5 symbol resolves canonical instrument {canonical}")
            matches.sort(key=lambda value: (len(value), value))
            resolved = matches[0]
            if not mt5.symbol_select(resolved, True):
                raise ConnectorError(f"Unable to select resolved MT5 symbol {resolved}")
            return resolved

        return self._call_for_account(broker_account, operation)

    def modify_broker_position(self, broker_position: BrokerPosition, *, sl=None, tp=None) -> BrokerPosition:
        if not broker_position.is_manageable:
            raise ConnectorError("Refusing to modify a manual or unknown MT5 position")

        def operation():
            current_rows = mt5.positions_get(ticket=int(broker_position.broker_position_ticket)) or ()
            if not current_rows:
                raise ConnectorError("MT5 position ticket is no longer open")
            current = current_rows[0]
            requested_sl = Decimal(str(sl)) if sl is not None else Decimal(str(getattr(current, "sl", 0) or 0))
            requested_tp = Decimal(str(tp)) if tp is not None else Decimal(str(getattr(current, "tp", 0) or 0))
            request = {
                "action": mt5.TRADE_ACTION_SLTP,
                "symbol": broker_position.symbol,
                "position": int(broker_position.broker_position_ticket),
                "sl": float(requested_sl),
                "tp": float(requested_tp),
                "magic": int(getattr(settings, "MT5_MAGIC_NUMBER", 20250813)),
                "comment": f"ezp:{broker_position.broker_position_ticket}"[:31],
            }
            result = mt5.order_send(request)
            if result is None:
                raise ConnectorError("MT5 protection modification outcome is ambiguous")
            if getattr(result, "retcode", None) != mt5.TRADE_RETCODE_DONE:
                raise ConnectorError(
                    f"MT5 protection modification rejected: retcode={getattr(result, 'retcode', None)}"
                )
            confirmed_rows = mt5.positions_get(ticket=int(broker_position.broker_position_ticket)) or ()
            if not confirmed_rows:
                raise ConnectorError("MT5 position disappeared before protection confirmation")
            confirmed = confirmed_rows[0]
            confirmed_sl = Decimal(str(getattr(confirmed, "sl", 0) or 0))
            confirmed_tp = Decimal(str(getattr(confirmed, "tp", 0) or 0))
            tolerance = Decimal(str(getattr(mt5.symbol_info(broker_position.symbol), "point", 0) or 0))
            if sl is not None and abs(confirmed_sl - requested_sl) > tolerance:
                raise ConnectorError("MT5 did not confirm the requested stop loss")
            if tp is not None and abs(confirmed_tp - requested_tp) > tolerance:
                raise ConnectorError("MT5 did not confirm the requested take profit")
            broker_position.sl = confirmed_sl if confirmed_sl > 0 else None
            broker_position.tp = confirmed_tp if confirmed_tp > 0 else None
            broker_position.current_price = Decimal(str(getattr(confirmed, "price_current", 0) or 0))
            broker_position.profit = Decimal(str(getattr(confirmed, "profit", 0) or 0))
            broker_position.last_reconciled_at = timezone.now()
            broker_position.broker_metadata = _safe_mt5_metadata(confirmed)
            broker_position.save(
                update_fields=[
                    "sl",
                    "tp",
                    "current_price",
                    "profit",
                    "last_reconciled_at",
                    "broker_metadata",
                ]
            )
            log_journal_event(
                "position.protection_modified",
                broker_account=broker_position.broker_account,
                symbol=broker_position.symbol,
                message=f"MT5 confirmed protection for ticket {broker_position.broker_position_ticket}",
                context={
                    "broker_position_ticket": broker_position.broker_position_ticket,
                    "sl": str(broker_position.sl) if broker_position.sl is not None else None,
                    "tp": str(broker_position.tp) if broker_position.tp is not None else None,
                },
            )
            return broker_position

        return self._call_for_account(broker_position.broker_account, operation, allow_switch=False)

    @staticmethod
    def _order_comment(order: Order, *, closing: bool = False) -> str:
        prefix = "ezc:" if closing else "ez:"
        return f"{prefix}{order.client_order_id}"[:31]

    @staticmethod
    def _filling_mode(symbol_info):
        configured = getattr(symbol_info, "filling_mode", None)
        valid = {
            getattr(mt5, "ORDER_FILLING_FOK", None),
            getattr(mt5, "ORDER_FILLING_IOC", None),
            getattr(mt5, "ORDER_FILLING_RETURN", None),
        }
        if configured in valid:
            return configured
        for name in ("ORDER_FILLING_FOK", "ORDER_FILLING_IOC", "ORDER_FILLING_RETURN"):
            value = getattr(mt5, name, None)
            if value is not None:
                return value
        raise ConnectorError("MT5 filling mode is unavailable")

    @staticmethod
    def _deal_time(value):
        raw = getattr(value, "time", None)
        if not raw:
            return timezone.now()
        return datetime.fromtimestamp(float(raw), tz=dt_timezone.utc)

    def _sync_broker_position(
        self,
        order: Order | None,
        raw_position,
        *,
        broker_account=None,
        ownership: str | None = None,
    ):
        ticket = _coerce_ticket(
            getattr(raw_position, "ticket", None) or getattr(raw_position, "identifier", None)
        )
        if ticket is None:
            return None
        account = order.broker_account if order is not None else broker_account
        if account is None:
            raise ConnectorError("Broker account is required to synchronize a position")
        magic = _coerce_ticket(getattr(raw_position, "magic", None))
        comment = str(getattr(raw_position, "comment", "") or "")
        configured_magic = int(getattr(settings, "MT5_MAGIC_NUMBER", 20250813))
        inferred = "ez_trade" if magic == configured_magic and comment.startswith(("ez:", "ezc:")) else "external"
        side = (
            "buy"
            if getattr(raw_position, "type", None) == getattr(mt5, "POSITION_TYPE_BUY", 0)
            else "sell"
        )
        opened_at = None
        if getattr(raw_position, "time", None):
            opened_at = datetime.fromtimestamp(float(raw_position.time), tz=dt_timezone.utc)
        position, _ = BrokerPosition.objects.update_or_create(
            broker_account=account,
            broker_position_ticket=ticket,
            defaults={
                "owner": account.owner,
                "bot": order.bot if order is not None and (ownership or inferred) == "ez_trade" else None,
                "originating_order": order if order is not None and (ownership or inferred) == "ez_trade" else None,
                "ownership": ownership or inferred,
                "symbol": str(getattr(raw_position, "symbol", order.symbol if order else "")),
                "side": side,
                "volume": Decimal(str(getattr(raw_position, "volume", 0) or 0)),
                "open_price": Decimal(str(getattr(raw_position, "price_open", 0) or 0)),
                "current_price": Decimal(str(getattr(raw_position, "price_current", 0) or 0)),
                "sl": Decimal(str(raw_position.sl)) if getattr(raw_position, "sl", None) else None,
                "tp": Decimal(str(raw_position.tp)) if getattr(raw_position, "tp", None) else None,
                "profit": Decimal(str(getattr(raw_position, "profit", 0) or 0)),
                "swap": Decimal(str(getattr(raw_position, "swap", 0) or 0)),
                "magic": magic,
                "comment": comment,
                "status": "open",
                "opened_at": opened_at,
                "last_reconciled_at": timezone.now(),
                "broker_metadata": _safe_mt5_metadata(raw_position),
            },
        )
        return position

    def _sync_broker_exposure_snapshot(self, broker_account, raw_positions) -> None:
        """Persist every position returned by the just-read broker snapshot."""
        configured_magic = int(getattr(settings, "MT5_MAGIC_NUMBER", 20250813))
        for raw_position in raw_positions:
            ticket = _coerce_ticket(
                getattr(raw_position, "ticket", None)
                or getattr(raw_position, "identifier", None)
            )
            if ticket is None:
                raise ConnectorError("MT5 returned a position without a ticket")
            order = (
                Order.objects.filter(
                    broker_account=broker_account,
                    broker_position_ticket=ticket,
                )
                .order_by("-created_at")
                .first()
            )
            magic = int(getattr(raw_position, "magic", 0) or 0)
            comment = str(getattr(raw_position, "comment", "") or "")
            is_owned = bool(
                order
                or (
                    magic == configured_magic
                    and comment.startswith(("ez:", "ezc:"))
                )
            )
            ownership = (
                "ez_trade"
                if is_owned
                else ("manual" if not comment else "external")
            )
            self._sync_broker_position(
                order,
                raw_position,
                broker_account=broker_account,
                ownership=ownership,
            )

    def _matching_broker_records(self, order: Order):
        comment = self._order_comment(order, closing=order.is_exit)
        date_from = (order.submitted_at or order.created_at or timezone.now()) - timedelta(minutes=5)
        date_to = timezone.now() + timedelta(minutes=1)
        deals = mt5.history_deals_get(date_from, date_to) or ()
        active_orders = mt5.orders_get(symbol=order.symbol) or ()
        positions = mt5.positions_get(symbol=order.symbol) or ()

        def matches(item):
            item_comment = str(getattr(item, "comment", "") or "")
            if item_comment == comment:
                return True
            ticket_values = {
                _coerce_ticket(getattr(item, "ticket", None)),
                _coerce_ticket(getattr(item, "order", None)),
                _coerce_ticket(getattr(item, "position_id", None)),
                _coerce_ticket(getattr(item, "identifier", None)),
            }
            expected = {
                order.broker_order_ticket,
                order.broker_deal_ticket,
                order.broker_position_ticket,
            }
            return bool({value for value in ticket_values if value} & {value for value in expected if value})

        return (
            [item for item in deals if matches(item)],
            [item for item in active_orders if matches(item)],
            [item for item in positions if matches(item)],
        )

    def reconcile_order(self, order: Order) -> bool:
        """Resolve an acknowledged/ambiguous order from broker history.

        Returns True when a broker record exists. A False result never causes
        an automatic resend of an ambiguous submission.
        """
        with _MT5Session.serialized():
            self._login_from_order(order)
            deals, active_orders, positions = self._matching_broker_records(order)

            if active_orders:
                broker_order = active_orders[-1]
                order.broker_order_ticket = _coerce_ticket(getattr(broker_order, "ticket", None))
                order.broker_response = _safe_mt5_metadata(broker_order)
                order.save(update_fields=["broker_order_ticket", "broker_response"])
                if order.status == "new":
                    update_order_status(order, "ack")
                return True

            if deals:
                from execution.services.portfolio import record_fill

                total_filled = Decimal("0")
                weighted = Decimal("0")
                for deal in deals:
                    deal_qty = Decimal(str(getattr(deal, "volume", 0) or 0))
                    deal_price = Decimal(str(getattr(deal, "price", 0) or 0))
                    if deal_qty <= 0:
                        continue
                    total_filled += deal_qty
                    weighted += deal_qty * deal_price
                    record_fill(
                        order,
                        deal_qty,
                        deal_price,
                        broker_order_ticket=_coerce_ticket(getattr(deal, "order", None)),
                        broker_deal_ticket=_coerce_ticket(getattr(deal, "ticket", None)),
                        broker_position_ticket=_coerce_ticket(getattr(deal, "position_id", None)),
                        broker_profit=Decimal(str(getattr(deal, "profit", 0) or 0)),
                        commission=Decimal(str(getattr(deal, "commission", 0) or 0)),
                        swap=Decimal(str(getattr(deal, "swap", 0) or 0)),
                        broker_metadata=_safe_mt5_metadata(deal),
                    )
                if total_filled <= 0:
                    return False
                avg_price = weighted / total_filled
                order.filled_qty = min(order.qty, total_filled)
                order.remaining_qty = max(Decimal("0"), order.qty - order.filled_qty)
                last = deals[-1]
                order.broker_order_ticket = _coerce_ticket(getattr(last, "order", None))
                order.broker_deal_ticket = _coerce_ticket(getattr(last, "ticket", None))
                order.broker_position_ticket = _coerce_ticket(getattr(last, "position_id", None))
                order.actual_fill_price = avg_price
                order.broker_response = _safe_mt5_metadata(last)
                order.save(
                    update_fields=[
                        "filled_qty",
                        "remaining_qty",
                        "broker_order_ticket",
                        "broker_deal_ticket",
                        "broker_position_ticket",
                        "actual_fill_price",
                        "broker_response",
                    ]
                )
                target = "filled" if order.remaining_qty == 0 else "part_filled"
                update_order_status(order, target, price=avg_price)
                for raw_position in positions:
                    self._sync_broker_position(order, raw_position, ownership="ez_trade")
                ExecutionAttempt.objects.filter(
                    order=order,
                    status__in=["submitting", "ambiguous"],
                ).update(status="reconciled", resolved_at=timezone.now())
                return True

            if positions:
                for raw_position in positions:
                    self._sync_broker_position(order, raw_position, ownership="ez_trade")
                raw_position = positions[-1]
                volume = Decimal(str(getattr(raw_position, "volume", order.qty) or order.qty))
                price = Decimal(str(getattr(raw_position, "price_open", order.price or 0) or 0))
                order.filled_qty = min(order.qty, volume)
                order.remaining_qty = max(Decimal("0"), order.qty - order.filled_qty)
                order.broker_position_ticket = _coerce_ticket(getattr(raw_position, "ticket", None))
                order.save(update_fields=["filled_qty", "remaining_qty", "broker_position_ticket"])
                update_order_status(
                    order,
                    "filled" if order.remaining_qty == 0 else "part_filled",
                    price=price,
                )
                return True

            return False

    def place_order(self, order: Order) -> None:
        with _MT5Session.serialized():
            return self._place_order_serialized(order)

    def _place_order_serialized(self, order: Order) -> None:
        """
        Market order flow:
        1) login + ensure symbol + terminal sanity
        2) mark ACK (submitted)
        3) send DEAL (buy/sell)
        4) on DONE/DONE_PARTIAL => mark filled + log Execution (+ balance)
            on PLACED            => keep as ack
            else                 => mark error + raise
        """
        runtime_cfg = get_runtime_config()
        # Normalize quantity to Decimal to avoid str/Decimal comparison errors.
        qty_dec = Decimal(str(order.qty))
        # 1) Ensure MT5 session & symbol
        self._login_from_order(order)
        try:
            self._ensure_symbol(order.symbol)
            _check_ready(order.symbol)
        except ConnectorError as e:
            # MT5 occasionally drops IPC; try a one-time re-init/login and retry symbol select.
            if "No IPC connection" in str(e) or "-10004" in str(e):
                mt5.shutdown()
                _MT5Session._initialized = False
                self._login_from_order(order)
                self._ensure_symbol(order.symbol)
                _check_ready(order.symbol)
            else:
                raise ConnectorError(f"order {order.id}: {e}") from e

        order.refresh_from_db()
        if order.status == "filled":
            return
        if order.status in {"canceled", "rejected", "error"}:
            raise ConnectorError(f"Order {order.id} is terminal ({order.status})")
        unresolved_attempt = order.attempts.filter(status__in=["submitting", "ambiguous"]).exists()
        if order.status == "ack" or unresolved_attempt:
            if self.reconcile_order(order):
                return
            raise ConnectorError(
                f"Order {order.id} has an ambiguous broker submission; automatic resend blocked"
            )

        # Exit intent is explicit. Never infer an exit merely because SL/TP is absent.
        is_close_order = order.is_exit
        if is_close_order:
            ticket = order.broker_position_ticket
            if not ticket:
                update_order_status(order, "rejected", error_msg="Exit order missing broker position ticket")
                raise ConnectorError("Exit order requires a broker position ticket")
            owned = BrokerPosition.objects.filter(
                broker_account=order.broker_account,
                broker_position_ticket=ticket,
                ownership="ez_trade",
                status="open",
            ).first()
            if owned is None:
                update_order_status(order, "rejected", error_msg="Position is manual, external, or not open")
                raise ConnectorError("Refusing to close a manual or unknown MT5 position")

            positions = mt5.positions_get(ticket=int(ticket))
            if not positions:
                if self.reconcile_order(order):
                    return
                update_order_status(order, "error", error_msg="Broker position ticket not found during close")
                raise ConnectorError("Broker position ticket not found during close reconciliation")

            pos = positions[0]
            available = Decimal(str(getattr(pos, "volume", 0) or 0))
            requested = min(Decimal(str(order.remaining_qty or order.qty)), available)
            if requested <= 0:
                raise ConnectorError("Broker position has no closeable volume")
            close_type = mt5.ORDER_TYPE_SELL if pos.type == mt5.POSITION_TYPE_BUY else mt5.ORDER_TYPE_BUY
            sinfo = mt5.symbol_info(order.symbol)
            req = {
                "action": mt5.TRADE_ACTION_DEAL,
                "symbol": order.symbol,
                "volume": float(requested),
                "type": close_type,
                "position": int(ticket),
                "deviation": int(getattr(settings, "MT5_CLOSE_DEVIATION_POINTS", 20)),
                "magic": int(getattr(settings, "MT5_MAGIC_NUMBER", 20250813)),
                "comment": self._order_comment(order, closing=True),
                "type_filling": self._filling_mode(sinfo),
            }
            check = mt5.order_check(req)
            if check is None:
                msg = f"MT5 close order_check unavailable: {mt5.last_error()}"
                update_order_status(order, "rejected", error_msg=msg)
                raise ConnectorError(msg)
            check_retcode = getattr(check, "retcode", 0)
            if check_retcode not in (0, getattr(mt5, "TRADE_RETCODE_DONE", 10009)):
                msg = f"MT5 close order_check rejected: retcode={check_retcode}"
                update_order_status(order, "rejected", error_msg=msg)
                raise ConnectorError(msg)
            if order.status == "new":
                update_order_status(order, "ack")
            with transaction.atomic():
                locked_order = Order.objects.select_for_update().get(pk=order.pk)
                attempt_no = (
                    ExecutionAttempt.objects.filter(order=locked_order).aggregate(value=Max("attempt_no"))["value"]
                    or 0
                ) + 1
                attempt = ExecutionAttempt.objects.create(
                    order=locked_order,
                    attempt_no=attempt_no,
                    status="submitting",
                    request_fingerprint=_request_fingerprint(req),
                    requested_qty=requested,
                    remaining_qty=locked_order.remaining_qty or requested,
                    submitted_at=timezone.now(),
                )
            result = _timed_order_send(order, req)
            if result is None:
                order.last_error = f"Ambiguous MT5 close submission; reconciliation required: {mt5.last_error()}"
                order.save(update_fields=["last_error"])
                attempt.status = "ambiguous"
                attempt.error = order.last_error
                attempt.save(update_fields=["status", "error"])
                raise ConnectorError(order.last_error)
            _maybe_store_broker_ticket(order, result)
            retcode = getattr(result, "retcode", None)
            order.mt5_retcode = retcode
            order.mt5_retcode_description = str(getattr(result, "comment", "") or "")
            order.broker_response = _safe_mt5_metadata(result)
            order.save(update_fields=["mt5_retcode", "mt5_retcode_description", "broker_response"])
            attempt.mt5_retcode = retcode
            attempt.mt5_retcode_description = order.mt5_retcode_description
            attempt.broker_order_ticket = order.broker_order_ticket
            attempt.broker_deal_ticket = order.broker_deal_ticket
            attempt.broker_position_ticket = int(ticket)
            attempt.response_metadata = order.broker_response
            if retcode not in (mt5.TRADE_RETCODE_DONE, mt5.TRADE_RETCODE_DONE_PARTIAL):
                msg = f"MT5 close rejected for ticket {ticket}: retcode={retcode}"
                attempt.status = "rejected"
                attempt.error = msg
                attempt.resolved_at = timezone.now()
                attempt.save()
                update_order_status(order, "rejected", error_msg=msg)
                raise ConnectorError(msg)

            reported_close_qty = Decimal(str(getattr(result, "volume", 0) or 0))
            if retcode == mt5.TRADE_RETCODE_DONE_PARTIAL and reported_close_qty <= 0:
                msg = (
                    "MT5 reported a partial close without filled volume; "
                    "broker reconciliation is required"
                )
                attempt.status = "ambiguous"
                attempt.error = msg
                attempt.save(
                    update_fields=[
                        "mt5_retcode",
                        "mt5_retcode_description",
                        "broker_order_ticket",
                        "broker_deal_ticket",
                        "broker_position_ticket",
                        "response_metadata",
                        "status",
                        "error",
                    ]
                )
                order.last_error = msg
                order.save(update_fields=["last_error"])
                raise ConnectorError(msg)

            filled = reported_close_qty if reported_close_qty > 0 else requested
            fill_price = Decimal(str(getattr(result, "price", 0) or 0))
            order.filled_qty += filled
            order.remaining_qty = max(Decimal("0"), order.qty - order.filled_qty)
            order.save(update_fields=["filled_qty", "remaining_qty"])
            attempt.filled_qty = filled
            attempt.remaining_qty = order.remaining_qty
            attempt.actual_fill_price = fill_price
            attempt.status = "partial" if order.remaining_qty > 0 else "accepted"
            attempt.resolved_at = timezone.now()
            attempt.save()
            record_fill(
                order,
                filled,
                fill_price,
                broker_order_ticket=order.broker_order_ticket,
                broker_deal_ticket=order.broker_deal_ticket,
                broker_position_ticket=int(ticket),
                broker_metadata=_safe_mt5_metadata(result),
            )
            mark_execution_recorded(order)
            remaining = mt5.positions_get(ticket=int(ticket)) or ()
            if remaining:
                self._sync_broker_position(order, remaining[0], ownership="ez_trade")
            else:
                owned.status = "closed"
                owned.volume = Decimal("0")
                owned.closed_at = timezone.now()
                owned.last_reconciled_at = timezone.now()
                owned.save(update_fields=["status", "volume", "closed_at", "last_reconciled_at"])
            update_order_status(
                order,
                "filled" if order.remaining_qty == 0 else "part_filled",
                price=fill_price,
            )
            return

        # Hedging guard: block opposite-side positions if hedging is disabled.
        allow_hedge = bool(
            runtime_cfg.decision_allow_hedging
            or (order.bot and getattr(order.bot, "allow_opposite_scalp", False))
        )
        broker_positions = mt5.positions_get()
        if broker_positions is None:
            msg = f"Order {order.id} rejected: unable to refresh broker exposure"
            update_order_status(order, "error", error_msg=msg)
            raise ConnectorError(msg)
        broker_positions = tuple(broker_positions)
        self._sync_broker_exposure_snapshot(order.broker_account, broker_positions)
        positions = tuple(
            position
            for position in broker_positions
            if str(getattr(position, "symbol", "")) == order.symbol
        )
        if not allow_hedge and positions and not is_close_order:
            buys = sum(Decimal(str(p.volume)) for p in positions if p.type == mt5.ORDER_TYPE_BUY)
            sells = sum(Decimal(str(p.volume)) for p in positions if p.type == mt5.ORDER_TYPE_SELL)
            net = buys - sells
            # Reject if opposite to existing net, or if both long/short exist (hedged pair)
            hedged_pair = buys > 0 and sells > 0
            if hedged_pair or (net > 0 and order.side == "sell") or (net < 0 and order.side == "buy"):
                msg = f"Order {order.id} rejected: hedging disabled and existing exposure on {order.symbol} (buys={buys}, sells={sells})"
                update_order_status(order, "error", error_msg=msg)
                raise ConnectorError(msg)

        # 2) Move out of 'new' – we have submitted to broker
        # Fetch tick for spread/notional checks
        tick = mt5.symbol_info_tick(order.symbol)
        if not tick:
            # Retry after ensuring symbol is visible
            try:
                mt5.symbol_select(order.symbol, True)
                time.sleep(0.05)
                tick = mt5.symbol_info_tick(order.symbol)
            except Exception:
                tick = None
        if not tick:
            msg = f"Order {order.id} rejected: no tick data for {order.symbol}"
            update_order_status(order, "error", error_msg=msg)
            raise ConnectorError(msg)

        bid = Decimal(str(getattr(tick, "bid", 0) or 0))
        ask = Decimal(str(getattr(tick, "ask", 0) or 0))
        if bid <= 0 or ask <= 0:
            # Retry once for transient MT5 glitches
            try:
                time.sleep(0.05)
                tick = mt5.symbol_info_tick(order.symbol)
                bid = Decimal(str(getattr(tick, "bid", 0) or 0))
                ask = Decimal(str(getattr(tick, "ask", 0) or 0))
            except Exception:
                pass
        if bid <= 0 or ask <= 0:
            msg = f"Order {order.id} rejected: invalid bid/ask for {order.symbol}"
            update_order_status(order, "rejected", error_msg=msg)
            raise ConnectorError(msg)

        symbol_info = mt5.symbol_info(order.symbol)
        broker_min_volume = Decimal(
            str(getattr(symbol_info, "volume_min", 0) or 0)
        )
        if (
            runtime_cfg.max_order_lot > 0
            and broker_min_volume > runtime_cfg.max_order_lot
        ):
            reason = "broker_min_volume_exceeds_max_order_lot"
            msg = (
                f"Order {order.id} rejected: {reason} "
                f"(broker_min={broker_min_volume}, "
                f"configured_max={runtime_cfg.max_order_lot})"
            )
            update_order_status(order, "rejected", error_msg=msg)
            raise ConnectorError(msg)

        try:
            from execution.services.live_risk import RiskRejected, enforce_pretrade_risk

            risk_result = enforce_pretrade_risk(
                order,
                self,
                tick,
                symbol_info,
                mt5.account_info(),
                broker_positions=broker_positions,
            )
            qty_dec = risk_result.volume
            mark_order_timestamp(order, "risk_validation_completed_at")
        except RiskRejected as exc:
            msg = f"Order {order.id} risk rejected: {exc}"
            update_order_status(order, "rejected", error_msg=msg)
            log_journal_event(
                "risk.rejection",
                severity="warning",
                order=order,
                bot=order.bot,
                broker_account=order.broker_account,
                symbol=order.symbol,
                message=str(exc),
                context={"reason": str(exc)},
            )
            raise ConnectorError(msg) from exc

        # Identify asset + basic classification (used for contract size handling)
        asset = getattr(order.bot, "asset", None) if order.bot else None
        is_crypto = False
        try:
            sym_upper = (order.symbol or "").upper()
            is_crypto = (getattr(asset, "category", "") == "crypto") or any(
                key in sym_upper for key in ("BTC", "ETH", "SOL", "XRP", "LTC")
            )
        except Exception:
            is_crypto = False

        # MT5 prices are per-unit; scale by contract size so notional checks use real exposure (e.g., 0.10 lot EURUSD = 10000 units).
        default_contract = runtime_cfg.mt5_default_contract_size
        contract_size = Decimal(str(default_contract))
        try:
            sinfo = mt5.symbol_info(order.symbol)
            raw_contract = getattr(sinfo, "trade_contract_size", None) if sinfo else None
            if raw_contract is not None:
                cs_val = Decimal(str(raw_contract))
                # Guard against bad/zero contract sizes coming from MT5; fall back to configured default for FX.
                if cs_val <= 0:
                    contract_size = Decimal(str(default_contract))
                elif cs_val < Decimal("10") and default_contract > 10:
                    # Crypto symbols often have tiny contract sizes; keep the broker value to avoid inflating notional.
                    contract_size = cs_val if is_crypto else Decimal(str(default_contract))
                else:
                    contract_size = cs_val
            elif is_crypto:
                # If MT5 omits contract size for crypto, assume 1 to avoid over-scaling notional checks.
                contract_size = Decimal("1")
        except Exception:
            # best-effort only; fall back to default if symbol info is unavailable
            contract_size = Decimal("1") if is_crypto else Decimal(str(default_contract))

        spread = ask - bid
        # Asset-based guards
        asset_max_spread = Decimal(str(asset.max_spread)) if asset else Decimal("0")
        # Allow close orders to proceed even if spread is wide to avoid being trapped.
        if not is_close_order and asset_max_spread > 0 and spread > asset_max_spread:
            msg = f"Order {order.id} rejected: spread {spread} exceeds limit {asset_max_spread} for {order.symbol}"
            update_order_status(order, "error", error_msg=msg)
            raise ConnectorError(msg)

        # Min/max notional and max lot checks (price * qty)
        test_mode = bool(getattr(settings, "TRADING_TEST_MODE", False))
        max_lot = runtime_cfg.max_order_lot
        if max_lot > 0 and qty_dec > max_lot:
            msg = f"Order {order.id} rejected: qty {qty_dec} exceeds max lot {max_lot}"
            update_order_status(order, "error", error_msg=msg)
            raise ConnectorError(msg)

        if not is_close_order:
            qty_abs = qty_dec.copy_abs()
            px = ask if order.side == "buy" else bid
            notional = px * qty_abs * contract_size
            asset_min_notional = Decimal(str(asset.min_notional)) if asset else Decimal("0")
            # Some brokers (cent/demo) report tiny contract sizes; scale back to the configured default
            # so that admin-set min/max notionals keep behaving as "standard lot" amounts.
            # Avoid scaling when contract_size is reasonable (e.g., 100 for metals) to prevent inflating notional.
            scale = Decimal("1")
            try:
                if contract_size > 0:
                    default_cs = Decimal(str(runtime_cfg.mt5_default_contract_size))
                    ratio = default_cs / contract_size
                    # Only scale when the contract size is clearly tiny (<10) and ratio not extreme.
                    if contract_size < Decimal("10") and ratio <= Decimal("1000"):
                        scale = ratio
            except Exception:
                scale = Decimal("1")
            effective_notional = notional * scale
            if not test_mode and asset_min_notional > 0 and effective_notional < asset_min_notional:
                msg = (
                    f"Order {order.id} rejected: notional {effective_notional} below minimum "
                    f"{asset_min_notional} (contract_size={contract_size})"
                )
                update_order_status(order, "error", error_msg=msg)
                raise ConnectorError(msg)
            max_notional = runtime_cfg.max_order_notional
            if not test_mode and max_notional > 0 and effective_notional > max_notional:
                msg = (
                    f"Order {order.id} rejected: notional {effective_notional} exceeds max limit {max_notional} "
                    f"(contract_size={contract_size})"
                )
                update_order_status(order, "error", error_msg=msg)
                raise ConnectorError(msg)

        volume = float(qty_dec)  # MT5 volume is float lots
        order_type = mt5.ORDER_TYPE_BUY if order.side == "buy" else mt5.ORDER_TYPE_SELL

        sinfo = mt5.symbol_info(order.symbol)
        req = {
            "action": mt5.TRADE_ACTION_DEAL,
            "symbol": order.symbol,
            "volume": volume,
            "type": order_type,
            # The authoritative risk pass resolves the bot's unit-aware,
            # symbol-specific slippage setting to raw MT5 points. This also
            # falls back to RiskPolicy.deviation_points for non-scalper orders.
            "deviation": int(risk_result.deviation_points),
            "magic": int(getattr(settings, "MT5_MAGIC_NUMBER", 20250813)),
            "comment": self._order_comment(order),
            "type_filling": self._filling_mode(sinfo),
        }

        # CRITICAL: Enforce SL/TP on every order (risk management)
        if order.sl is not None:
            req["sl"] = float(order.sl)

        if order.tp is not None:
            req["tp"] = float(order.tp)
        
        if order.sl is None or order.tp is None:
            msg = f"Order {order.id} rejected: SL or TP missing (risk management enforced)"
            update_order_status(order, "rejected", error_msg=msg)
            raise ConnectorError(msg)

        # Best-effort: adjust SL/TP to respect broker stop level to reduce MT5 10016 errors.
        try:
            sinfo = mt5.symbol_info(order.symbol)
            point = Decimal(str(getattr(sinfo, "point", 0) or 0))
            stops_level = Decimal(str(getattr(sinfo, "stops_level", 0) or 0))
            min_stop = point * stops_level
            if min_stop > 0:
                if order.side == "buy":
                    if "sl" in req and req["sl"] > 0:
                        sl_gap = Decimal(str(ask)) - Decimal(str(req["sl"]))
                        if sl_gap < min_stop:
                            req["sl"] = float(ask - min_stop)
                    if "tp" in req and req["tp"] > 0:
                        tp_gap = Decimal(str(req["tp"])) - Decimal(str(bid))
                        if tp_gap < min_stop:
                            req["tp"] = float(bid + min_stop)
                else:
                    if "sl" in req and req["sl"] > 0:
                        sl_gap = Decimal(str(req["sl"])) - Decimal(str(bid))
                        if sl_gap < min_stop:
                            req["sl"] = float(bid + min_stop)
                    if "tp" in req and req["tp"] > 0:
                        tp_gap = Decimal(str(ask)) - Decimal(str(req["tp"]))
                        if tp_gap < min_stop:
                            req["tp"] = float(ask - min_stop)
        except Exception:
            # If we cannot read/adjust stops, let MT5 enforce.
            pass

        # Broker validation before submission. A rejected check is definitive
        # and safe to store without sending an order.
        check = mt5.order_check(req)
        if check is None:
            msg = f"MT5 order_check unavailable: {mt5.last_error()}"
            update_order_status(order, "rejected", error_msg=msg)
            raise ConnectorError(msg)
        check_retcode = getattr(check, "retcode", 0)
        if check_retcode not in (0, getattr(mt5, "TRADE_RETCODE_DONE", 10009)):
            msg = f"MT5 order_check rejected: retcode={check_retcode}"
            order.mt5_retcode = check_retcode
            order.mt5_retcode_description = str(getattr(check, "comment", "") or "")
            order.broker_response = _safe_mt5_metadata(check)
            order.save(update_fields=["mt5_retcode", "mt5_retcode_description", "broker_response"])
            update_order_status(order, "rejected", error_msg=msg)
            raise ConnectorError(msg)

        update_order_status(order, "ack")

        with transaction.atomic():
            locked_order = Order.objects.select_for_update().get(pk=order.pk)
            attempt_no = (
                ExecutionAttempt.objects.filter(order=locked_order).aggregate(value=Max("attempt_no"))["value"]
                or 0
            ) + 1
            attempt = ExecutionAttempt.objects.create(
                order=locked_order,
                attempt_no=attempt_no,
                status="submitting",
                request_fingerprint=_request_fingerprint(req),
                requested_qty=qty_dec,
                remaining_qty=locked_order.remaining_qty or qty_dec,
                requested_price=locked_order.requested_price,
                submitted_at=timezone.now(),
            )

        result = _timed_order_send(order, req)
        if result is None:
            err = mt5.last_error()
            msg = f"MT5 submission outcome is ambiguous: {err}"
            attempt.status = "ambiguous"
            attempt.error = msg
            attempt.save(update_fields=["status", "error"])
            order.last_error = msg
            order.save(update_fields=["last_error"])
            log_journal_event(
                "order.dispatch_error",
                severity="error",
                order=order,
                bot=order.bot,
                broker_account=order.broker_account,
                symbol=order.symbol,
                message="MT5 order_send outcome ambiguous; resend blocked pending reconciliation",
                context={"retcode": None, "details": str(err)},
            )
            raise ConnectorError(msg)

        ret = getattr(result, "retcode", None)
        raw_price = getattr(result, "price", None)
        fill_price = Decimal(str(raw_price)) if raw_price is not None else None
        _maybe_store_broker_ticket(order, result)
        response_metadata = _safe_mt5_metadata(result)
        order.mt5_retcode = ret
        order.mt5_retcode_description = str(getattr(result, "comment", "") or "")
        order.broker_response = response_metadata
        order.save(update_fields=["mt5_retcode", "mt5_retcode_description", "broker_response"])

        attempt.mt5_retcode = ret
        attempt.mt5_retcode_description = order.mt5_retcode_description
        attempt.broker_order_ticket = order.broker_order_ticket
        attempt.broker_deal_ticket = order.broker_deal_ticket
        attempt.broker_position_ticket = order.broker_position_ticket
        attempt.response_metadata = response_metadata

        # 4) Handle retcodes
        if ret in (mt5.TRADE_RETCODE_DONE, mt5.TRADE_RETCODE_DONE_PARTIAL):
            reported_qty = Decimal(str(getattr(result, "volume", 0) or 0))
            if ret == mt5.TRADE_RETCODE_DONE_PARTIAL and reported_qty <= 0:
                msg = (
                    "MT5 reported a partial fill without filled volume; "
                    "broker reconciliation is required"
                )
                attempt.status = "ambiguous"
                attempt.error = msg
                attempt.save(
                    update_fields=[
                        "mt5_retcode",
                        "mt5_retcode_description",
                        "broker_order_ticket",
                        "broker_deal_ticket",
                        "broker_position_ticket",
                        "response_metadata",
                        "status",
                        "error",
                    ]
                )
                order.last_error = msg
                order.save(update_fields=["last_error"])
                log_journal_event(
                    "order.reconciliation_required",
                    severity="warning",
                    order=order,
                    bot=order.bot,
                    broker_account=order.broker_account,
                    symbol=order.symbol,
                    message=msg,
                    context={"retcode": ret},
                )
                raise ConnectorError(msg)
            fill_qty = reported_qty if reported_qty > 0 else qty_dec
            fill_qty = min(fill_qty, Decimal(str(order.remaining_qty or qty_dec)))
            order.filled_qty = min(order.qty, order.filled_qty + fill_qty)
            order.remaining_qty = max(Decimal("0"), order.qty - order.filled_qty)
            order.actual_fill_price = fill_price
            order.save(update_fields=["filled_qty", "remaining_qty", "actual_fill_price"])

            attempt.filled_qty = fill_qty
            attempt.remaining_qty = order.remaining_qty
            attempt.actual_fill_price = fill_price
            attempt.status = "partial" if order.remaining_qty > 0 else "accepted"
            attempt.resolved_at = timezone.now()
            attempt.save(
                update_fields=[
                    "mt5_retcode",
                    "mt5_retcode_description",
                    "broker_order_ticket",
                    "broker_deal_ticket",
                    "broker_position_ticket",
                    "response_metadata",
                    "filled_qty",
                    "remaining_qty",
                    "actual_fill_price",
                    "status",
                    "resolved_at",
                ]
            )

            # fetch account balance at the time of fill
            acc_info = mt5.account_info()
            balance = None
            if acc_info is not None:
                balance = Decimal(str(acc_info.balance))

            broker_profit = None
            commission = Decimal("0")
            swap = Decimal("0")
            deal_metadata = response_metadata
            if order.broker_deal_ticket:
                try:
                    matching_deals = mt5.history_deals_get(ticket=order.broker_deal_ticket) or ()
                    if matching_deals:
                        broker_deal = matching_deals[-1]
                        # MqlTradeResult does not consistently expose a position
                        # field for market deals. The authoritative position id is
                        # available on the resulting deal and must be persisted so
                        # the position can be reconciled and closed by exact ticket.
                        broker_position_ticket = _coerce_ticket(
                            getattr(broker_deal, "position_id", None)
                        )
                        if broker_position_ticket:
                            order.broker_position_ticket = broker_position_ticket
                            order.save(update_fields=["broker_position_ticket"])
                            attempt.broker_position_ticket = broker_position_ticket
                            attempt.save(update_fields=["broker_position_ticket"])
                        broker_profit = Decimal(str(getattr(broker_deal, "profit", 0) or 0))
                        commission = Decimal(str(getattr(broker_deal, "commission", 0) or 0))
                        swap = Decimal(str(getattr(broker_deal, "swap", 0) or 0))
                        deal_metadata = _safe_mt5_metadata(broker_deal)
                except Exception:
                    logger.warning("Unable to load MT5 deal economics for order %s", order.id, exc_info=True)

            record_fill(
                order=order,
                qty=fill_qty,
                price=fill_price if fill_price is not None else Decimal("0"),
                account_balance=balance,
                contract_size=contract_size,
                broker_order_ticket=order.broker_order_ticket,
                broker_deal_ticket=order.broker_deal_ticket,
                broker_position_ticket=order.broker_position_ticket,
                broker_profit=broker_profit,
                commission=commission,
                swap=swap,
                broker_metadata=deal_metadata,
            )
            mark_execution_recorded(order)

            if order.broker_position_ticket:
                try:
                    broker_positions = mt5.positions_get(ticket=order.broker_position_ticket) or ()
                    if broker_positions:
                        self._sync_broker_position(order, broker_positions[0], ownership="ez_trade")
                except Exception:
                    logger.warning("Unable to synchronize MT5 position for order %s", order.id, exc_info=True)

            update_order_status(
                order,
                "filled" if order.remaining_qty == 0 else "part_filled",
                price=fill_price,
            )
            return

        if ret == mt5.TRADE_RETCODE_PLACED:
            attempt.status = "accepted"
            attempt.save(
                update_fields=[
                    "mt5_retcode",
                    "mt5_retcode_description",
                    "broker_order_ticket",
                    "broker_deal_ticket",
                    "broker_position_ticket",
                    "response_metadata",
                    "status",
                ]
            )
            log_journal_event(
                "order.submitted",
                order=order,
                bot=order.bot,
                broker_account=order.broker_account,
                symbol=order.symbol,
                message="MT5 acknowledged order; awaiting broker reconciliation",
                context={"retcode": ret, "broker_order_ticket": order.broker_order_ticket},
            )
            return

        msg = f"MT5 order rejected: retcode={ret} {order.mt5_retcode_description}".strip()
        attempt.status = "rejected"
        attempt.error = msg
        attempt.resolved_at = timezone.now()
        attempt.save(
            update_fields=[
                "mt5_retcode",
                "mt5_retcode_description",
                "broker_order_ticket",
                "broker_deal_ticket",
                "broker_position_ticket",
                "response_metadata",
                "status",
                "error",
                "resolved_at",
            ]
        )
        update_order_status(order, "rejected", error_msg=msg)
        log_journal_event(
            "order.dispatch_error",
            severity="error",
            order=order,
            bot=order.bot,
            broker_account=order.broker_account,
            symbol=order.symbol,
            message="MT5 order rejected",
            context={"retcode": ret, "description": order.mt5_retcode_description},
        )
        raise ConnectorError(msg)


    def cancel_order(self, order: Order) -> None:
        """Cancel only after broker state is known or broker removal is confirmed."""
        with _MT5Session.serialized():
            order.refresh_from_db()
            if order.status == "canceled":
                return
            if order.status in {"filled", "rejected", "error"}:
                raise ConnectorError(
                    f"Order {order.id} is terminal ({order.status}) and cannot be canceled"
                )

            has_submission = bool(
                order.submitted_at
                or order.broker_order_ticket
                or order.attempts.filter(
                    status__in=["submitting", "ambiguous", "accepted", "partial"]
                ).exists()
            )
            if order.status == "new" and not has_submission:
                update_order_status(order, "canceled")
                return

            self._login_from_order(order)
            if not order.broker_order_ticket:
                self.reconcile_order(order)
                order.refresh_from_db()
                if order.status == "filled":
                    raise ConnectorError(
                        f"Order {order.id} filled before cancellation could be confirmed"
                    )
                if not order.broker_order_ticket:
                    raise ConnectorError(
                        f"Order {order.id} cancellation is unverified; "
                        "broker reconciliation is required"
                    )

            active = mt5.orders_get(ticket=int(order.broker_order_ticket))
            if active is None:
                raise ConnectorError(
                    f"MT5 orders_get failed during cancellation: {mt5.last_error()}"
                )
            if not active:
                if self.reconcile_order(order):
                    order.refresh_from_db()
                    if order.status in {"filled", "part_filled"}:
                        raise ConnectorError(
                            f"Order {order.id} has broker fills and cannot be locally canceled"
                        )
                raise ConnectorError(
                    f"Order {order.id} is absent from active broker orders; "
                    "cancellation remains unverified"
                )

            request = {
                "action": mt5.TRADE_ACTION_REMOVE,
                "order": int(order.broker_order_ticket),
                "symbol": order.symbol,
                "magic": int(getattr(settings, "MT5_MAGIC_NUMBER", 20250813)),
                "comment": self._order_comment(order)[:31],
            }
            result = mt5.order_send(request)
            if result is None:
                raise ConnectorError(
                    f"MT5 cancellation outcome is ambiguous: {mt5.last_error()}"
                )
            retcode = getattr(result, "retcode", None)
            if retcode != mt5.TRADE_RETCODE_DONE:
                raise ConnectorError(
                    f"MT5 cancellation rejected: retcode={retcode} "
                    f"{getattr(result, 'comment', '')}".strip()
                )
            remaining = mt5.orders_get(ticket=int(order.broker_order_ticket))
            if remaining is None:
                raise ConnectorError(
                    "MT5 cancellation could not be verified after broker acknowledgement"
                )
            if remaining:
                raise ConnectorError(
                    "MT5 order remains active after cancellation acknowledgement"
                )

            order.mt5_retcode = retcode
            order.mt5_retcode_description = str(getattr(result, "comment", "") or "")
            order.broker_response = _safe_mt5_metadata(result)
            order.save(
                update_fields=[
                    "mt5_retcode",
                    "mt5_retcode_description",
                    "broker_response",
                ]
            )
            update_order_status(order, "canceled")
