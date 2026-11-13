# Phase 6 validation record

Executed locally on 2026-11-13. No public datasets or real production logs were used.

## Executed checks

- 102 unit tests passed on Python 3.14.10 and separately on Python 3.12.15.
- 54 integration tests passed on Python 3.14.10. The full integration discovery ran 64 tests
  with ten skips; imported test classes are not counted a second time.
- New checks cover 14 SQL transaction/recovery cases, one alert ASGI case, two migration/schema
  cases, and an additional real-model durable stream restart case.
- SQLite Alembic upgrade, downgrade and re-upgrade passed, including actual foreign-key rejection.
- The frozen schema compiled through SQLAlchemy's PostgreSQL dialect. This is not a live DB test.
- Six production Lua scripts passed syntax checks under the available Lua 5.5.1 interpreter.
  Two unit tests executed retention-watermark logic with Lua Redis-call stubs, including large
  stream IDs beyond floating-point integer precision. Redis command execution/atomicity and
  Redis's embedded Lua runtime were not validated by these checks.
- Compilation, installed dependency consistency and combined Compose configuration rendering passed.
- 96 distinct package pins across seven requirements files have no conflicts. Missing Redis/
  Psycopg drivers were not installed: the attempted package install returned no available versions.
- No unintended default MLflow database was created by the final regression suite.

## Recovery evidence

Actual SQLAlchemy transactions with SQLite and an explicitly test-only memory broker verified:

- Commit-before-ACK loss yields one durable prediction and skips rescoring on replay.
- A failed database commit restores the previous committed feature context before retry.
- Publishing before the outbox mark is replay-safe and creates no duplicate alert occurrence.
- An alert commit followed by a lost ACK is replayed without incrementing occurrence count.
- Missing approved models remain pending without spending poison retry attempts.
- Bounded data/logic retries end in a committed SQL failure record before dead-letter ACK.
- Malformed/conflicting input is quarantined without echoing its sensitive payload.
- A normal ERROR prediction does not alert; anomaly/warm-up/score policies govern eligibility.
- Recurrences group, acknowledgement/resolution are idempotent and audited, and a resolved
  alert is not silently reopened by a new occurrence.
- A stale checkpoint revision rolls back prediction and outbox insertion together.

The additional real-model integration trained/evaluated/registered temporary development
versions through the real MLflow stack, persisted 100 predictions, simulated loss of the ACK
around event 80, rebuilt context from committed SQL history, and reproduced the direct model
scores. These tests use a frozen permissive test policy to verify mechanics, not detection
quality. Their broker is the explicit memory test fixture, not a real Redis service.

The alert API test executed actual ASGI routing, shared token guards, SQL reads, action
transitions, validation and redacted history. Its synchronous test executor accommodates
sandbox-denied socket-pair wakeups. Listening HTTP/threadpool behavior was not tested.

## Skipped or unverified gates

Five earlier HTTP TestClient tests remain skipped due to sandbox socket-pair restrictions;
Drain3 and an earlier live Redis test are unavailable. Phase 6 adds two real Redis tests
and one real PostgreSQL disposable-schema test, also skipped without drivers/services and
explicit opt-in. Redis/PostgreSQL binaries are absent and Docker daemon access is denied.

No Redis server/Lua command semantics, actual PostgreSQL concurrency/locking/migrations,
container builds/startup, or compiled ML dependency stack on Python 3.12 was validated here.
The real adapters, opt-in live tests and localhost-only testing overlay are included for
execution on a normal Arch host. SQLite, memory-broker and PostgreSQL compilation checks
must not be represented as a substitute for those live-service results.

## Default model and remaining scope

The existing Phase 4 default model remains unapproved. Phase 6 does not generate fake
predictions/alerts or load candidate artifacts to bypass that gate. Valid incoming events
stay recoverable while the model is unavailable.

The SQL migration is the necessary Phase 6 durability foundation. Phase 7 still needs the
remaining application schema, gateway APIs and retention integration. Readiness/worker
heartbeats exist; Prometheus/Grafana instrumentation remains Phase 10. Late event-time
records are visibly quarantined; a live event-time reorder buffer is not implemented.

See [stream/alert setup and contracts](streams-and-alerts.md) for complete commands.
