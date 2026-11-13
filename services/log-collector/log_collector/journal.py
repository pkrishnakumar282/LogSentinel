"""User journal collection without root, sudo or privileged container mounts."""
from datetime import UTC, datetime, timedelta
import json
import subprocess
from uuid import NAMESPACE_URL, uuid5

from shared.common.publishing import PublicationUnavailable
from shared.schemas import InvalidRecord, RawLog
from shared.schemas.logs import MAX_MESSAGE_BYTES


class JournalCollector:
    def __init__(self, hostname: str, store, transport, units: list[str] | None = None):
        self.hostname, self.store, self.transport = hostname, store, transport
        self.units = units or []
        if any(not unit or unit.startswith("-") or len(unit) > 128 for unit in self.units):
            raise ValueError("invalid_journal_unit")
        self.key = "journal:user:" + ",".join(sorted(self.units))

    def command(self, state: dict) -> list[str]:
        command = ["journalctl", "--user", "--output=json", "--no-pager", "--quiet",
                   "--output-fields=MESSAGE,PRIORITY,_SYSTEMD_USER_UNIT,_SYSTEMD_UNIT,"
                   "SYSLOG_IDENTIFIER,_HOSTNAME,__REALTIME_TIMESTAMP,__CURSOR"]
        for unit in self.units:
            command.extend(["--user-unit", unit])
        if state.get("cursor"):
            command.append(f"--after-cursor={state['cursor']}")
        else:
            command.append(f"--since={state['since']}")
        return command

    def poll(self, limit: int = 1000) -> dict:
        if limit < 1:
            raise ValueError("invalid_poll_limit")
        state = self.store.get(self.key)
        if not state:
            state = {"since": (datetime.now(UTC) - timedelta(minutes=5)).isoformat()}
            self.store.put(self.key, state)
        counts = {"accepted": 0, "duplicate": 0, "rejected": 0}
        try:
            process = subprocess.Popen(self.command(state), stdout=subprocess.PIPE,
                                       stderr=subprocess.DEVNULL)
        except OSError:
            raise PublicationUnavailable("journal_unavailable") from None
        try:
            assert process.stdout is not None
            for _ in range(limit):
                line = process.stdout.readline(MAX_MESSAGE_BYTES * 2 + 1)
                if not line:
                    break
                if len(line) > MAX_MESSAGE_BYTES * 2:
                    # Do not checkpoint data we cannot safely identify.
                    raise PublicationUnavailable("journal_record_too_large")
                try:
                    value = json.loads(line)
                    cursor = value["__CURSOR"]
                    if not isinstance(cursor, str) or not cursor or len(cursor) > 4096:
                        raise ValueError
                except (ValueError, TypeError, KeyError):
                    raise PublicationUnavailable("invalid_journal_record") from None
                event_id = str(uuid5(NAMESPACE_URL, f"{self.hostname}:journal:{cursor}"))
                try:
                    record = RawLog(event_id, "linux", "journal", self.hostname,
                                    datetime.now(UTC).isoformat(), line.decode("utf-8"))
                    status = self.transport.send(record)
                    if status not in counts:
                        raise PublicationUnavailable("invalid_transport_receipt")
                except (InvalidRecord, UnicodeDecodeError):
                    status = "rejected"
                counts[status] += 1
                state["cursor"] = cursor
                self.store.put(self.key, state)
            if not line:
                try:
                    code = process.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    raise PublicationUnavailable("journal_timeout") from None
                if code != 0:
                    raise PublicationUnavailable("journal_unavailable")
        finally:
            if process.poll() is None:
                process.terminate()
                try:
                    process.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait(timeout=5)
            if process.stdout:
                process.stdout.close()
        return counts
