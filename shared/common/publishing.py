"""Normalized-event publishers. SQLite is an explicit local development sink."""
from contextlib import contextmanager
from dataclasses import dataclass
import hashlib
import json
from pathlib import Path
import sqlite3

from shared.schemas import NormalizedLog


class PublicationUnavailable(RuntimeError):
    pass


class EventConflict(ValueError):
    pass


class QueueFull(PublicationUnavailable):
    pass


@dataclass(frozen=True)
class Receipt:
    event_id: str
    status: str  # accepted or duplicate


def fingerprint(event: NormalizedLog) -> str:
    value = event.to_dict()
    # Drain templates evolve as clusters learn. The same event remains the same event.
    value.pop("template")
    value["metadata"].pop("template_engine", None)
    return hashlib.sha256(json.dumps(value, sort_keys=True).encode()).hexdigest()


class SQLitePublisher:
    """Transactional dedup and sanitized persistence for offline development, not production DB."""
    def __init__(self, path: str):
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        self.path = path
        with self.connect() as db:
            db.execute("PRAGMA journal_mode=WAL")
            db.execute("CREATE TABLE IF NOT EXISTS events "
                       "(event_id TEXT PRIMARY KEY, fingerprint TEXT NOT NULL, payload TEXT NOT NULL)")

    @contextmanager
    def connect(self):
        db = sqlite3.connect(self.path, timeout=10)
        try:
            with db:
                yield db
        finally:
            db.close()

    def publish(self, event: NormalizedLog) -> Receipt:
        digest = fingerprint(event)
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            previous = db.execute("SELECT fingerprint FROM events WHERE event_id=?",
                                  (event.event_id,)).fetchone()
            if previous:
                if previous[0] != digest:
                    raise EventConflict("event_id_conflict")
                return Receipt(event.event_id, "duplicate")
            db.execute("INSERT INTO events VALUES (?, ?, ?)",
                       (event.event_id, digest, event.to_json()))
        return Receipt(event.event_id, "accepted")

    def ping(self) -> bool:
        with self.connect() as db:
            db.execute("SELECT 1")
        return True

    def export(self):
        with self.connect() as db:
            for row in db.execute("SELECT payload FROM events ORDER BY rowid"):
                yield row[0]


# Publishers never trim. Phase 6 consumers reclaim capacity only at all-group safe watermarks.
# Refuse writes at capacity rather than silently deleting unconsumed logs.
PUBLISH_SCRIPT = """
local previous = redis.call('GET', KEYS[2])
if previous then
  if previous == ARGV[1] then return 'duplicate' else return 'conflict' end
end
if redis.call('XLEN', KEYS[1]) >= tonumber(ARGV[3]) then return 'full' end
redis.call('XADD', KEYS[1], '*', 'event_id', ARGV[4], 'payload', ARGV[2])
redis.call('SET', KEYS[2], ARGV[1], 'EX', ARGV[5])
return 'accepted'
"""


class RedisPublisher:
    def __init__(self, host: str, port: int, password: str, stream: str,
                 capacity: int = 100000, dedup_ttl: int = 604800):
        from redis import Redis
        if not password or capacity < 1 or dedup_ttl < 1:
            raise ValueError("invalid_redis_configuration")
        self.client = Redis(host=host, port=port, password=password, decode_responses=True,
                            socket_connect_timeout=3, socket_timeout=5)
        self.stream, self.capacity, self.dedup_ttl = stream, capacity, dedup_ttl
        self.script = self.client.register_script(PUBLISH_SCRIPT)

    def publish(self, event: NormalizedLog) -> Receipt:
        from redis.exceptions import RedisError
        try:
            status = self.script(keys=[self.stream, f"{self.stream}:dedup:{event.event_id}"],
                                 args=[fingerprint(event), event.to_json(), self.capacity,
                                       event.event_id, self.dedup_ttl])
        except RedisError:
            raise PublicationUnavailable("redis_unavailable") from None
        if status == "conflict":
            raise EventConflict("event_id_conflict")
        if status == "full":
            raise QueueFull("stream_capacity_reached")
        return Receipt(event.event_id, status)

    def ping(self) -> bool:
        return bool(self.client.ping())
