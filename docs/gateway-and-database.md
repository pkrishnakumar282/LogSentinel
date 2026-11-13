# Phase 7: application database and API gateway

The gateway serves real SQL-backed dashboard/log/source queries and forwards ingestion,
exploratory predictions and alert actions to their owning services. Business logic remains
in shared repositories and separate service processes. No model is trained inside an HTTP
request, and a successful training job does not approve or deploy its candidate.

## Run locally on Arch Linux

Use the [Arch installation guide](local-development.md) for Python 3.12, PostgreSQL,
Redis and Docker installation. Run all commands from the repository root. Python 3.12
is the container target; this sandbox's complete installed stack was exercised on 3.14.

```bash
python3.12 -m venv .venv
source .venv/bin/activate
python -m pip install -r services/api-gateway/requirements.txt
python -m pip install -r services/ml-training/requirements-worker.txt
cp --no-clobber .env.example .env
```

Generate independent secrets with `python -c 'import secrets; print(secrets.token_urlsafe(32))'`.
Set POSTGRES_PASSWORD, REDIS_PASSWORD, INGEST_TOKEN, INFERENCE_API_TOKEN,
ALERT_API_TOKEN, GATEWAY_API_TOKEN and ADMIN_API_TOKEN in `.env`. Never reuse example
values. Keep `.env` private; it is ignored. For native processes:

```bash
set -a
source .env
set +a
export PYTHONPATH="$PWD:$PWD/services/api-gateway:$PWD/services/ml-training"
export POSTGRES_HOST=127.0.0.1 REDIS_HOST=127.0.0.1
python scripts/manage_pipeline.py upgrade
python -m api_gateway
```

The native gateway binds **127.0.0.1:8000**. `/docs` supplies OpenAPI; use the gateway
bearer token for reads, ingestion and exploratory prediction. Mutations to alerts,
training jobs and retention require the independent admin token. A missing admin token
disables those operations. Tokens are never embedded in frontend source or URLs.
Local shared-token access is an MVP boundary, not per-user authentication/RBAC.
Audit actors are operator-supplied identifiers rather than verified user identities.

For a dependency-light local database only, set `PIPELINE_DB_BACKEND=sqlite` and
`PIPELINE_SQLITE_PATH=.local/pipeline.sqlite3` before the migration. SQLite results do
not validate PostgreSQL locking or concurrency. Gateway live/readiness require no
approved model; inference can remain unavailable without hiding archived logs.

Run the archive consumer in another terminal with the same database/Redis settings:

```bash
python -m shared.application.archive
```

It validates and redacts normalized records, excludes authentication records, commits
canonical logs, provenance and a permanent receipt, then acknowledges Redis. It uses a
separate `archive-v1` consumer group, registers the inference group before consuming,
and uses the existing pending-first recovery and all-group trim protocol. Deploy the
archive before inference starts trimming a new stream. Existing streams cannot recover
records already trimmed before this consumer group existed.

## Docker overlay

```bash
docker compose -f docker-compose.yml -f compose.ingestion.yml -f compose.mlflow.yml \
  -f compose.inference.yml -f compose.streams.yml -f compose.gateway.yml up --build -d
```

The overlay starts gateway and archive and orders inference after archive readiness.
Gateway startup requires the migrated database, while other services can report degraded
health. API ports bind to loopback; Redis/PostgreSQL remain on the internal network.
A newly installed stack has no approved model, so inference health/prediction failures
are expected until the existing evaluation and promotion procedure succeeds. This
sandbox could render the Compose configuration but could not build/start containers.
Full default Compose integration remains Phase 9.

## APIs and examples

All following routes start with `/api/v1`:

| Method / route | Behavior |
| --- | --- |
| GET `/dashboard?hours=24` | SQL totals, latest-prediction counts, distributions, hourly event-time timeline, recent events |
| GET `/logs` | `q`, `source`, `service`, `severity`, `since`, `until`, `anomaly`, `limit`, `offset` |
| GET `/logs/{event_id}` | Canonical redacted event, prediction history and model thresholds |
| POST `/ingest` | Validate envelope and forward to processor; acceptance is not a durable SQL receipt |
| POST `/predict`, `/predict/batch` | Exploratory inference through inference service; stream predictions are the durable dashboard history |
| GET `/alerts`, `/alerts/{id}`, `/alerts/{id}/history` | Forward to alert manager |
| POST `/alerts/{id}/acknowledge`, `/resolve` | Admin only; owning service writes transition and audit atomically |
| GET `/sources` | Observed source/host, cumulative canonical records, first/last receipt, recent/stale status |
| GET `/models` | Bounded read-only MLflow registry models, versions and aliases |
| GET `/models/observed` | Model/config bindings actually used in SQL predictions |
| GET `/model/status` | Current inference-service model health |
| GET `/experiments` | Bounded real MLflow run names, parameters, metrics, status and timing |
| GET `/training/jobs`, `/training/jobs/{id}` | Persisted training queue and safe results |
| POST `/training/jobs` | Admin only; freeze an immutable event-ID snapshot and return 202 |
| GET `/audit-events?action=model_promote` | Application audit including imported lifecycle history |
| POST `/maintenance/retention` | Admin only; audited preview by default, explicit apply |
| GET `/system` | Dependency probes, retained Redis stream lengths and actual gateway process CPU/peak RSS |

```bash
curl --fail-with-body -H "Authorization: Bearer $GATEWAY_API_TOKEN" \
  'http://127.0.0.1:8000/api/v1/logs?source=nginx&severity=ERROR&limit=25'
curl --fail-with-body -H "Authorization: Bearer $ADMIN_API_TOKEN" \
  -H 'Content-Type: application/json' \
  -d '{"actor":"local-engineer","synthetic":true,"estimators":[100,200],"seed":42}' \
  http://127.0.0.1:8000/api/v1/training/jobs
```

Search treats `%` and `_` literally. Time bounds must include a timezone; pagination
is stable by event time/ID. Each event contributes at most once to normal/anomaly
counts, using its latest committed prediction. Unscored records have a separate count;
ERROR severity does not imply an anomaly. Timeline counts use event time, whereas source
freshness uses actual database receipt time. Sources show observed ingestion, not a
fabricated collector connection or proof every collected record reached storage.
The API never exposes raw messages, internal fingerprints or arbitrary record metadata.
Sanitization cannot guarantee removal of all sensitive prose; inspect local inputs.

## Asynchronous training

Install training-worker dependencies separately; the gateway never imports training logic.
Jobs accept 40–10,000 matching canonical records and explicitly select synthetic versus
local data. Mixed synthetic/real training is prohibited. Oversized selections use the
existing offline CLI rather than silent truncation. Only one queued/running job is
accepted at a time. No API argument controls filesystem paths, tracking URIs or commands.

After enqueuing, start one bounded worker job:

```bash
python -m ml_training.job_worker --once
```

Or using the same overlay list as above:

```bash
docker compose -f docker-compose.yml -f compose.ingestion.yml -f compose.mlflow.yml \
  -f compose.inference.yml -f compose.streams.yml -f compose.gateway.yml \
  --profile training-job run --rm training-job
```

Training is on demand and consumes no persistent worker resources by default. Omit
`--once` only for an intentionally managed continuous queue worker. The worker exports
frozen canonical IDs into its trusted job directory, creates provenance, and launches
the existing reproducible CLI in a separate process with a one-hour timeout. HTTP requests
return immediately after committing the queue record. PostgreSQL advisory/row locks
and SQLite immediate transactions serialize queue claims and retention. Job claim tokens
prevent stale completion; abandoned leases become failed on the next worker claim.
No automatic retry or model promotion occurs. Worker crashes may leave local artifacts;
review job directories before manual cleanup. The child environment excludes database,
Redis and admin/API secrets. Run workers under the local service account/container.

Set MLFLOW_TRACKING_URI on the worker for real experiment tracking. Docker supplies it.
Successful results include dataset version and tracking run ID when tracking is configured.
Artifacts remain under TRAINING_JOB_ROOT/job-id/artifacts; the API omits private paths.
Use the [Phase 4 workflow](model-lifecycle.md) for independent validation, registration
and promotion. The default synthetic model remains unapproved.

## Schema, migration and lifecycle history

`0002_application` adds data_sources, log_events and training_runs, query indexes and a
backfill of Phase 6 canonical records. Run `python scripts/manage_pipeline.py current`
to inspect revision. Existing alert/prediction/checkpoint tables remain intact. Roll back
only after stopping writers and backing up:

```bash
python scripts/manage_pipeline.py downgrade --revision 0001_pipeline
```

`normalized_logs` owns the only full message/template. `log_events` stores receipt identity,
source reference and timestamps; deleting eligible content sets its optional canonical
FK to null while preserving provenance. Source counts are cumulative, not current row counts.
Foreign keys protect alerts and model bindings. SQLAlchemy pools hide parameters and use
bounded PostgreSQL connection/statement/lock timeouts. No schema auto-creation occurs in
production APIs/workers.

Phase 4's lifecycle ledger remains the inference approval authority. Import its allowlisted,
redacted records into PostgreSQL application audit history after registration/promotion:

```bash
python -m shared.application.audit_sync --ledger .local/mlflow/lifecycle.sqlite3
```

The source is opened read-only, imports are idempotent, and bundle paths/secrets are omitted.
The import is explicit and bounded to 10,000 source entries; a larger ledger requires an
extended batch import before its complete history can be shown. No registry mutation is
performed through the gateway.

## Retention and operational limits

```bash
curl --fail-with-body -H "Authorization: Bearer $ADMIN_API_TOKEN" \
  -H 'Content-Type: application/json' -d '{"actor":"local-engineer","days":30,"dry_run":true}' \
  http://127.0.0.1:8000/api/v1/maintenance/retention
```

Apply by explicitly setting `dry_run:false`. Age is based on receipt time, preserving old
logs imported today. Batches are bounded and audit recorded. The conservative policy
preserves **all scored events, archived stream events (including pending inference),
checkpoint/idempotency receipts, alert history and active training snapshots**. It can
remove old unreferenced offline/quarantine content. It does not currently bound the size
of stream history; coordinated checkpoint/consumer compaction and archival are future
work. This limitation is intentional to avoid changing restart features or losing pending
work. No automatic deletion scheduler runs.

Upstream credentials are independent of browser credentials; redirects are disabled,
connect/read and overall request timeouts are bounded, and service errors never echo
arbitrary upstream bodies. The gateway has API token guards, body/rate limits and explicit
local CORS origins. MLflow listings are bounded snapshots, not a complete paginated registry.
`/system` reports gateway-process CPU/peak memory; Redis lengths count retained entries rather than consumer backlog. Host/container utilization,
latency series and Prometheus scraping are still Phase 10. No fake metrics substitute for them.

Implementation references: [SQLAlchemy correlated subqueries](https://docs.sqlalchemy.org/en/20/tutorial/data_select.html),
[FastAPI CORS](https://fastapi.tiangolo.com/tutorial/cors/) and
[HTTPX timeout controls](https://www.python-httpx.org/advanced/timeouts/).

## Phase 8 frontend

For Phase 8 React dashboard setup, pages, access settings and validation, see the
[frontend guide](frontend.md) and [validation record](phase8-validation.md). The UI reads the
versioned APIs described above. Compose static serving remains in Phase 9.
