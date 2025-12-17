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
    args: "-A config worker -l info --concurrency=2"
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


def start_process(cmd, cwd, env):
    return subprocess.Popen(
        cmd,
        cwd=cwd,
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        creationflags=subprocess.CREATE_NEW_PROCESS_GROUP if os.name == "nt" else 0,
        text=True,
        bufsize=1,
    )


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


def tail_output(proc, label):
    for line in proc.stdout or []:
        print(f"[{label}] {line.rstrip()}")


def main():
    cfg = load_config()
    project_root = Path(__file__).resolve().parents[1]
    env = build_env(cfg)

    django_host = cfg["django"].get("host", "127.0.0.1")
    django_port = cfg["django"].get("port", 8000)
    base_url = f"http://{django_host}:{django_port}"

    procs = []

    def stop_procs():
        for _label, proc in procs:
            try:
                if os.name == "nt":
                    proc.send_signal(signal.CTRL_BREAK_EVENT)
                else:
                    proc.terminate()
            except Exception:
                pass
        time.sleep(2)
        for _label, proc in procs:
            if proc.poll() is None:
                try:
                    proc.kill()
                except Exception:
                    pass

    try:
        # Start Django
        dj_cmd = [sys.executable, "manage.py", "runserver", f"{django_host}:{django_port}"]
        procs.append(("django", start_process(dj_cmd, cwd=project_root, env=env)))

        # Start Celery worker
        worker_args = cfg["celery"]["worker"]["args"].split()
        procs.append(("celery-worker", start_process(["celery", *worker_args], cwd=project_root, env=env)))

        # Start Celery beat
        beat_args = cfg["celery"]["beat"]["args"].split()
        procs.append(("celery-beat", start_process(["celery", *beat_args], cwd=project_root, env=env)))

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


if __name__ == "__main__":
    main()
