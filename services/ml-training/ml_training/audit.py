"""Durable local lifecycle ledger, mirrored read-only into application audit_events by shared.application.audit_sync."""
from contextlib import closing, contextmanager
from datetime import UTC, datetime
import fcntl
import json
from pathlib import Path
import sqlite3
from uuid import uuid4


class LifecycleAudit:
    def __init__(self, path: str):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with closing(sqlite3.connect(self.path)) as db, db:
            db.execute("CREATE TABLE IF NOT EXISTS audit_events (id TEXT PRIMARY KEY, timestamp TEXT NOT NULL, "
                       "action TEXT NOT NULL, actor TEXT NOT NULL, status TEXT NOT NULL, details TEXT NOT NULL)")

    @contextmanager
    def lock(self):
        # Serialize registry mutations on this host; no network side effect is hidden in a DB transaction.
        with self.path.with_suffix(".lock").open("a") as stream:
            fcntl.flock(stream, fcntl.LOCK_EX)
            try:
                yield
            finally:
                fcntl.flock(stream, fcntl.LOCK_UN)

    def record(self, action: str, actor: str, status: str, details: dict) -> str:
        identity = str(uuid4())
        with closing(sqlite3.connect(self.path)) as db, db:
            db.execute("INSERT INTO audit_events VALUES (?, ?, ?, ?, ?, ?)",
                       (identity, datetime.now(UTC).isoformat(), action, actor, status, json.dumps(details, sort_keys=True)))
        return identity

    def entries(self):
        with closing(sqlite3.connect(self.path)) as db:
            rows = db.execute("SELECT id,timestamp,action,actor,status,details FROM audit_events ORDER BY rowid").fetchall()
        return [dict(zip(["id", "timestamp", "action", "actor", "status", "details"], row, strict=True))
                | {"details": json.loads(row[-1])} for row in rows]

    def approved_registration(self, name: str, version: str):
        return next((row for row in reversed(self.entries()) if row["action"] == "register"
                     and row["status"] == "succeeded" and row["details"].get("model_name") == name
                     and str(row["details"].get("version")) == str(version)), None)
