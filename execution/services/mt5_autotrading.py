"""Read-only validation of the operator-controlled MT5 trading switches."""

from execution.connectors.base import ConnectorError


def ensure_algo_trading_enabled(api, *, terminal_path=None):
    """Require operator approval in MT5; never activate windows or send keys."""
    terminal = api.terminal_info()
    if terminal is None:
        raise ConnectorError("MT5 terminal status is unavailable")
    if bool(getattr(terminal, "tradeapi_disabled", False)):
        raise ConnectorError(
            "MT5 blocks external Python trading. In Tools > Options > Expert Advisors, "
            "clear 'Disable automated trading via external Python API'."
        )
    if not bool(getattr(terminal, "trade_allowed", False)):
        raise ConnectorError("Enable Algo Trading manually in MT5 before submitting orders")
