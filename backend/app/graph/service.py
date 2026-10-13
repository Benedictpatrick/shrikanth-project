"""Run lifecycle: create, execute in the background, pause at human gates, resume."""
from __future__ import annotations

import logging
import shutil
import threading
import traceback
import zipfile
from pathlib import Path

from langgraph.types import Command

from ..config import settings
from . import pipeline
from .store import store

log = logging.getLogger(__name__)
_locks: dict[str, threading.Lock] = {}
SKIP_DIRS = {".git", "node_modules", "__pycache__", ".venv", "output"}


def _lock(run_id: str) -> threading.Lock:
    return _locks.setdefault(run_id, threading.Lock())


def create_run(name: str, source: Path | None, upload_zip: Path | None, options: dict) -> str:
    run_id = store.create(name, options)
    dest = store.source_dir(run_id)
    dest.mkdir(parents=True, exist_ok=True)
    if source is not None:
        _copy_tree(source, dest)
    elif upload_zip is not None:
        _extract_zip(upload_zip, dest)
    store.emit(run_id, "created", f"Run created from {name}")
    return run_id


def _copy_tree(src: Path, dest: Path) -> None:
    for p in src.rglob("*"):
        rel = p.relative_to(src)
        if any(part in SKIP_DIRS for part in rel.parts) or not p.is_file():
            continue
        if p.stat().st_size > 5_000_000:
            continue
        target = dest / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(p, target)


def _extract_zip(zip_path: Path, dest: Path) -> None:
    root = dest.resolve()
    with zipfile.ZipFile(zip_path) as zf:
        total = 0
        for info in zf.infolist():
            if info.is_dir():
                continue
            target = (dest / info.filename).resolve()
            if root not in target.parents:  # zip-slip guard
                raise ValueError(f"unsafe path in archive: {info.filename}")
            total += info.file_size
            if total > 100_000_000:
                raise ValueError("archive is larger than 100 MB uncompressed")
            target.parent.mkdir(parents=True, exist_ok=True)
            with zf.open(info) as r, open(target, "wb") as w:
                shutil.copyfileobj(r, w)


def start_run(run_id: str) -> None:
    run = store.get(run_id)
    initial = {"run_id": run_id, "options": run["options"]}
    _spawn(run_id, initial)


def resume_run(run_id: str, value: dict) -> None:
    run = store.get(run_id)
    if run is None or run["status"] != "awaiting_approval":
        raise ValueError("run is not waiting for an approval")
    store.update(run_id, status="running", pending=None)
    store.emit(run_id, "approval", f"Human decision received: {value.get('decision') or ('approved' if value.get('approved') else 'rejected')}")
    _spawn(run_id, Command(resume=value))


def _spawn(run_id: str, payload) -> None:
    t = threading.Thread(target=_execute, args=(run_id, payload), daemon=True, name=f"run-{run_id}")
    t.start()


def _execute(run_id: str, payload) -> None:
    config = {"configurable": {"thread_id": run_id}, "recursion_limit": 200}
    with _lock(run_id):
        try:
            app = pipeline.get_app()
            store.update(run_id, status="running", error=None)
            app.invoke(payload, config)
            snap = app.get_state(config)
            interrupts = [i for t in snap.tasks for i in (t.interrupts or [])]
            if interrupts:
                store.update(run_id, status="awaiting_approval", pending=interrupts[0].value,
                             stage=interrupts[0].value.get("type", "approval"))
                store.emit(run_id, "approval", interrupts[0].value.get("message", "Waiting for approval"))
            else:
                status = snap.values.get("status", "completed")
                store.update(run_id, status="completed" if status == "completed" else status, stage="done")
        except Exception as exc:  # surface any failure to the UI instead of dying silently
            log.exception("run %s failed", run_id)
            store.update(run_id, status="failed", error=f"{type(exc).__name__}: {exc}")
            store.emit(run_id, "error", f"{type(exc).__name__}: {exc}", "error",
                       {"traceback": traceback.format_exc(limit=8)})


def sample_projects() -> list[dict]:
    out = []
    if settings.samples_dir.is_dir():
        for d in sorted(p for p in settings.samples_dir.iterdir() if p.is_dir()):
            progs = [f.name for f in d.rglob("*") if f.suffix.lower() in (".cbl", ".cob", ".cobol")]
            out.append({"id": d.name, "path": str(d), "programs": sorted(progs)})
    return out
