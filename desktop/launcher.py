"""
Lightweight desktop launcher for EzScalperBot.

Responsibilities:
- Read config (desktop/config.sample.yml -> %APPDATA%/EzScalperBot/config.yml on first run).
- Start Django dev server + Celery worker + Celery beat as child processes.
- Open the existing web UI in a pywebview window.
- Shut everything down cleanly when the user exits.

This script is additive; it does not modify the web/server deployment.
"""

import os
import signal
import subprocess
import sys
import threading
import time
from pathlib import Path

import webview
import yaml

APPDATA = Path(os.getenv("APPDATA", Path.home() / "AppData" / "Roaming"))
DESKTOP_ROOT = APPDATA / "EzScalperBot"
DESKTOP_ROOT.mkdir(parents=True, exist_ok=True)

CONFIG_PATH = DESKTOP_ROOT / "config.yml"
SAMPLE_CONFIG = Path(__file__).parent / "config.sample.yml"
ENV_PATH = DESKTOP_ROOT / ".env"
LOG_DIR = DESKTOP_ROOT / "logs"

# Fallback default config (used if bundled sample is not found)
DEFAULT_CONFIG_YAML = """\
django:
  host: 127.0.0.1
  port: 4000
  settings_module: config.settings_desktop
celery:
  worker:
    args: "-A config worker -l info -Q celery --pool=solo --concurrency=1"
  mt5_worker:
    args: "-A config worker -l info -Q mt5_execution --pool=solo --concurrency=1"
  beat:
    args: "-A config beat -l info"
mt5:
  terminal_path: "C:/Program Files/MetaTrader 5/terminal64.exe"
  login: ""
  password: ""
  server: ""
logging:
  root_dir: "%APPDATA%/EzScalperBot/logs"
  max_bytes: 10485760
  backup_count: 5
ui:
  width: 1280
  height: 800
  title: "EzScalperBot Desktop"
"""


def ensure_config():
    if not CONFIG_PATH.exists():
        if SAMPLE_CONFIG.exists():
            contents = SAMPLE_CONFIG.read_text(encoding="utf-8")
        else:
            contents = DEFAULT_CONFIG_YAML
        CONFIG_PATH.write_text(contents, encoding="utf-8")
    LOG_DIR.mkdir(parents=True, exist_ok=True)


def load_config():
    ensure_config()
    with open(CONFIG_PATH, "r", encoding="utf-8") as f:
        return yaml.safe_load(f) or {}


def build_env(cfg):
    env = os.environ.copy()
    env.setdefault("DJANGO_SETTINGS_MODULE", cfg["django"].get("settings_module", "config.settings_desktop"))
    env.setdefault("PYTHONUNBUFFERED", "1")
    env.setdefault("ALLOW_SQLITE_DESKTOP", "1")
    # Point to a per-user .env if present; otherwise fall back to repo .env
    env.setdefault("ENV_FILE", str(ENV_PATH))
    return env


def start_process(cmd, cwd, env, label):
    """Start a managed child with output drained directly to a per-service log."""
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    log_stream = (LOG_DIR / f"{label}.log").open(
        "a",
        encoding="utf-8",
        buffering=1,
    )
    log_stream.write(f"\n[{time.strftime('%Y-%m-%d %H:%M:%S')}] starting {' '.join(cmd)}\n")
    try:
        proc = subprocess.Popen(
            cmd,
            cwd=cwd,
            env=env,
            stdout=log_stream,
            stderr=subprocess.STDOUT,
            creationflags=subprocess.CREATE_NEW_PROCESS_GROUP if os.name == "nt" else 0,
            text=True,
            bufsize=1,
        )
    except Exception:
        log_stream.close()
        raise
    proc._eztrade_log_stream = log_stream
    return proc


def close_process_log(proc):
    stream = getattr(proc, "_eztrade_log_stream", None)
    if stream and not stream.closed:
        stream.close()


def wait_for_http(url, timeout=30):
    import urllib.request

    start = time.time()
    while time.time() - start < timeout:
        try:
            with urllib.request.urlopen(url) as resp:
                if resp.status < 500:
                    return True
        except Exception:
            time.sleep(0.5)
    return False


def main():
    cfg = load_config()
    project_root = Path(__file__).resolve().parents[1]
    env = build_env(cfg)

    django_host = cfg["django"].get("host", "127.0.0.1")
    django_port = cfg["django"].get("port", 8000)
    base_url = f"http://{django_host}:{django_port}"

    procs = []
    procs_lock = threading.RLock()
    stopping = threading.Event()

    def add_process(label, cmd):
        proc = start_process(cmd, cwd=project_root, env=env, label=label)
        with procs_lock:
            procs.append((label, proc, cmd))
        return proc

    def stop_procs():
        if stopping.is_set():
            return
        stopping.set()
        with procs_lock:
            snapshot = list(procs)
        for _label, proc, _cmd in snapshot:
            try:
                if proc.poll() is not None:
                    continue
                if os.name == "nt":
                    proc.send_signal(signal.CTRL_BREAK_EVENT)
                else:
                    proc.terminate()
            except Exception:
                pass
        deadline = time.time() + 5
        while time.time() < deadline and any(proc.poll() is None for _, proc, _ in snapshot):
            time.sleep(0.1)
        for _label, proc, _cmd in snapshot:
            if proc.poll() is None:
                try:
                    proc.kill()
                except Exception:
                    pass
            close_process_log(proc)

    def supervise_procs():
        while not stopping.wait(2):
            with procs_lock:
                for index, (label, proc, cmd) in enumerate(list(procs)):
                    return_code = proc.poll()
                    if return_code is None:
                        continue
                    close_process_log(proc)
                    print(f"[{label}] exited with code {return_code}; restarting")
                    try:
                        replacement = start_process(
                            cmd,
                            cwd=project_root,
                            env=env,
                            label=label,
                        )
                    except Exception as exc:
                        print(f"[{label}] restart failed: {exc}")
                        continue
                    procs[index] = (label, replacement, cmd)

    supervisor = None
    try:
        # Start Django
        dj_cmd = [sys.executable, "manage.py", "runserver", f"{django_host}:{django_port}"]
        add_process("django", dj_cmd)

        # Start Celery worker
        worker_args = cfg["celery"]["worker"]["args"].split()
        add_process("celery-worker", [sys.executable, "-m", "celery", *worker_args])

        # MetaTrader5 owns process-global state, so its queue is consumed by
        # exactly one dedicated solo worker.
        mt5_worker_config = cfg["celery"].get(
            "mt5_worker",
            {
                "args": "-A config worker -l info -Q mt5_execution "
                "--pool=solo --concurrency=1"
            },
        )
        mt5_worker_args = mt5_worker_config["args"].split()
        add_process("mt5-worker", [sys.executable, "-m", "celery", *mt5_worker_args])

        # Start Celery beat
        beat_args = cfg["celery"]["beat"]["args"].split()
        add_process("celery-beat", [sys.executable, "-m", "celery", *beat_args])

        supervisor = threading.Thread(
            target=supervise_procs,
            name="eztrade-process-supervisor",
            daemon=True,
        )
        supervisor.start()

        # Give the server a moment to start
        if not wait_for_http(base_url, timeout=30):
            print("Web server did not become ready; check logs.")

        # Show UI
        window = webview.create_window(
            cfg.get("ui", {}).get("title", "EzScalperBot Desktop"),
            base_url,
            width=int(cfg.get("ui", {}).get("width", 1280)),
            height=int(cfg.get("ui", {}).get("height", 800)),
            resizable=True,
        )

        # Ensure child processes stop when window closes
        def on_closing():
            stop_procs()

        window.events.closing += on_closing
        webview.start()
    finally:
        stop_procs()
        if supervisor:
            supervisor.join(timeout=3)


if __name__ == "__main__":
    main()
