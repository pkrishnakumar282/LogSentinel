# Phase 7 validation record

Executed locally on 2026-11-13. Phase 7 is implemented; live-service deployment gates
remain explicitly unverified. No synthetic result is represented as production performance.

## Executed checks

- Python 3.14.10 / installed `.venv`: **102 unit tests passed**.
- Managed Python 3.12.15: **102 dependency-light unit tests passed**.
- Python 3.14.10 / installed `.venv`: **81 integration tests discovered, 71 passed,
  10 environment-dependent tests skipped**.
- Real Alembic SQLite upgrade/downgrade/re-upgrade and populated Phase 6 → Phase 7
  provenance backfill passed. PostgreSQL DDL compiles with the actual SQLAlchemy dialect.
- New gateway/application integration coverage: **17 tests passed** (13 gateway,
  four migration/metadata tests). Tests cover auth/admin boundaries, CORS preflight,
  body limits, safe validation, SQL search/filters/pagination, timestamp validation,
  latest-model counts, deduplication, source counts, queue snapshots/claims, retention,
  safe upstream failures, readiness, lifecycle audit import and real MLflow reads.
- A real separate training subprocess trained and saved candidates from an explicitly
  synthetic canonical snapshot. Its job completed as **candidate_not_approved**.
- Real alert FastAPI ASGI requests through the gateway/client persisted an acknowledgement
  and redacted note in SQL history. This uses HTTPX ASGITransport, not a listening server.
- Existing real MLflow training/evaluation/registration, feature parity, durable recovery
  and alert tests continue to pass. Temporary test registrations use explicit development
  test policy, not the default production gate policy.
- Full base + ingestion + MLflow + inference + streams + gateway Compose configuration,
  including the training-job profile, rendered successfully with temporary validation
  environment values. No credentials were printed or written into project files.
- Python compileall passed; installed `.venv` `pip check` reported no broken requirements.

Commands:

```bash
.venv/bin/python -m unittest discover -s tests/unit -q
.venv/bin/python -m unittest discover -s tests/integration -q
python3.12 -m unittest discover -s tests/unit -q
.venv/bin/python -m compileall -q shared services scripts tests infrastructure/postgres/migrations
.venv/bin/python -m pip check
```

The managed Python 3.12 executable was invoked by its available environment path; use
`python3.12` on a normal Arch installation. Existing runtime dependency pins were retained.
MLflow emits upstream SQLAlchemy/Python deprecation warnings; they did not fail tests.

## Limits of this evidence

Gateway ASGI tests inject a synchronous executor because this sandbox denies threadpool
socket wakeups. They exercise actual routing, middleware, validation and SQL, but do not
prove listening HTTP, production threadpool scheduling, network timeouts or concurrency.
Most upstream failure/credential tests use an explicitly declared HTTPX MockTransport.
One gateway → alert integration uses the actual alert ASGI application. Existing stream
failure-injection tests use the declared memory broker; this is not live Redis evidence.

Ten tests remain skipped: earlier restricted HTTP TestClient cases, unavailable Drain3,
real Redis integrations and the opt-in PostgreSQL integration. Redis/Psycopg packages
and service binaries are absent, package network access is restricted, and the Docker
daemon is inaccessible. Live PostgreSQL migration/locking/advisory locks, real Redis
consumer recovery/all-group trim, image builds, container startup and the compiled Python
3.12 ML stack remain host-side verification gates. No GitHub workflow was run in Phase 7.

Retention intentionally preserves every scored/archived stream record and its recovery
references. It can remove eligible old offline/quarantine content, but does not yet bound
stream history growth. Lifecycle audit import is explicit and bounded; job workers are
on demand. CPU/memory output is scoped to the gateway process; Redis stream-length probes are included but remain unverified against a real broker;
Prometheus/backlog/host monitoring is still Phase 10.

The persisted default model still fails its fixed quality gates and remains unapproved.
Ingestion/archive/read APIs do not need that model. Prediction APIs return safe unavailable
responses until an actual approved version can load. No fake dashboard records or ML
metrics are seeded by the gateway.

See [Phase 7 operating instructions](gateway-and-database.md) for startup, API requests,
audit import, training workers and retention. Phase 8 requires user approval.
