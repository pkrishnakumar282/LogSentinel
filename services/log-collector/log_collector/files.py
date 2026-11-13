"""Bounded file polling: binary byte offsets, stable IDs, partial-line protection."""
from datetime import UTC, datetime
import hashlib
from pathlib import Path
import stat
from uuid import NAMESPACE_URL, uuid4, uuid5

from shared.schemas import InvalidRecord, RawLog
from shared.schemas.logs import MAX_MESSAGE_BYTES


class FileCollector:
    def __init__(self, path: str, source: str, service: str, hostname: str, store, transport,
                 synthetic: bool = False):
        self.path = Path(path).resolve()
        self.source, self.service, self.hostname = source, service, hostname
        self.store, self.transport, self.synthetic = store, transport, synthetic
        self.key = f"file:{self.path}:{source}:{service}"

    def poll(self, limit: int = 1000) -> dict:
        if limit < 1:
            raise ValueError("invalid_poll_limit")
        counts = {"accepted": 0, "duplicate": 0, "rejected": 0}
        with self.path.open("rb") as stream:
            import os
            info = os.fstat(stream.fileno())
            if not stat.S_ISREG(info.st_mode):
                raise ValueError("not_a_regular_file")
            state = self.store.get(self.key)
            prefix_length = state.get("prefix_length", 0)
            prefix = hashlib.sha256(stream.read(prefix_length)).hexdigest()
            if (not state or state.get("identity") != [info.st_dev, info.st_ino]
                    or info.st_size < state.get("offset", 0) or state.get("prefix") != prefix):
                prefix_length = min(64, info.st_size)
                stream.seek(0)
                state = {"identity": [info.st_dev, info.st_ino], "offset": 0,
                         "generation": str(uuid4()), "prefix_length": prefix_length,
                         "prefix": hashlib.sha256(stream.read(prefix_length)).hexdigest()}
                # Persist generation before delivery so a failed first delivery keeps its event ID.
                self.store.put(self.key, state)
            stream.seek(state["offset"])
            for _ in range(limit):
                start = stream.tell()
                line = stream.readline(MAX_MESSAGE_BYTES + 2)
                if not line:
                    break
                oversized = len(line) > MAX_MESSAGE_BYTES
                if oversized:
                    # Skip oversized records in bounded memory. Stop at the snapshot's EOF.
                    while line and not line.endswith(b"\n"):
                        line = stream.readline(MAX_MESSAGE_BYTES + 2)
                    if not line.endswith(b"\n"):
                        break  # wait for completion; preserve cursor
                elif not line.endswith(b"\n"):
                    break
                event_id = str(uuid5(NAMESPACE_URL, f"{self.key}:{state['generation']}:{start}"))
                try:
                    if oversized:
                        raise InvalidRecord("record_too_large")
                    raw = line.decode("utf-8").rstrip("\r\n")
                    record = RawLog(event_id, self.source, self.service, self.hostname,
                                    datetime.now(UTC).isoformat(), raw, self.synthetic)
                    status = self.transport.send(record)
                    if status not in counts:
                        raise ValueError("invalid_transport_receipt")
                except (UnicodeDecodeError, InvalidRecord):
                    status = "rejected"
                counts[status] += 1
                state["offset"] = stream.tell()
                state["prefix_length"] = min(64, state["offset"])
                position = stream.tell()
                stream.seek(0)
                state["prefix"] = hashlib.sha256(stream.read(state["prefix_length"])).hexdigest()
                stream.seek(position)
                self.store.put(self.key, state)
        return counts
