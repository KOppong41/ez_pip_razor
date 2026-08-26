"""Local-only secret storage used by the Windows desktop development runtime."""

from __future__ import annotations

import os
import secrets
import time
from pathlib import Path


def load_or_create_broker_creds_key() -> str:
    """Return a stable per-user key without committing it to the repository."""
    local_root = Path(
        os.getenv("LOCALAPPDATA", Path.home() / "AppData" / "Local")
    ) / "EzScalperBot"
    local_root.mkdir(parents=True, exist_ok=True)
    key_path = local_root / "broker_creds.key"

    try:
        descriptor = os.open(key_path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    except FileExistsError:
        descriptor = None
    else:
        with os.fdopen(descriptor, "w", encoding="utf-8") as key_file:
            key_file.write(secrets.token_urlsafe(48))

    # Django's auto-reloader can import settings in two processes at once.
    for _ in range(20):
        value = key_path.read_text(encoding="utf-8").strip()
        if value:
            return value
        time.sleep(0.05)
    raise RuntimeError(f"Local broker credential key is empty: {key_path}")
