"""
Desktop-friendly settings overlay.
- Forces a local SQLite database under %APPDATA%/EzScalperBot.
- Provides safe defaults for required env vars (tokens/hosts) so the app can boot offline.
- Leaves the main config/settings.py untouched for server/web deployments.
"""

import os
from pathlib import Path

# Local app data root for desktop mode (logs, DB, media)
APPDATA = Path(os.getenv("APPDATA", Path.home() / "AppData" / "Roaming"))
DESKTOP_ROOT = APPDATA / "EzScalperBot"
DESKTOP_ROOT.mkdir(parents=True, exist_ok=True)

# Allow SQLite and point DATABASE_URL before importing base settings.
os.environ.setdefault("ALLOW_SQLITE_DESKTOP", "1")
os.environ.setdefault("DATABASE_URL", f"sqlite:///{DESKTOP_ROOT / 'db.sqlite3'}")

# Relax host/debug for local runs.
os.environ.setdefault("DJANGO_DEBUG", "True")
os.environ.setdefault("ALLOWED_HOSTS", "127.0.0.1,localhost")

# Provide safe defaults for required tokens to avoid startup errors in desktop mode.
os.environ.setdefault("TOKEN", "")
os.environ.setdefault("SECRET", "")
os.environ.setdefault("URL", "")

# Optional: point Celery broker/result to local Redis if present; otherwise use in-memory.
os.environ.setdefault("CELERY_BROKER_URL", os.getenv("CELERY_BROKER_URL", "redis://localhost:6379/0"))
os.environ.setdefault("CELERY_RESULT_BACKEND", os.getenv("CELERY_RESULT_BACKEND", "redis://localhost:6379/1"))

from .settings import *  # noqa: E402,F401,F403

# Override paths to keep desktop artifacts isolated from the repo tree.
STATIC_ROOT = DESKTOP_ROOT / "staticfiles"
MEDIA_ROOT = DESKTOP_ROOT / "media"

# Ensure local static dir exists to avoid collectstatic errors.
STATIC_ROOT.mkdir(parents=True, exist_ok=True)
MEDIA_ROOT.mkdir(parents=True, exist_ok=True)
