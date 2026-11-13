# Phase 2: ingestion and normalization

## Implemented path

File or user journal → envelope → processor → strict parser → authentication filter →
redaction → template engine → normalized sink → receipt → collector checkpoint.

The default service path uses FastAPI/Pydantic, Drain3 and Redis Streams. A dependency-free
local path uses the same parsers and contracts with an explicit deterministic `masked`
template engine and a development SQLite sink. SQLite is not a replacement for the planned
PostgreSQL application database. Neither path performs anomaly detection yet.

The envelope contains `event_id` (UUID), `source`, `service`, `hostname`, `observed_at` (aware
ISO-8601), `raw` and `synthetic` (default false). The common normalized schema is implemented
in `shared/schemas/logs.py`. Standard-library dataclasses enforce the domain contract;
Pydantic adds strict HTTP input validation through `shared/schemas/ingestion.py`. Original messages are retained only after
redaction. Nginx access messages retain request and status; identifying access fields are
intentionally discarded. Unknown structured JSON fields never enter metadata.

## Run the dependency-free local pipeline

From the repository root, using Python 3.12 where installed (the core also runs on 3.14):

```bash
export PYTHONPATH="$PWD:$PWD/services/log-collector:$PWD/services/log-processor"
python -m log_collector collect --once \
  --config docs/collector.example.json \
  --local-sink .local/normalized.sqlite3 --template-mode masked
python -m log_processor --sink .local/normalized.sqlite3 --export
# Restart with the same command: no already-checkpointed records are read again.
```

Expected first-run summary: 2 access + 1 error + 2 application + 2 journal records accepted.
All are explicitly synthetic. Cursor state is `.local/collector.sqlite3`. Choose a fresh
`--state` and `--local-sink` to repeat a clean demonstration. Local runtime files are ignored
by Git and Docker builds. Export contains sanitized data only; do not commit real exports.

For continuous polling, omit `--once`. The worker prints only aggregate counts and source
indices; unavailable inputs set its heartbeat unready while other sources continue.

```bash
python -m log_collector simulate --count 100 --seed 42 \
  | python -m log_processor --sink .local/simulation.sqlite3 --template-mode masked
python -m log_processor --sink .local/simulation.sqlite3 --export
```

The simulator is deterministic and emits `synthetic: true`. Fixture labels in
`tests/fixtures/labels.json` describe controlled normal and unusual cases. They are never
inserted into normalized features, nor presented as independent model performance evidence.
An expected ERROR retry is explicitly labeled normal in the fixture annotations.

## Install service dependencies and run HTTP ingestion

In a Python 3.12 virtual environment with network access:

```bash
python -m pip install -r services/log-processor/requirements.txt
export PYTHONPATH="$PWD:$PWD/services/log-collector:$PWD/services/log-processor"
# Assign your generated value; keep it out of version control:
export INGEST_TOKEN="$(openssl rand -hex 24)"
# Start with a local development sink:
export PROCESSOR_SINK=sqlite
export PROCESSOR_SQLITE_PATH=.local/http-normalized.sqlite3
uvicorn log_processor.api:create_app --factory --host 127.0.0.1 --port 8012 --no-access-log
```

In another shell, set the same `INGEST_TOKEN` and `PYTHONPATH`, then:

```bash
export PROCESSOR_URL=http://127.0.0.1:8012/api/v1/ingest
python -m log_collector collect --config docs/collector.example.json \
  --state .local/http-collector.sqlite3 --once
```

The Python services read the process environment; they do not automatically load `.env`.
Compose injects variables explicitly. `COLLECTOR_CONFIG`/`COLLECTOR_STATE` select paths;
`COLLECTOR_FILES`, when nonempty, is a JSON array overriding the file list, and
`JOURNAL_ENABLED=true|false` overrides the config's journal switch. A fresh token in a different shell will fail auth.
The host collector itself requires no third-party packages when using HTTP.

API endpoints:

| Route | Behavior |
|---|---|
| `GET /health/live` | Process liveness |
| `GET /health/ready` | Sink connectivity |
| `POST /api/v1/ingest` | Bearer-protected single envelope; accepted/duplicate receipt |
| `GET /docs` | Processor OpenAPI UI, local development only |

Example request with synthetic data:

```bash
curl --fail-with-body http://127.0.0.1:8012/api/v1/ingest \
  -H "Authorization: Bearer $INGEST_TOKEN" -H 'Content-Type: application/json' \
  --data '{"event_id":"a99cb4d8-cbcb-4ee2-a500-e7a259819242","source":"application","service":"demo-api","hostname":"synthetic-host","observed_at":"2026-01-01T00:00:00Z","synthetic":true,"raw":"2026-01-01T00:00:00Z INFO demo-api task complete"}'
```

Invalid input returns 422 with a reason code, never the raw body. Conflicting reused IDs
return 409. Temporary publisher failure or full Redis capacity returns 503; the collector
retains its cursor. Auth failures return 401 and rate limiting returns 429. Actual request
bodies are capped at 400 KB (including escaped JSON); raw records are capped at 64 KiB.
Retries are bounded (three attempts by default) and redirects are refused to avoid forwarding
credentials. The endpoint has a per-process global rate limit; multi-worker coordinated
rate limiting belongs to the later gateway phase.

## Optional Docker ingestion stack

Set `POSTGRES_PASSWORD`, `REDIS_PASSWORD` and a random `INGEST_TOKEN` in `.env` first:

```bash
docker compose -f docker-compose.yml -f compose.ingestion.yml config --quiet
docker compose -f docker-compose.yml -f compose.ingestion.yml up --build -d
docker compose -f docker-compose.yml -f compose.ingestion.yml logs -f log-collector log-processor
docker compose -f docker-compose.yml -f compose.ingestion.yml down
```

Both application images run as UID 10001. The example container collector reads synthetic
fixture files via read-only mounts. Replace its JSON config/mounts for real local sources.
The processor binds to `127.0.0.1:8012` only; Redis/PostgreSQL remain private. Builds and
startup have not been executed in the restricted workspace. Full application Compose remains
a Phase 9 gate. Redis publisher script deduplicates IDs atomically with a configurable TTL
(default seven days). Event-ID uniqueness in downstream PostgreSQL will provide permanent
deduplication in later phases. The script supports a standalone Redis instance, not Redis
Cluster. At configured capacity it refuses new records rather than trimming unread messages;
consumer-aware retention/recovery comes in Phase 6. In this phase a full queue requires an
operator-controlled export/drain before further ingestion.

## Supported formats and operational limits

- Linux: journal JSON with `MESSAGE`, integer microsecond `__REALTIME_TIMESTAMP` and optional
  priority/unit/hostname; or RFC3164-style `Mon DD HH:MM:SS host service[pid]: message`.
- Nginx: standard common/combined access format, or standard error format.
- Python: JSON with ISO timestamp, message and optional level/severity/service/status_code;
  or `YYYY-MM-DD HH:MM:SS,mmm LEVEL service message` (ISO-aware timestamps also work).
- Naive timestamps use `LOG_TIMEZONE` (default UTC). Syslog's missing year is inferred from
  the closest adjacent year relative to collection; inferred-year metadata is explicit.
  Ambiguous DST wall times use the timezone's first occurrence. Prefer explicit offsets.
- HTTP 4xx maps to WARNING and 5xx to ERROR; severity never implies an anomaly label.
- Sensitive authentication services and known auth message patterns are dropped even if
  the original source is application logs. Redaction masks common credentials, cookies,
  bearer/basic auth, emails, IP addresses and complete URL query strings. It cannot promise
  complete PII removal from arbitrary prose; only collect approved, nonsensitive workloads.
- File checkpoints use byte offsets, inode/device identity, content prefix and a persisted
  generation UUID. Restart and append retain IDs; rename/replacement and detectable
  copytruncate reset the generation. Partial final lines wait for a newline. Invalid UTF-8
  and oversized complete lines are discarded with counts, without persisting their payloads.
- Polling does not drain unread bytes from an old renamed file after rotation. Use a rotation
  grace period or pause the writer until the collector catches up; lossless rotation needs
  open-descriptor tracking. Truncate-and-regrow with the same first 64 bytes can be undetectable.
  Use rename-based rotation with a grace period for this local implementation.
- Run only one collector process for each state database/input set. Cursor databases are
  local durable state, not distributed coordination. No local disk spool stores raw logs.
- Drain3 state is bounded to 4096 in-memory clusters and restarts with the processor. Template
  strings can evolve; the first accepted event stays immutable on duplicate retries. A
  frozen template reference and reproducible feature artifacts are later ML lifecycle work.

## Real user journal collection (opt-in)

Copy the JSON configuration and replace its files/hostname with approved local sources.
To enable the user journal, set `journal.enabled` to true and preferably list specific
application units, e.g. `demo-worker.service`. Run on the host as your current user.
The collector invokes `journalctl --user --output=json`, starting at the last five minutes
on first launch and resuming with `--after-cursor` only after receipts. It never uses sudo,
privileged mode, host journal mounts or system-wide permission changes. Authentication
records are rejected at the processor. Vacuumed/invalid cursors make collection unavailable;
explicitly reset the journal checkpoint after investigating the gap. This phase intentionally
supports the user journal; broader system logs can be ingested from already-readable syslog
files without elevating the application. No real journal data was collected during validation.

The [systemd documentation](https://www.freedesktop.org/software/systemd/man/255/journalctl.html)
defines JSON/cursor output. Format support follows the
[Nginx log module](https://nginx.org/en/docs/http/ngx_http_log_module.html); the default engine
uses the [Drain3 API](https://github.com/logpai/Drain3).

## Tests

```bash
python -W error::ResourceWarning -m unittest discover -s tests/unit -p 'test_*.py' -v
python -W error::ResourceWarning -m unittest discover -s tests/integration -p 'test_*.py' -v
# Dependency-backed tests require requirements-dev.txt and Python 3.12:
python -m pip install -r requirements-dev.txt
# For a disposable Redis instance accessible from this shell:
export RUN_REDIS_INTEGRATION=1
export REDIS_HOST=127.0.0.1
# Set REDIS_PASSWORD and REDIS_PORT for that instance, then rerun integration tests.
```

Do not interpret skipped tests as passes. The Redis integration test uses a unique stream,
removes only its own test keys, and validates actual XADD, dedup, conflict and backpressure.
The API tests validate authorization, redaction, validation errors, readiness and body limits.

## Executed Phase 2 validation (2026-11-13)

- 34 unit tests passed on Python 3.14.10, including parsers, normalization, redaction,
  stable IDs, restart, partial lines, rotation/truncation, invalid bytes/size, journal
  checkpoint behavior, bounded HTTP retries and duplicate/conflict persistence.
- Two subprocess integration tests passed: all three fixture sources → durable sanitized
  sink → export/restart, and 25 deterministic simulated events → accepted/duplicate replay.
- Seven dependency-backed tests skipped: five HTTP API tests, one actual Drain3 test and
  one Redis integration test. Required packages are absent and pip failed with DNS errors.
- Python compilation and base+ingestion Compose rendering passed. Docker builds/startup,
  actual Redis publication, actual Drain3 and FastAPI execution remain unverified.
- No real journal records, public datasets, model metrics or production data were used.

These constraints are outstanding validation gates, not evidence of successful runtime
integration. Install the pinned dependencies in Python 3.12 and run all adapter tests on a
host with Docker/Redis access before relying on the default service path.
