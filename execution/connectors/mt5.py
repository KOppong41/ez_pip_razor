
from __future__ import annotations
from decimal import Decimal
import threading
import logging
import time
from execution.services.portfolio import record_fill
from execution.models import Order
from execution.services.orchestrator import update_order_status
from execution.services.journal import log_journal_event
from .base import BaseConnector, ConnectorError
from django.conf import settings

from core.metrics import mt5_errors_total
from execution.services.runtime_config import get_runtime_config

try:
    import MetaTrader5 as _mt5_module  # type: ignore
except Exception:
    _mt5_module = None


def is_mt5_available() -> bool:
    return _mt5_module is not None


class _MT5Proxy:
    """
    Lightweight proxy so the rest of the code can reference `mt5` even when the
    MetaTrader5 package is not installed (e.g., in Docker/Linux environments).
    """

    def __getattr__(self, item):
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
        try:
            self._login_from_creds(creds, allow_switch=False)
            self._ensure_symbol(symbol)
            _check_ready(symbol)
        except ConnectorError as e:
            if "No IPC connection" in str(e) or "-10004" in str(e):
                mt5.shutdown()
                _MT5Session._initialized = False
                self._login_from_creds(creds, allow_switch=False)
                self._ensure_symbol(symbol)
                _check_ready(symbol)
            else:
                raise

    def place_order(self, order: Order) -> None:
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

        # Treat close orders specially on hedging accounts: close by ticket to avoid stacking.
        is_close_order = str(getattr(order, "client_order_id", "")).startswith("close|")
        if is_close_order:
            positions = mt5.positions_get(symbol=order.symbol)
            if not positions:
                update_order_status(order, "filled")
                return

            last_price = None
            for pos in positions:
                vol = Decimal(str(getattr(pos, "volume", 0) or 0))
                if vol <= 0:
                    continue
                # In hedging mode MT5 requires specifying the ticket to close.
                close_type = mt5.ORDER_TYPE_SELL if pos.type == mt5.POSITION_TYPE_BUY else mt5.ORDER_TYPE_BUY
                req = {
                    "action": mt5.TRADE_ACTION_DEAL,
                    "symbol": order.symbol,
                    "volume": float(vol),
                    "type": close_type,
                    "position": pos.ticket,
                    "deviation": 20,
                    "magic": 20250813,
                    "comment": f"close:{order.id}:{pos.ticket}",
                    "type_filling": mt5.ORDER_FILLING_IOC,
                }
                result = mt5.order_send(req)
                if result is None or getattr(result, "retcode", None) not in (mt5.TRADE_RETCODE_DONE, mt5.TRADE_RETCODE_DONE_PARTIAL):
                    details = result._asdict() if result and hasattr(result, "_asdict") else result
                    msg = f"MT5 close failed for pos {pos.ticket}: retcode={getattr(result, 'retcode', None)} details={details}"
                    update_order_status(order, "error", error_msg=msg)
                    raise ConnectorError(msg)

                fill_price = Decimal(str(getattr(result, "price", 0) or 0))
                last_price = fill_price

                # Record fill against this order with side that matches the close direction.
                try:
                    tmp_side = "sell" if close_type == mt5.ORDER_TYPE_SELL else "buy"
                    tmp_order = order
                    tmp_order.side = tmp_side
                    tmp_order.qty = vol
                    record_fill(tmp_order, vol, fill_price)
                except Exception:
                    # Best-effort: do not block the close if portfolio accounting fails
                    pass

            update_order_status(order, "filled", price=last_price)
            return

        # Hedging guard: block opposite-side positions if hedging is disabled.
        allow_hedge = bool(
            runtime_cfg.decision_allow_hedging
            or (order.bot and getattr(order.bot, "allow_opposite_scalp", False))
        )
        positions = mt5.positions_get(symbol=order.symbol)
        if positions is None:
            msg = f"Order {order.id} rejected: unable to fetch positions for {order.symbol}"
            update_order_status(order, "error", error_msg=msg)
            raise ConnectorError(msg)
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
        update_order_status(order, "ack")

        # Enforce broker min lot using both asset config and MT5 symbol metadata to avoid invalid volume errors.
        try:
            asset_min = None
            if order.bot and getattr(order.bot, "asset", None):
                asset_min = Decimal(str(order.bot.asset.min_qty))

            mt5_min = None
            volume_step = None
            try:
                sinfo = mt5.symbol_info(order.symbol)
                if sinfo:
                    raw_min = getattr(sinfo, "volume_min", None)
                    raw_step = getattr(sinfo, "volume_step", None)
                    if raw_min is not None:
                        mt5_min = Decimal(str(raw_min))
                    if raw_step is not None:
                        volume_step = Decimal(str(raw_step))
            except Exception:
                mt5_min = None
                volume_step = None

            effective_min = asset_min
            if mt5_min is not None:
                effective_min = mt5_min if effective_min is None else max(effective_min, mt5_min)

            if effective_min is not None and qty_dec < effective_min:
                msg = (
                    f"Order {order.id} rejected: qty {order.qty} below broker minimum {effective_min} "
                    f"(volume_min)"
                )
                update_order_status(order, "error", error_msg=msg)
                raise ConnectorError(msg)

            if volume_step is not None and volume_step > 0:
                remainder = qty_dec % volume_step
                if remainder != 0:
                    msg = f"Order {order.id} rejected: qty {order.qty} not aligned to volume_step {volume_step}"
                    update_order_status(order, "error", error_msg=msg)
                    raise ConnectorError(msg)
        except ConnectorError:
            raise
        except Exception:
            # If anything goes wrong reading asset or symbol info, fall through and let broker validate.
            pass

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
            update_order_status(order, "error", error_msg=msg)
            raise ConnectorError(msg)

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
        asset_min_qty = Decimal(str(asset.min_qty)) if asset else Decimal("0")
        asset_max_spread = Decimal(str(asset.max_spread)) if asset else Decimal("0")
        # Allow close orders to proceed even if spread is wide to avoid being trapped.
        if not is_close_order and asset_max_spread > 0 and spread > asset_max_spread:
            msg = f"Order {order.id} rejected: spread {spread} exceeds limit {asset_max_spread} for {order.symbol}"
            update_order_status(order, "error", error_msg=msg)
            raise ConnectorError(msg)

        # Min/max notional and max lot checks (price * qty)
        test_mode = bool(getattr(settings, "TRADING_TEST_MODE", False))
        max_lot = runtime_cfg.max_order_lot
        effective_max_lot = max_lot
        if asset_min_qty > 0 and max_lot > 0 and max_lot < asset_min_qty:
            # If the configured cap is below the broker minimum, respect the broker to avoid rejections.
            effective_max_lot = asset_min_qty
            logger.warning(
                "max_order_lot %s below broker minimum %s for %s; raising cap to broker minimum",
                max_lot,
                asset_min_qty,
                order.symbol,
            )
        if not test_mode and effective_max_lot > 0 and qty_dec > effective_max_lot:
            msg = f"Order {order.id} rejected: qty {order.qty} exceeds max lot {effective_max_lot}"
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

        req = {
            "action": mt5.TRADE_ACTION_DEAL,
            "symbol": order.symbol,
            "volume": volume,
            "type": order_type,
            "deviation": 20,               # increased from 10 (reduce rejections, allow more slippage)
            "magic": 20250813,             # strategy id
            "comment": f"order:{order.id}",
            "type_filling": mt5.ORDER_FILLING_IOC,  # IOC: fill any amount immediately
        }

        # CRITICAL: Enforce SL/TP on every order (risk management)
        if order.sl is not None:
            req["sl"] = float(order.sl)

        if order.tp is not None:
            req["tp"] = float(order.tp)
        
        # Skip SL/TP enforcement for close orders (market exits)
        # Close orders created by monitor_positions_task won't have SL/TP
        # This is acceptable for risk management since we're closing the position anyway
        is_market_close = (order.sl is None and order.tp is None) and order.side in ("buy", "sell")
        
        if not is_market_close and (order.sl is None or order.tp is None):
            msg = f"Order {order.id} rejected: SL or TP missing (risk management enforced)"
            update_order_status(order, "error", error_msg=msg)
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

        # 3) Send to MT5 with retry on slippage
        max_retries = 2
        for attempt in range(max_retries):
            result = mt5.order_send(req)
            if result is not None:
                # Trace raw response so we can debug stuck ACK/PLACED cases.
                try:
                    logger.info(
                        "[MT5] order_send retcode=%s attempt=%s order=%s details=%s",
                        getattr(result, "retcode", None),
                        attempt + 1,
                        order.id,
                        result._asdict() if hasattr(result, "_asdict") else result,
                    )
                except Exception:
                    # best-effort logging only
                    pass
                break
            if attempt < max_retries - 1:
                import time
                time.sleep(0.1)  # brief wait before retry
        
        if result is None:
            err = mt5.last_error()
            msg = f"order_send failed after {max_retries} attempts: {err}"
            update_order_status(order, "error", error_msg=msg)
            log_journal_event(
                "order.dispatch_error",
                severity="error",
                order=order,
                bot=order.bot,
                broker_account=order.broker_account,
                symbol=order.symbol,
                message="MT5 order_send returned None",
                context={"retcode": None, "details": str(err)},
            )
            raise ConnectorError(msg)

        ret = getattr(result, "retcode", None)
        raw_price = getattr(result, "price", None)
        fill_price = Decimal(str(raw_price)) if raw_price is not None else None

        # 4) Handle retcodes
        if ret in (mt5.TRADE_RETCODE_DONE, mt5.TRADE_RETCODE_DONE_PARTIAL):
            # mark order filled
            update_order_status(order, "filled", price=fill_price)

            # fetch account balance at the time of fill
            acc_info = mt5.account_info()
            balance = None
            if acc_info is not None:
                balance = Decimal(str(acc_info.balance))

            # log execution + update Position
            record_fill(
                order=order,
                qty=qty_dec,
                price=fill_price if fill_price is not None else Decimal("0"),
                account_balance=balance,
            )
            return

        if ret == mt5.TRADE_RETCODE_PLACED:
            # Treat unexpected PLACED for market orders as error to avoid stuck ACK.
            details = result._asdict() if hasattr(result, "_asdict") else result
            msg = f"MT5 order placed but not filled: retcode={ret}, details={details}"
            update_order_status(order, "error", error_msg=msg)
            log_journal_event(
                "order.dispatch_error",
                severity="error",
                order=order,
                bot=order.bot,
                broker_account=order.broker_account,
                symbol=order.symbol,
                message="MT5 order placed but not filled",
                context={"retcode": ret, "details": str(details)},
            )
            raise ConnectorError(msg)

        # anything else is treated as error
        details = result._asdict() if hasattr(result, "_asdict") else result
        msg = f"MT5 order failed: retcode={ret}, details={details}"
        update_order_status(order, "error", error_msg=msg)
        log_journal_event(
            "order.dispatch_error",
            severity="error",
            order=order,
            bot=order.bot,
            broker_account=order.broker_account,
            symbol=order.symbol,
            message="MT5 order rejected",
            context={"retcode": ret, "details": str(details)},
        )
        raise ConnectorError(msg)


    def cancel_order(self, order: Order) -> None:
        # For market orders there’s nothing to cancel post‑fill.
        update_order_status(order, "canceled")
