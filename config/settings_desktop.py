"""Local Windows settings overlay using the project's PostgreSQL database."""

import os
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

# Safe local-only defaults. Database and secret values still come from .env.
os.environ.setdefault("DJANGO_DEBUG", "False")
os.environ.setdefault("ALLOWED_HOSTS", "127.0.0.1,localhost")
os.environ.setdefault("API_ALLOW_OPEN", "False")
os.environ.setdefault("BROKER_CREDS_KEY", load_or_create_broker_creds_key())
os.environ.setdefault("MT5_AUTO_ENABLE_ALGO_TRADING", "True")

# Use a shared filesystem queue so the desktop app has no Redis dependency.
QUEUE_ROOT = DESKTOP_ROOT / "celery"
QUEUE_IN = QUEUE_ROOT / "queue"
QUEUE_PROCESSED = QUEUE_ROOT / "processed"
RESULT_ROOT = QUEUE_ROOT / "results"
for directory in (QUEUE_IN, QUEUE_PROCESSED, RESULT_ROOT):
    directory.mkdir(parents=True, exist_ok=True)

# Desktop mode deliberately overrides server/compose Redis values. All local
# processes share these per-user folders, so the app remains self-contained.
os.environ["CELERY_BROKER_URL"] = "filesystem://"
os.environ["CELERY_RESULT_BACKEND"] = f"file:///{RESULT_ROOT.as_posix()}"

from .settings import *  # noqa: E402,F401,F403

CELERY_BROKER_TRANSPORT_OPTIONS = {
    "data_folder_in": str(QUEUE_IN),
    "data_folder_out": str(QUEUE_IN),
    "data_folder_processed": str(QUEUE_PROCESSED),
}

# Override paths to keep desktop artifacts isolated from the repo tree.
STATIC_ROOT = DESKTOP_ROOT / "staticfiles"
MEDIA_ROOT = DESKTOP_ROOT / "media"

# Ensure local static dir exists to avoid collectstatic errors.
STATIC_ROOT.mkdir(parents=True, exist_ok=True)
MEDIA_ROOT.mkdir(parents=True, exist_ok=True)
