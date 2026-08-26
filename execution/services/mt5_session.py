from __future__ import annotations

from contextlib import contextmanager
from decimal import Decimal
import logging
import threading
from typing import Callable, Iterator

from execution.connectors.base import ConnectorError


logger = logging.getLogger(__name__)


class MT5SessionService:
    """Own the process-global MetaTrader terminal session.

    The ``mt5_execution`` Celery worker provides process-level serialization;
    this re-entrant lock makes account verification and each MT5 command atomic
    inside that worker process.
    """

    _lock = threading.RLock()
    _api_provider: Callable[[], object] | None = None
    _active_login: int | None = None
    _active_server: str | None = None
    _initialized = False
    _last_error = ""

    @classmethod
    def configure_api(cls, provider: Callable[[], object]) -> None:
        cls._api_provider = provider

    @classmethod
    def _api(cls):
        if cls._api_provider is None:
            raise ConnectorError("MT5 session API is not configured")
        return cls._api_provider()

    @classmethod
    @contextmanager
    def serialized(cls) -> Iterator[None]:
        with cls._lock:
            yield

    @classmethod
    def _reset_locked(cls, *, reason: str = "") -> None:
        api = cls._api()
        try:
            api.shutdown()
        except Exception:
            logger.debug("MT5 shutdown failed during reset", exc_info=True)
        cls._initialized = False
        cls._active_login = None
        cls._active_server = None
        if reason:
            cls._last_error = reason

    @classmethod
    def reset(cls, *, reason: str = "") -> None:
        with cls._lock:
            cls._reset_locked(reason=reason)

    @classmethod
    def ensure_login(
        cls,
        *,
        path: str,
        login: int,
        password: str,
        server: str,
        allow_switch: bool = True,
    ) -> None:
        api = cls._api()
        login = int(login)
        server = str(server)

        with cls._lock:
            if cls._initialized:
                try:
                    if api.terminal_info() is None or api.account_info() is None:
                        cls._reset_locked(reason="terminal_or_account_unavailable")
                except Exception:
                    cls._reset_locked(reason="session_health_check_failed")

            if cls._initialized:
                account = api.account_info()
                active_login = getattr(account, "login", cls._active_login)
                active_server = getattr(account, "server", cls._active_server)
                same_account = int(active_login or 0) == login and str(active_server or "") == server
                if same_account:
                    cls._active_login = login
                    cls._active_server = server
                    cls._last_error = ""
                    return
                if not allow_switch:
                    raise ConnectorError(
                        "MT5 session is connected to a different account; refusing account switch"
                    )

            if not cls._initialized:
                cls._reset_locked()
                if not api.initialize(path=path, login=login, password=password, server=server):
                    error = f"MT5 initialize failed: {api.last_error()}"
                    cls._last_error = error
                    raise ConnectorError(error)
                cls._initialized = True
            elif not api.login(login=login, password=password, server=server):
                error = f"MT5 login failed: {api.last_error()}"
                cls._last_error = error
                raise ConnectorError(error)

            terminal = api.terminal_info()
            account = api.account_info()
            if terminal is None or account is None:
                cls._reset_locked(reason="post_login_verification_failed")
                raise ConnectorError("MT5 connection verification failed after login")

            actual_login = getattr(account, "login", login)
            actual_server = getattr(account, "server", server)
            if int(actual_login or 0) != login or str(actual_server or "") != server:
                cls._reset_locked(reason="incorrect_active_account")
                raise ConnectorError(
                    f"MT5 active account mismatch for requested login={login} server={server}"
                )

            cls._active_login = login
            cls._active_server = server
            cls._last_error = ""
            logger.info("MT5 session connected for login=%s server=%s", login, server)

    @classmethod
    def account_equity(cls) -> Decimal:
        with cls._lock:
            info = cls._api().account_info()
            if info is None:
                raise ConnectorError("MT5 account_info failed")
            return Decimal(str(info.equity))

    @classmethod
    def health(cls) -> dict:
        """Return a non-secret connection snapshot for diagnostics."""
        with cls._lock:
            try:
                connected = bool(
                    cls._initialized
                    and cls._api().terminal_info() is not None
                    and cls._api().account_info() is not None
                )
            except Exception:
                connected = False
            return {
                "connected": connected,
                "login": cls._active_login,
                "server": cls._active_server,
                "last_error": cls._last_error,
            }
