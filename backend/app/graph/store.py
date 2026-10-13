"""Run bookkeeping: SQLite index, per-run folders, event log."""
from __future__ import annotations

import json
import sqlite3
import threading
import time
import uuid
from pathlib import Path

from ..config import settings

_lock = threading.RLock()


class RunStore:
    def __init__(self, workspace: Path | None = None) -> None:
        self.workspace = Path(workspace or settings.workspace)
        self.workspace.mkdir(parents=True, exist_ok=True)
        self.db_path = self.workspace / "runs.db"
        with self._conn() as c:
            c.executescript("""
                CREATE TABLE IF NOT EXISTS runs(
                    id TEXT PRIMARY KEY, name TEXT, status TEXT, stage TEXT, created REAL, updated REAL,
                    options TEXT, error TEXT, pending TEXT);
                CREATE TABLE IF NOT EXISTS events(
                    id INTEGER PRIMARY KEY AUTOINCREMENT, run_id TEXT, ts REAL, stage TEXT,
                    level TEXT, message TEXT, data TEXT);
                CREATE INDEX IF NOT EXISTS ix_events_run ON events(run_id, id);
            """)

    def _conn(self) -> sqlite3.Connection:
        c = sqlite3.connect(self.db_path, timeout=30)
        c.row_factory = sqlite3.Row
        return c

    # -- paths -------------------------------------------------------------------------
    def run_dir(self, run_id: str) -> Path:
        return self.workspace / "runs" / run_id

    def source_dir(self, run_id: str) -> Path:
        return self.run_dir(run_id) / "source"

    def output_dir(self, run_id: str) -> Path:
        return self.run_dir(run_id) / "output"

    def work_dir(self, run_id: str) -> Path:
        return self.run_dir(run_id) / "work"

    def artifact_dir(self, run_id: str) -> Path:
        d = self.run_dir(run_id) / "artifacts"
        d.mkdir(parents=True, exist_ok=True)
        return d

    def write_artifact(self, run_id: str, name: str, data) -> None:
        p = self.artifact_dir(run_id) / name
        p.parent.mkdir(parents=True, exist_ok=True)
        tmp = p.with_suffix(p.suffix + ".tmp")
        tmp.write_text(json.dumps(data, indent=1, default=str), encoding="utf-8")
        tmp.replace(p)

    def read_artifact(self, run_id: str, name: str, default=None):
        p = self.artifact_dir(run_id) / name
        if not p.is_file():
            return default
        return json.loads(p.read_text(encoding="utf-8"))

    # -- runs ----------------------------------------------------------------------------
    def create(self, name: str, options: dict) -> str:
        run_id = time.strftime("%Y%m%d-%H%M%S-") + uuid.uuid4().hex[:6]
        now = time.time()
        with _lock, self._conn() as c:
            c.execute("INSERT INTO runs(id,name,status,stage,created,updated,options) VALUES(?,?,?,?,?,?,?)",
                      (run_id, name, "created", "created", now, now, json.dumps(options)))
        self.run_dir(run_id).mkdir(parents=True, exist_ok=True)
        return run_id

    def update(self, run_id: str, **fields) -> None:
        if "pending" in fields:
            fields["pending"] = json.dumps(fields["pending"]) if fields["pending"] is not None else None
        if "options" in fields:
            fields["options"] = json.dumps(fields["options"])
        fields["updated"] = time.time()
        cols = ", ".join(f"{k}=?" for k in fields)
        with _lock, self._conn() as c:
            c.execute(f"UPDATE runs SET {cols} WHERE id=?", (*fields.values(), run_id))

    def get(self, run_id: str) -> dict | None:
        with self._conn() as c:
            row = c.execute("SELECT * FROM runs WHERE id=?", (run_id,)).fetchone()
        return self._row(row) if row else None

    def list(self) -> list[dict]:
        with self._conn() as c:
            rows = c.execute("SELECT * FROM runs ORDER BY created DESC").fetchall()
        return [self._row(r) for r in rows]

    @staticmethod
    def _row(row: sqlite3.Row) -> dict:
        d = dict(row)
        d["options"] = json.loads(d["options"]) if d.get("options") else {}
        d["pending"] = json.loads(d["pending"]) if d.get("pending") else None
        return d

    # -- events ----------------------------------------------------------------------------
    def emit(self, run_id: str, stage: str, message: str, level: str = "info", data: dict | None = None) -> None:
        with _lock, self._conn() as c:
            c.execute("INSERT INTO events(run_id,ts,stage,level,message,data) VALUES(?,?,?,?,?,?)",
                      (run_id, time.time(), stage, level, message, json.dumps(data) if data else None))

    def events(self, run_id: str, after: int = 0, limit: int = 500) -> list[dict]:
        with self._conn() as c:
            rows = c.execute("SELECT * FROM events WHERE run_id=? AND id>? ORDER BY id LIMIT ?",
                             (run_id, after, limit)).fetchall()
        out = []
        for r in rows:
            d = dict(r)
            d["data"] = json.loads(d["data"]) if d["data"] else None
            out.append(d)
        return out


store = RunStore()
