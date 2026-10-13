"""Reviewable Git history for the migrated output repository."""
from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

IDENTITY = ["-c", "user.name=Legacy Code Automation", "-c", "user.email=lca@localhost",
            "-c", "commit.gpgsign=false", "-c", "core.autocrlf=false"]


def available() -> bool:
    return shutil.which("git") is not None


def _git(repo: Path, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(["git", *IDENTITY, *args], cwd=repo, capture_output=True, text=True, timeout=60)


def init_repo(repo: Path) -> bool:
    repo.mkdir(parents=True, exist_ok=True)
    if not available():
        return False
    if not (repo / ".git").exists():
        _git(repo, "init", "-q", "-b", "main")
    return True


def commit_all(repo: Path, message: str) -> str | None:
    """Stage everything and commit; returns the short hash (None if nothing changed)."""
    if not available() or not (repo / ".git").exists():
        return None
    _git(repo, "add", "-A")
    status = _git(repo, "status", "--porcelain")
    if not status.stdout.strip():
        return None
    proc = _git(repo, "commit", "-q", "-m", message)
    if proc.returncode != 0:
        return None
    return _git(repo, "rev-parse", "--short", "HEAD").stdout.strip() or None


def log(repo: Path, limit: int = 50) -> list[dict]:
    if not available() or not (repo / ".git").exists():
        return []
    proc = _git(repo, "log", f"-{limit}", "--pretty=format:%h%x1f%s%x1f%ad", "--date=iso")
    out = []
    for line in proc.stdout.splitlines():
        parts = line.split("\x1f")
        if len(parts) == 3:
            out.append({"hash": parts[0], "subject": parts[1], "date": parts[2]})
    return out
