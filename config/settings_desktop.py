"""Self-contained local Windows settings for the Flutter desktop app."""

import os
import secrets
from pathlib import Path

import environ

from .local_secrets import load_or_create_broker_creds_key

# Local app data root for desktop mode (logs, DB, media)
APPDATA = Path(os.getenv("APPDATA", Path.home() / "AppData" / "Roaming"))
DESKTOP_ROOT = Path(os.environ.get("EZTRADE_DESKTOP_ROOT", APPDATA / "EzScalperBot"))
DESKTOP_ROOT.mkdir(parents=True, exist_ok=True)

# Load the selected environment file before installing desktop fallbacks. In
# particular, preserve an existing BROKER_CREDS_KEY so previously encrypted
# MT5 passwords remain decryptable when switching from server to desktop mode.
PROJECT_ROOT = Path(__file__).resolve().parents[1]
ENV_PATH = Path(os.environ.get("ENV_FILE", DESKTOP_ROOT / ".env"))
if not ENV_PATH.exists():
    ENV_PATH = PROJECT_ROOT / ".env"
if ENV_PATH.exists():
    environ.Env.read_env(ENV_PATH, overwrite=False)

# A packaged first run has no project .env. Generate a stable per-user Django
# secret and use a local SQLite database unless the user explicitly configured
# a database in their own %APPDATA%/EzScalperBot/.env.
SECRET_PATH = DESKTOP_ROOT / "django_secret.key"
if not os.environ.get("DJANGO_SECRET_KEY"):
    if not SECRET_PATH.exists():
        SECRET_PATH.write_text(secrets.token_urlsafe(64), encoding="utf-8")
    os.environ["DJANGO_SECRET_KEY"] = SECRET_PATH.read_text(encoding="utf-8").strip()

if not os.environ.get("DATABASE_URL") and not os.environ.get("DB_NAME"):
    database_path = (DESKTOP_ROOT / "db.sqlite3").resolve()
    os.environ["DATABASE_URL"] = f"sqlite:///{database_path.as_posix()}"

# Safe local-only defaults.
os.environ.setdefault("DJANGO_DEBUG", "False")
os.environ.setdefault("ALLOWED_HOSTS", "127.0.0.1,localhost")
os.environ.setdefault("API_ALLOW_OPEN", "False")
os.environ.setdefault("ALLOW_SQLITE_DESKTOP", "True")
os.environ.setdefault("BROKER_CREDS_KEY", load_or_create_broker_creds_key())
os.environ.setdefault("MT5_AUTO_ENABLE_ALGO_TRADING", "True")

# Use a shared, priority-aware filesystem queue so the desktop app has no
# Redis dependency while urgent MT5 work can overtake queued entries.
QUEUE_ROOT = DESKTOP_ROOT / "celery"
QUEUE_IN = QUEUE_ROOT / "queue"
QUEUE_PROCESSED = QUEUE_ROOT / "processed"
QUEUE_CONTROL = QUEUE_ROOT / "control"
RESULT_ROOT = QUEUE_ROOT / "results"
for directory in (QUEUE_IN, QUEUE_PROCESSED, QUEUE_CONTROL, RESULT_ROOT):
    directory.mkdir(parents=True, exist_ok=True)

# Desktop mode deliberately overrides server/compose Redis values. All local
# processes share these per-user folders, so the app remains self-contained.
from .celery_priority_filesystem import register_transport

register_transport()
os.environ["CELERY_BROKER_URL"] = "priorityfilesystem://"
os.environ["CELERY_RESULT_BACKEND"] = f"file:///{RESULT_ROOT.as_posix()}"

from .settings import *  # noqa: E402,F401,F403

DESKTOP_MODE = True

if "sqlite" in DATABASES["default"]["ENGINE"]:
    DATABASES["default"].setdefault("OPTIONS", {})["timeout"] = 30

CELERY_BROKER_TRANSPORT_OPTIONS = {
    "data_folder_in": str(QUEUE_IN),
    "data_folder_out": str(QUEUE_IN),
    "processed_folder": str(QUEUE_PROCESSED),
    "control_folder": str(QUEUE_CONTROL),
}

# Override paths to keep desktop artifacts isolated from the repo tree.
STATIC_ROOT = DESKTOP_ROOT / "staticfiles"
MEDIA_ROOT = DESKTOP_ROOT / "media"

# Ensure local static dir exists to avoid collectstatic errors.
STATIC_ROOT.mkdir(parents=True, exist_ok=True)
MEDIA_ROOT.mkdir(parents=True, exist_ok=True)
