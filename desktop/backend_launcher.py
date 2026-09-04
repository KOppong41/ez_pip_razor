"""Headless process supervisor for the EZ Trade Flutter desktop app.

The Flutter executable owns this process. The supervisor migrates the local
database, starts the local Django API and Celery services, restarts a service
if it crashes, and stops everything when Flutter exits.

The same file works in development and when frozen with PyInstaller. Frozen
children re-enter this executable with ``--service`` instead of trying to run
``manage.py`` or ``python -m celery`` from a bundled application.
"""

from __future__ import annotations

import argparse
import copy
import ctypes
import json
import os
import shlex
import signal
import subprocess
import sys
import threading
import time
import traceback
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

import yaml


APP_NAME = "EzScalperBot"
DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 8000
APPDATA = Path(os.getenv("APPDATA", Path.home() / "AppData" / "Roaming"))
DESKTOP_ROOT = APPDATA / APP_NAME
CONFIG_PATH = DESKTOP_ROOT / "config.yml"
ENV_PATH = DESKTOP_ROOT / ".env"
LOG_DIR = DESKTOP_ROOT / "logs"

DEFAULT_CONFIG_YAML = """\
django:
  host: 127.0.0.1
  port: 8000
  settings_module: config.settings_desktop
celery:
  worker:
    args: "worker -l info -Q celery --pool=solo --concurrency=1 --hostname=general@%h"
  mt5_worker:
    args: "worker -l info -Q mt5_execution --pool=solo --concurrency=1 --hostname=mt5@%h"
  beat:
    args: "beat -l info"
logging:
  root_dir: "%APPDATA%/EzScalperBot/logs"
  max_bytes: 10485760
  backup_count: 5
"""


def project_root() -> Path:
    """Return the source root or PyInstaller's bundled data root."""
    if getattr(sys, "frozen", False):
        return Path(getattr(sys, "_MEIPASS"))
    return Path(__file__).resolve().parents[1]


def sample_config_path() -> Path:
    return project_root() / "desktop" / "config.sample.yml"


def ensure_runtime_dirs() -> None:
    DESKTOP_ROOT.mkdir(parents=True, exist_ok=True)
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    if CONFIG_PATH.exists():
        return
    sample = sample_config_path()
    contents = sample.read_text(encoding="utf-8") if sample.exists() else DEFAULT_CONFIG_YAML
    CONFIG_PATH.write_text(contents, encoding="utf-8")


def load_config(*, host: str | None = None, port: int | None = None) -> dict[str, Any]:
    ensure_runtime_dirs()
    with CONFIG_PATH.open("r", encoding="utf-8") as config_file:
        config = yaml.safe_load(config_file) or {}
    defaults = yaml.safe_load(DEFAULT_CONFIG_YAML) or {}
    _merge_missing_config(config, defaults)
    django_config = config.setdefault("django", {})
    django_config["host"] = host or os.getenv("EZTRADE_BACKEND_HOST") or django_config.get("host", DEFAULT_HOST)
    env_port = os.getenv("EZTRADE_BACKEND_PORT")
    django_config["port"] = int(port or env_port or django_config.get("port", DEFAULT_PORT))
    django_config.setdefault("settings_module", "config.settings_desktop")
    return config


def _merge_missing_config(
    config: dict[str, Any], defaults: dict[str, Any]
) -> dict[str, Any]:
    """Backfill new launcher keys without replacing user customisations."""
    for key, default_value in defaults.items():
        if key not in config or config[key] is None:
            config[key] = copy.deepcopy(default_value)
            continue
        if isinstance(config[key], dict) and isinstance(default_value, dict):
            _merge_missing_config(config[key], default_value)
    return config


def validate_runtime_config(config: dict[str, Any]) -> None:
    """Fail fast when a desktop service required by the app is not configured."""
    celery_config = config.get("celery")
    if not isinstance(celery_config, dict):
        raise RuntimeError("Desktop configuration is missing the celery section.")
    for service_name in ("worker", "mt5_worker", "beat"):
        service_config = celery_config.get(service_name)
        if not isinstance(service_config, dict) or not service_config.get("args"):
            raise RuntimeError(
                f"Desktop configuration is missing celery.{service_name}.args."
            )


def check_packaged_runtime(config: dict[str, Any]) -> int:
    """Verify packaged imports and service config without starting local services."""
    validate_runtime_config(config)
    import MetaTrader5  # noqa: F401
    import celery  # noqa: F401
    import django  # noqa: F401
    import waitress  # noqa: F401

    return 0


def build_env(config: dict[str, Any]) -> dict[str, str]:
    env = os.environ.copy()
    django_config = config["django"]
    env["DJANGO_SETTINGS_MODULE"] = str(
        django_config.get("settings_module", "config.settings_desktop")
    )
    env["EZTRADE_BACKEND_HOST"] = str(django_config["host"])
    env["EZTRADE_BACKEND_PORT"] = str(django_config["port"])
    env["PYTHONUNBUFFERED"] = "1"
    env["ALLOW_SQLITE_DESKTOP"] = "1"
    env["ENV_FILE"] = str(ENV_PATH)
    return env


def _service_command(service: str) -> list[str]:
    command = [sys.executable]
    if not getattr(sys, "frozen", False):
        command.append(str(Path(__file__).resolve()))
    command.extend(["--service", service])
    return command


def _creation_flags() -> int:
    if os.name != "nt":
        return 0
    return subprocess.CREATE_NEW_PROCESS_GROUP | subprocess.CREATE_NO_WINDOW


def _write_supervisor_log(message: str) -> None:
    ensure_runtime_dirs()
    timestamp = time.strftime("%Y-%m-%d %H:%M:%S")
    with (LOG_DIR / "supervisor.log").open("a", encoding="utf-8") as stream:
        stream.write(f"[{timestamp}] {message}\n")


def start_process(
    command: list[str],
    *,
    env: dict[str, str],
    label: str,
) -> subprocess.Popen[str]:
    """Start one managed child and send all of its output to a service log."""
    ensure_runtime_dirs()
    log_stream = (LOG_DIR / f"{label}.log").open(
        "a", encoding="utf-8", buffering=1
    )
    log_stream.write(
        f"\n[{time.strftime('%Y-%m-%d %H:%M:%S')}] starting {shlex.join(command)}\n"
    )
    try:
        process = subprocess.Popen(
            command,
            cwd=project_root(),
            env=env,
            stdin=subprocess.DEVNULL,
            stdout=log_stream,
            stderr=subprocess.STDOUT,
            creationflags=_creation_flags(),
            text=True,
        )
    except Exception:
        log_stream.close()
        raise
    process._eztrade_log_stream = log_stream  # type: ignore[attr-defined]
    return process


def close_process_log(process: subprocess.Popen[str]) -> None:
    stream = getattr(process, "_eztrade_log_stream", None)
    if stream and not stream.closed:
        stream.close()


def stop_processes(processes: list[tuple[str, subprocess.Popen[str], list[str]]]) -> None:
    for _label, process, _command in processes:
        if process.poll() is None:
            try:
                process.terminate()
            except OSError:
                pass

    deadline = time.monotonic() + 8
    while time.monotonic() < deadline:
        if all(process.poll() is not None for _, process, _ in processes):
            break
        time.sleep(0.1)

    for _label, process, _command in processes:
        if process.poll() is None:
            try:
                process.kill()
            except OSError:
                pass
        close_process_log(process)


def backend_is_ready(base_url: str, *, timeout: float = 1.0) -> bool:
    """Return true only when the database and worker health checks pass."""
    try:
        response = urllib.request.urlopen(f"{base_url}/api/health/", timeout=timeout)
    except urllib.error.HTTPError as exc:
        if exc.code != 503:
            return False
        response = exc
    except (OSError, urllib.error.URLError):
        return False

    try:
        payload = json.loads(response.read().decode("utf-8"))
        return (
            isinstance(payload, dict)
            and payload.get("status") == "ok"
            and payload.get("db") is True
            and payload.get("worker") is True
        )
    except (UnicodeDecodeError, json.JSONDecodeError):
        return False
    finally:
        response.close()


def wait_for_backend(base_url: str, *, timeout: float = 60.0) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if backend_is_ready(base_url):
            return True
        time.sleep(0.4)
    return False


def process_is_running(process_id: int) -> bool:
    if process_id <= 0:
        return False
    if os.name != "nt":
        try:
            os.kill(process_id, 0)
            return True
        except OSError:
            return False

    process_query_limited_information = 0x1000
    still_active = 259
    kernel32 = ctypes.windll.kernel32
    handle = kernel32.OpenProcess(
        process_query_limited_information, False, process_id
    )
    if not handle:
        return False
    try:
        exit_code = ctypes.c_ulong()
        if not kernel32.GetExitCodeProcess(handle, ctypes.byref(exit_code)):
            return False
        return exit_code.value == still_active
    finally:
        kernel32.CloseHandle(handle)


def _normalise_celery_args(value: str, expected_command: str) -> list[str]:
    args = shlex.split(value, posix=os.name != "nt")
    if len(args) >= 2 and args[0] in {"-A", "--app"}:
        args = args[2:]
    if not args or args[0] != expected_command:
        args.insert(0, expected_command)
    if expected_command == "worker" and os.name == "nt":
        pool_flags = {"--pool", "-P"}
        if not any(flag in pool_flags or flag.startswith("--pool=") for flag in args):
            args.extend(["--pool", "solo"])
    return args


def _ensure_frozen_service_streams(service: str) -> None:
    """Windowed PyInstaller apps have no standard streams; libraries expect them."""
    if not getattr(sys, "frozen", False):
        return
    if sys.stdout is not None and sys.stderr is not None:
        return
    ensure_runtime_dirs()
    stream = (LOG_DIR / f"{service}-runtime.log").open(
        "a", encoding="utf-8", buffering=1
    )
    if sys.stdout is None:
        sys.stdout = stream
    if sys.stderr is None:
        sys.stderr = stream


def run_service(service: str, config: dict[str, Any]) -> int:
    root = project_root()
    os.chdir(root)
    if str(root) not in sys.path:
        sys.path.insert(0, str(root))
    os.environ.update(build_env(config))
    _ensure_frozen_service_streams(service)

    if service == "migrate":
        from django.core.management import execute_from_command_line

        execute_from_command_line(["manage.py", "migrate", "--noinput"])
        return 0

    if service == "safety-stop":
        import django
        from django.db import connection

        django.setup()
        if connection.vendor != "sqlite":
            print("Safety reset skipped for an explicitly configured external database.")
            return 0
        from bots.models import Bot
        from execution.models import RiskPolicy

        policies = RiskPolicy.objects.filter(entries_enabled=True).update(
            entries_enabled=False
        )
        bots = Bot.objects.exclude(status="stopped").update(status="stopped")
        print(f"Desktop safety reset: {policies} policies and {bots} bots stopped.")
        return 0

    if service == "web":
        from waitress import serve
        from config.wsgi import application

        django_config = config["django"]
        serve(
            application,
            host=str(django_config["host"]),
            port=int(django_config["port"]),
            threads=8,
            clear_untrusted_proxy_headers=True,
        )
        return 0

    from config.celery import app

    if service == "worker":
        args = _normalise_celery_args(
            config["celery"]["worker"]["args"], "worker"
        )
        app.worker_main(args)
        return 0
    if service == "mt5-worker":
        mt5_config = config["celery"].get("mt5_worker") or {}
        args = _normalise_celery_args(
            mt5_config.get(
                "args",
                "worker -l info -Q mt5_execution --pool=solo --concurrency=1 --hostname=mt5@%h",
            ),
            "worker",
        )
        app.worker_main(args)
        return 0
    if service == "beat":
        args = _normalise_celery_args(config["celery"]["beat"]["args"], "beat")
        if "--schedule" not in args and "-s" not in args:
            args.extend(["--schedule", str(DESKTOP_ROOT / "celerybeat-schedule")])
        app.start(args)
        return 0
    raise ValueError(f"Unknown service: {service}")


def _run_one_shot(service: str, env: dict[str, str], *, label: str) -> bool:
    process = start_process(_service_command(service), env=env, label=label)
    return_code = process.wait()
    close_process_log(process)
    if return_code != 0:
        _write_supervisor_log(
            f"{label} failed with exit code {return_code}."
        )
        return False
    return True


def run_supervisor(
    config: dict[str, Any],
    *,
    parent_pid: int | None,
    shutdown_file: Path | None,
) -> int:
    env = build_env(config)
    django_config = config["django"]
    base_url = f"http://{django_config['host']}:{django_config['port']}"

    if backend_is_ready(base_url):
        _write_supervisor_log(f"Backend already available at {base_url}; nothing to start.")
        return 0
    if not _run_one_shot("migrate", env, label="migrate"):
        return 2
    if not _run_one_shot("safety-stop", env, label="safety-stop"):
        return 2

    specifications = [
        ("django", "web"),
        ("celery-worker", "worker"),
        ("mt5-worker", "mt5-worker"),
        ("celery-beat", "beat"),
    ]
    processes: list[tuple[str, subprocess.Popen[str], list[str]]] = []
    stopping = threading.Event()

    def request_stop(_signal: int, _frame: Any) -> None:
        stopping.set()

    for signal_name in ("SIGINT", "SIGTERM", "SIGBREAK"):
        signal_value = getattr(signal, signal_name, None)
        if signal_value is not None:
            try:
                signal.signal(signal_value, request_stop)
            except (OSError, ValueError):
                pass

    try:
        for label, service in specifications:
            command = _service_command(service)
            process = start_process(command, env=env, label=label)
            processes.append((label, process, command))

        if not wait_for_backend(base_url):
            _write_supervisor_log(
                f"Backend did not become ready at {base_url}; startup aborted."
            )
            return 3
        _write_supervisor_log(f"Backend ready at {base_url}.")

        while not stopping.wait(1):
            if shutdown_file and shutdown_file.exists():
                _write_supervisor_log("Flutter requested backend shutdown.")
                break
            if parent_pid and not process_is_running(parent_pid):
                _write_supervisor_log("Flutter process exited; stopping backend.")
                break

            for index, (label, process, command) in enumerate(list(processes)):
                return_code = process.poll()
                if return_code is None:
                    continue
                close_process_log(process)
                _write_supervisor_log(
                    f"{label} exited with code {return_code}; restarting it."
                )
                replacement = start_process(command, env=env, label=label)
                processes[index] = (label, replacement, command)
        return 0
    except Exception:
        _write_supervisor_log(f"Supervisor failure:\n{traceback.format_exc()}")
        return 4
    finally:
        stop_processes(processes)
        _run_one_shot("safety-stop", env, label="safety-stop")
        if shutdown_file:
            try:
                shutdown_file.unlink(missing_ok=True)
            except OSError:
                pass
        _write_supervisor_log("Backend services stopped.")


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="EZ Trade local backend supervisor")
    parser.add_argument(
        "--check-runtime",
        action="store_true",
        help="Validate packaged dependencies and service configuration, then exit.",
    )
    parser.add_argument(
        "--service",
        choices=(
            "migrate",
            "safety-stop",
            "web",
            "worker",
            "mt5-worker",
            "beat",
        ),
    )
    parser.add_argument("--host", default=None)
    parser.add_argument("--port", type=int, default=None)
    parser.add_argument("--parent-pid", type=int, default=None)
    parser.add_argument("--shutdown-file", type=Path, default=None)
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    try:
        args = parse_args(argv)
        config = load_config(host=args.host, port=args.port)
        if args.check_runtime:
            return check_packaged_runtime(config)
        if args.service:
            return run_service(args.service, config)
        return run_supervisor(
            config,
            parent_pid=args.parent_pid,
            shutdown_file=args.shutdown_file,
        )
    except Exception:
        try:
            ensure_runtime_dirs()
            error_path = LOG_DIR / "launcher-error.log"
        except Exception:
            error_path = Path(os.getenv("TEMP", ".")) / "eztrade-launcher-error.log"
        with error_path.open("a", encoding="utf-8") as stream:
            traceback.print_exc(file=stream)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
