import argparse
from datetime import UTC, datetime
import json
import os
from pathlib import Path
import socket
import sys
import time

from shared.common.publishing import EventConflict, PublicationUnavailable
from shared.schemas import InvalidRecord
from shared.schemas.logs import identifier

from .files import FileCollector
from .journal import JournalCollector
from .simulation import simulated_records
from .state import CursorStore
from .transport import HTTPTransport


class LocalTransport:
    def __init__(self, path: str, mode: str, timezone: str):
        from log_processor.normalizer import Normalizer
        from log_processor.pipeline import ProcessingPipeline
        from log_processor.templates import TemplateEngine
        from shared.common.publishing import SQLitePublisher
        self.pipeline = ProcessingPipeline(Normalizer(TemplateEngine(mode), timezone), SQLitePublisher(path))

    def send(self, record) -> str:
        try:
            return self.pipeline.ingest(record).status
        except InvalidRecord:
            return "rejected"


def main() -> int:
    parser = argparse.ArgumentParser(description="LogSentinel host collector (never requires root)")
    sub = parser.add_subparsers(dest="command", required=True)
    simulate = sub.add_parser("simulate", help="Emit labeled-as-synthetic envelope JSONL to stdout")
    simulate.add_argument("--count", type=int, default=100)
    simulate.add_argument("--seed", type=int, default=42)
    collect = sub.add_parser("collect")
    collect.add_argument("--config", default=os.environ.get("COLLECTOR_CONFIG", "docs/collector.example.json"))
    collect.add_argument("--state", default=os.environ.get("COLLECTOR_STATE", ".local/collector.sqlite3"))
    collect.add_argument("--once", action="store_true")
    collect.add_argument("--local-sink", help="Explicit offline development SQLite sink (not PostgreSQL)")
    collect.add_argument("--template-mode", choices=["drain3", "masked"], default=os.environ.get("TEMPLATE_MODE", "drain3"))
    health = sub.add_parser("health")
    health.add_argument("--state", default=os.environ.get("COLLECTOR_STATE", ".local/collector.sqlite3"))
    args = parser.parse_args()
    if args.command == "simulate":
        if not 1 <= args.count <= 100000:
            parser.error("count must be between 1 and 100000")
        for record in simulated_records(args.count, args.seed):
            print(json.dumps(record.to_dict()))
        return 0
    if args.command == "health":
        store = CursorStore(args.state)
        heartbeat = store.get("health")
        return 0 if heartbeat.get("ready") and time.time() - heartbeat.get("timestamp", 0) < 60 else 1
    store = CursorStore(args.state)
    try:
        config = json.loads(Path(args.config).read_text())
        if set(config) - {"hostname", "files", "journal", "poll_seconds"}:
            raise ValueError("unknown_collector_setting")
        if os.environ.get("COLLECTOR_FILES"):
            config["files"] = json.loads(os.environ["COLLECTOR_FILES"])
        if "JOURNAL_ENABLED" in os.environ:
            enabled = os.environ["JOURNAL_ENABLED"].lower()
            if enabled not in ("true", "false"):
                raise ValueError("invalid_journal_enabled")
            config.setdefault("journal", {})["enabled"] = enabled == "true"
        hostname = identifier(config.get("hostname", socket.gethostname()))
        interval = float(config.get("poll_seconds", 2))
        if not 0.1 <= interval <= 30:
            raise ValueError("invalid_poll_interval")
        if args.local_sink:
            transport = LocalTransport(args.local_sink, args.template_mode, os.environ.get("LOG_TIMEZONE", "UTC"))
        else:
            transport = HTTPTransport(os.environ.get("PROCESSOR_URL", "http://127.0.0.1:8012/api/v1/ingest"),
                                      os.environ.get("INGEST_TOKEN", ""))
        collectors = []
        for entry in config.get("files", []):
            if set(entry) - {"path", "source", "service", "synthetic"}:
                raise ValueError("unknown_file_setting")
            if entry["source"] not in ("linux", "nginx", "application"):
                raise ValueError("invalid_source")
            identifier(entry["service"])
            if type(entry.get("synthetic", False)) is not bool:
                raise ValueError("invalid_synthetic_flag")
            collectors.append(FileCollector(entry["path"], entry["source"], entry["service"],
                                            hostname, store, transport, entry.get("synthetic", False)))
        journal = config.get("journal", {})
        if set(journal) - {"enabled", "units"} or type(journal.get("enabled", False)) is not bool:
            raise ValueError("invalid_journal_setting")
        if journal.get("enabled", False):
            collectors.append(JournalCollector(hostname, store, transport, journal.get("units")))
        if not collectors:
            raise ValueError("no_collectors_configured")
        while True:
            failed = False
            for index, collector in enumerate(collectors):
                try:
                    counts = collector.poll()
                    # Source index and aggregate counts only: never paths or raw payloads.
                    print(json.dumps({"collector": index, **counts}), flush=True)
                except (PublicationUnavailable, OSError, EventConflict):
                    failed = True
                    print(json.dumps({"collector": index, "status": "unavailable"}), file=sys.stderr)
            store.put("health", {"ready": not failed, "timestamp": time.time(),
                                 "checked_at": datetime.now(UTC).isoformat()})
            if args.once:
                return 1 if failed else 0
            time.sleep(interval)
    except KeyboardInterrupt:
        return 0
    except (ValueError, KeyError, TypeError, OSError, ImportError):
        print('{"status":"error","reason":"collector_configuration_or_dependency"}', file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
