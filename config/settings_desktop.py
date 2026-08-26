"""Local Windows settings overlay using the project's PostgreSQL database."""

import os
from pathlib import Path

from .local_secrets import load_or_create_broker_creds_key

# Local app data root for desktop mode (logs, DB, media)
APPDATA = Path(os.getenv("APPDATA", Path.home() / "AppData" / "Roaming"))
DESKTOP_ROOT = APPDATA / "EzScalperBot"
DESKTOP_ROOT.mkdir(parents=True, exist_ok=True)

# Safe local-only defaults. Database and secret values still come from .env.
os.environ.setdefault("DJANGO_DEBUG", "False")
os.environ.setdefault("ALLOWED_HOSTS", "127.0.0.1,localhost")
os.environ.setdefault("API_ALLOW_OPEN", "False")
os.environ.setdefault("BROKER_CREDS_KEY", load_or_create_broker_creds_key())

# Use a shared filesystem queue so the desktop app has no Redis dependency.
QUEUE_ROOT = DESKTOP_ROOT / "celery"
QUEUE_IN = QUEUE_ROOT / "queue"
QUEUE_PROCESSED = QUEUE_ROOT / "processed"
RESULT_ROOT = QUEUE_ROOT / "results"
for directory in (QUEUE_IN, QUEUE_PROCESSED, RESULT_ROOT):
    directory.mkdir(parents=True, exist_ok=True)

os.environ.setdefault("CELERY_BROKER_URL", "filesystem://")
os.environ.setdefault("CELERY_RESULT_BACKEND", f"file:///{RESULT_ROOT.as_posix()}")

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
