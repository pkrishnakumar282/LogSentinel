from contextlib import closing
import json
from pathlib import Path
import sqlite3


class CursorStore:
    """Single collector per state database; cursors contain no original log messages."""
    def __init__(self, path: str):
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        self.path = path
        with closing(sqlite3.connect(path)) as db, db:
            db.execute("CREATE TABLE IF NOT EXISTS cursors (name TEXT PRIMARY KEY, state TEXT NOT NULL)")

    def get(self, name: str) -> dict:
        with closing(sqlite3.connect(self.path)) as db, db:
            row = db.execute("SELECT state FROM cursors WHERE name=?", (name,)).fetchone()
        return json.loads(row[0]) if row else {}

    def put(self, name: str, state: dict) -> None:
        with closing(sqlite3.connect(self.path)) as db, db:
            db.execute("INSERT INTO cursors VALUES (?, ?) ON CONFLICT(name) DO UPDATE SET state=excluded.state",
                       (name, json.dumps(state)))
