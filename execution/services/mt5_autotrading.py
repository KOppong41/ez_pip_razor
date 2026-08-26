"""Guarded Windows automation for MT5's global Algo Trading switch."""

from __future__ import annotations

import os
from pathlib import Path
import time

from execution.connectors.base import ConnectorError


def _terminal_window_pid(terminal_path: str) -> int | None:
    if os.name != "nt":
        return None

    import win32api
    import win32con
    import win32gui
    import win32process

    expected = os.path.normcase(os.path.abspath(terminal_path))
    candidates: list[tuple[bool, int]] = []

    def visit(hwnd, _extra):
        class_name = win32gui.GetClassName(hwnd)
        if not class_name.startswith("MetaQuotes::MetaTrader"):
            return
        _thread_id, process_id = win32process.GetWindowThreadProcessId(hwnd)
        try:
            process = win32api.OpenProcess(
                win32con.PROCESS_QUERY_INFORMATION | win32con.PROCESS_VM_READ,
                False,
                process_id,
            )
            try:
                executable = win32process.GetModuleFileNameEx(process, 0)
            finally:
                process.Close()
        except Exception:
            return
        if os.path.normcase(os.path.abspath(executable)) == expected:
            candidates.append((bool(win32gui.IsWindowVisible(hwnd)), process_id))

    win32gui.EnumWindows(visit, None)
    if not candidates:
        return None
    candidates.sort(reverse=True)
    return candidates[0][1]


def ensure_algo_trading_enabled(api, *, terminal_path: str) -> None:
    """Enable Ctrl+E only for the exact connected terminal and verify the result."""
    terminal = api.terminal_info()
    if terminal is None:
        raise ConnectorError("MT5 terminal status is unavailable")
    if bool(getattr(terminal, "tradeapi_disabled", False)):
        raise ConnectorError(
            "MT5 blocks external Python trading. In Tools > Options > Expert Advisors, "
            "clear 'Disable automated trading via external Python API'."
        )
    if bool(getattr(terminal, "trade_allowed", False)):
        return
    if os.name != "nt":
        raise ConnectorError("Enable Algo Trading in MT5 before starting the bot")

    path = Path(terminal_path)
    if not path.is_file():
        raise ConnectorError(f"MT5 terminal path not found: {path}")
    process_id = _terminal_window_pid(str(path))
    if process_id is None:
        raise ConnectorError(
            "MT5 is connected but its desktop window could not be identified; "
            "enable Algo Trading manually"
        )

    import win32com.client

    shell = win32com.client.Dispatch("WScript.Shell")
    if not shell.AppActivate(process_id):
        raise ConnectorError(
            "MT5 is connected but its window could not be activated; "
            "enable Algo Trading manually"
        )
    time.sleep(0.2)
    shell.SendKeys("^e")

    for _ in range(20):
        time.sleep(0.1)
        current = api.terminal_info()
        if current is not None and bool(getattr(current, "trade_allowed", False)):
            return
    raise ConnectorError(
        "MT5 did not enable Algo Trading after the automatic Ctrl+E command"
    )
