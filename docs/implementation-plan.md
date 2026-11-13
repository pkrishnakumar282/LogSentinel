# Delivery plan

Each phase ends with executed checks, README updates and explicit user approval before the
next phase. No phase is considered complete based on directories alone.

| Phase | Deliverables | Verification |
|---|---|---|
| 1 | Architecture, monorepo, environment template, infrastructure Compose, preflight | Foundation tests, Compose render, environment report |
| 2 | Schemas, redaction, parsers, file/journal collectors, synthetic fixtures | Parser, cursor, malformed input and redaction tests |
| 3 | Shared causal features, TF-IDF/Isolation Forest and numerical baseline | Feature parity, deterministic training, split leakage tests |
| 4 | Holdout evaluation, MLflow tracking, registry approval gates | Labeled/unlabeled evaluation and registry integration |
| 5 | Inference service, single/batch prediction, version readiness | Artifact loading, thresholds, failed loading tests |
| 6 | Streams groups, outbox, retry recovery, alert grouping and transitions | Redis recovery, idempotency and alert tests |
| 7 | SQLAlchemy, Alembic, PostgreSQL repositories and gateway | PostgreSQL migrations, constraints, API auth/filter tests |
| 8 | React/TypeScript dashboard with eight pages and real APIs | Component tests, type check, build, browser integration |
| 9 | Service Dockerfiles, full Compose and health/resource policies | Image builds and complete-stack smoke tests |
| 10 | Metrics, drift summaries, Prometheus and Grafana provisioning | Scrape validation, metric and dashboard checks |
| 11 | GitHub Actions lint/types/tests/build/dependency jobs | Local equivalent checks; remote CI only when actually run |
| 12 | End-to-end validation, operational runbooks and final documentation | Ingest → prediction → alert → dashboard; recovery and retention |

Database interfaces may be introduced before phase 7 when needed by earlier services;
phase 7 completes persistence and integration. Full container startup is a phase 9 gate.

## Current progress

Phase 1 foundation is implemented. Phase 2 collectors, parsers, redaction, templates and
publishing adapters are implemented with 34 passing unit tests and two passing local
integration tests. Seven dependency-backed tests remain skipped due to the environment;
see [the validation record](ingestion.md).

Phase 3 shared causal features, train-only fitted preprocessing, two Isolation Forest variants,
configuration comparison, versioned dataset/split records and candidate Joblib artifacts are
implemented. Real model training/reload/reproducibility tests ran on Python 3.14 using cached
wheels; core tests also ran on Python 3.12.15. See [training validation](training.md).
Phase 4 evaluation, MLflow tracking and registry approval gates are implemented. Future
validation/test separation, frozen thresholds, labeled/unlabeled metrics, real SQLite-backed
MLflow runs/versions, token-protected registration, source/package integrity and audited
alias changes are tested. The default synthetic model failed its quality gates and remains
unapproved. Current checks: 77 unit tests and 23 integration tests passed; seven earlier
adapter tests skipped. See [Phase 4 validation](phase4-validation.md). Phase 5 is implemented: independently runnable approved-version inference, strict shared
HTTP contracts, single/batch scoring, bounded transactional contexts, numeric registry
version binding, readiness/model health and safe explicit reloads. Current-version
revocation withdraws readiness; failed different-version reloads preserve the last healthy
model. Real registered-model, CLI and ASGI tests pass. Current totals: 94 unit and 36
integration tests passed; seven earlier adapter tests skipped. Default inference remains
unready because the persisted model failed its quality gates. See
[Phase 5 validation](phase5-validation.md). Phase 6 is implemented: ordered Redis group adapters, pending recovery, bounded poison
retries, reason-only dead letters, safe retention watermarks, SQLAlchemy/Alembic durability,
prediction/checkpoint/outbox commits, idempotent alert grouping and audited API transitions.
Actual SQLite recovery/transaction, real-model checkpoint and ASGI tests pass. Current
checks: 102 unit and 54 integration tests passed; ten environment-dependent tests skipped.
Live Redis/PostgreSQL/Docker behavior remains unverified. The default model remains
unapproved. See [Phase 6 validation](phase6-validation.md). Phase 7 implements the application schema/provenance migration, independent archive consumer,
SQL-backed dashboard/log/source queries, bounded gateway proxies, real MLflow metadata,
durable asynchronous training jobs, lifecycle audit import and conservative retention.
Validation and environment limitations are recorded in [Phase 7 validation](phase7-validation.md).
Phase 8 implements all eight responsive dashboard pages against the versioned gateway,
including log/anomaly exploration, audited alert actions, model/job management, experiments,
sources and system status. `npm ci`, the component suite (3 tests), strict TypeScript check
and production build pass. The app uses route-level splitting; a live full-stack browser run
remains for integration phases. See [Phase 8 validation](phase8-validation.md).
Phase 9 awaits approval.
