"""Capture code identity at process startup, never by rereading HEAD per trade."""
import json
import re
import subprocess
import sys
from pathlib import Path


def valid_sha(value):
    return isinstance(value, str) and re.fullmatch(r"[0-9a-f]{40}|[0-9a-f]{64}", value) is not None


def capture_build_identity(root=None, *, frozen=None):
    root = Path(root) if root is not None else Path(__file__).resolve().parents[1]
    frozen = getattr(sys, "frozen", False) if frozen is None else frozen
    if frozen:
        try:
            manifest = json.loads((root / "core" / "build-info.json").read_text(encoding="utf-8"))
            if manifest.get("status") == "clean" and valid_sha(manifest.get("sha")):
                return {"sha": manifest["sha"], "status": "clean", "source": "build_manifest"}
            return {"sha": None, "status": "dirty" if manifest.get("status") == "dirty" else "unavailable", "source": "build_manifest"}
        except (OSError, ValueError, AttributeError):
            return {"sha": None, "status": "unavailable", "source": "build_manifest"}
    try:
        def git(*args):
            return subprocess.run(["git", "-C", str(root), *args], check=True, capture_output=True,
                                  text=True, timeout=5, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0)).stdout.strip()
        if Path(git("rev-parse", "--show-toplevel")).resolve() != root.resolve():
            raise ValueError("Source root is not the repository root")
        sha = git("rev-parse", "HEAD")
        if not valid_sha(sha):
            raise ValueError("Invalid revision")
        if git("status", "--porcelain", "--untracked-files=normal"):
            return {"sha": None, "status": "dirty", "source": "source_checkout"}
        return {"sha": sha, "status": "clean", "source": "source_checkout"}
    except (OSError, ValueError, subprocess.SubprocessError):
        return {"sha": None, "status": "unavailable", "source": "source_checkout"}
