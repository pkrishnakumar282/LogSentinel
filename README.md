# LogSentinel AI

Intelligent multi-source log anomaly detection and monitoring platform for local MLOps,
DevOps and SRE workflows. The intended platform collects Linux, Nginx and Python application
logs, learns normal patterns, and connects anomaly detection to investigation and alerting.

**Current status: Phase 8 React monitoring dashboard implemented.**
Ingestion, training, evaluation, approved-version inference, durable prediction checkpoints,
transactional outbox, Redis consumer adapters and grouped alerts with audited actions are
implemented. PostgreSQL is the worker default; SQLite is an explicit development backend.

Local verification uses actual SQLAlchemy/SQLite, real registered-model scoring, ASGI APIs
and a test-only broker for failure injection. Live Redis/PostgreSQL and Docker remain
unverified here. The default model still fails its quality gates and remains unapproved;
no successful production detection performance or fake alerts are claimed.

The gateway now exposes SQL-backed logs, dashboard statistics, source provenance, MLflow
metadata, alert actions and a persisted asynchronous training queue. An independent archive
consumer stores logs without an approved model. Retention is explicit and preserves stream
recovery history. The responsive React dashboard now covers all eight operational pages and reads the gateway APIs. Full container integration, Prometheus/Grafana and CI remain scheduled.

Use this repository root directly; an extra `logsentinel-ai/` nesting level is unnecessary.

- [Architecture, service boundaries and data flow](docs/architecture.md)
- [Twelve-phase implementation and validation plan](docs/implementation-plan.md)
- [Arch Linux setup and troubleshooting](docs/local-development.md)
- [Phase 2 ingestion setup, formats, API and limitations](docs/ingestion.md)
- [Phase 3 features, training and artifacts](docs/training.md)
- [Executed Phase 3 validation](docs/phase3-validation.md)
- [Phase 4 evaluation, MLflow and approval commands](docs/model-lifecycle.md)
- [Executed Phase 4 validation](docs/phase4-validation.md)
- [Phase 5 inference setup and API](docs/inference.md)
- [Executed Phase 5 validation](docs/phase5-validation.md)
- [Phase 6 streams, recovery and alert API](docs/streams-and-alerts.md)
- [Executed Phase 6 validation](docs/phase6-validation.md)
- [Phase 7 database, gateway, jobs and retention](docs/gateway-and-database.md)
- [Executed Phase 7 validation](docs/phase7-validation.md)
- [Frontend dashboard and local setup](docs/frontend.md)
- [Executed Phase 8 validation](docs/phase8-validation.md)
- [Dataset provenance policy](ml/datasets/README.md)

The stack uses React/TypeScript/Vite/Tailwind/Recharts, FastAPI/Pydantic/SQLAlchemy,
PostgreSQL, Redis Streams, scikit-learn, MLflow and Docker. Separate services own ingestion,
normalization, training, inference, alerting and the API. See the architecture document for
the diagram and ownership table.

## Start the Phase 1 infrastructure

Install prerequisites using the Arch setup guide, then:

```bash
cp .env.example .env
# Edit .env and set unique POSTGRES_PASSWORD and REDIS_PASSWORD values.
# Generate each value separately with: openssl rand -hex 24
python scripts/preflight.py
docker compose config --quiet
docker compose up -d --build
docker compose ps
docker compose logs -f
# Stop containers while retaining named volumes:
docker compose down
```

The base Compose file defines Redis and PostgreSQL. Neither publishes host ports. An optional
`compose.ingestion.yml` adds collector and processor services; see the ingestion guide. Full service
startup, including frontend, comes in Phase 9. Do not run `down -v` unless you intend to
delete persistent database and queue data. Credentials must be set before Compose renders;
never commit `.env`. Changing a PostgreSQL password variable does not change an existing
volume's database password; rotate through SQL using the existing credentials.

## Verify the foundation

```bash
python -m unittest discover -s tests/unit -p 'test_*.py' -v
python -m unittest discover -s tests/integration -p 'test_*.py' -v
python scripts/preflight.py --json
```

Core tests and the explicit local ingestion path use only Python's standard library and run
on Python 3.12 and 3.14. Real ML integration tests require the training service dependencies.
Backend deployment targets Python 3.12; its compiled ML dependency execution remains a gate. Adapter tests
skip visibly when their dependencies are unavailable. Direct service dependencies are pinned;
full transitive locking remains a deployment gate. Current Compose images pin release
lines, not immutable digests; deployment reproducibility will be tightened during Phase 9.

## Planned workflows

Training replays sanitized historical data with causal window features and compares TF-IDF
and numerical/frequency Isolation Forests on the same temporal holdout. It saves candidate
artifacts, split/dataset hashes, parameters and measured score/latency diagnostics. Phase 4
now adds labeled evaluation, real MLflow tracking and validation-gated registration/promotion.
Inference loads the fitted pipeline without refitting and records the numeric registry
version and uncalibrated score with every prediction. Synthetic development
fixtures will be explicitly identified. No accuracy claims will be made for unlabeled data.

The frontend provides overview, logs, anomalies, alerts, models, experiments, sources
and system monitoring pages backed by gateway APIs under `/api/v1/`. See the
[dashboard guide](docs/frontend.md) for setup and API behavior. Training commands are available now in
[the training guide](docs/training.md); HTTP prediction is implemented in
[the inference guide](docs/inference.md). The processor
ingestion API and request examples are in [the ingestion guide](docs/ingestion.md).

CI in Phase 11 will run linting, typing, backend/frontend tests, ML tests, infrastructure
integration, image builds and dependency checks. There is no external deployment workflow.

## Limitations and next steps

The current phases establish architecture, ingestion, training, evaluation and local MLflow lifecycle.
Local SQLite storage is for development only; the application database will use PostgreSQL.
Docker daemon access is required to validate container startup; configuration validation
alone is not evidence that containers are healthy. No cloud account is needed. Advanced
models, distributed collectors, SSO and multi-tenant policies remain future improvements
after the requested local platform is complete.

## Run Phase 2 locally without extra packages

```bash
export PYTHONPATH="$PWD:$PWD/services/log-collector:$PWD/services/log-processor"
python -m log_collector collect --once --config docs/collector.example.json \
  --local-sink .local/normalized.sqlite3 --template-mode masked
python -m log_processor --sink .local/normalized.sqlite3 --export
```

A fresh run accepts seven explicitly synthetic records across all three sources. Subsequent
runs resume from durable checkpoints. `masked` is an explicit offline template engine;
normal service startup requires Drain3. See the ingestion guide for real file/user-journal
configuration, authenticated HTTP mode, optional Docker services and test results.

## Train Phase 3 candidates

```bash
python -m pip install -r services/ml-training/requirements.txt
export PYTHONPATH="$PWD:$PWD/services/ml-training"
python -m ml_training make-synthetic --output ml/datasets/synthetic-phase3.jsonl --count 600
python -m ml_training train --dataset ml/datasets/synthetic-phase3.jsonl --output ml/artifacts
```

This creates four candidates on a 480/120 chronological split, with fitted preprocessing
and actual holdout score diagnostics. Synthetic smoke tests do not prove detection quality.
No candidate is approved automatically, and scores are not probabilities. The first smoke
run flagged no holdout events at the default threshold; Phase 4 evaluation/calibration is now implemented and the demonstrated model failed its
quality gates. [The training guide](docs/training.md) covers features, provenance,
artifact loading, warm-up behavior and the optional one-shot Docker profile.

## Phase 4 evaluation and MLflow

Install `services/ml-training/requirements-mlflow.txt`, then follow the
[lifecycle guide](docs/model-lifecycle.md) to track training, create future validation/test
data, freeze calibration and evaluate. Registration requires passing the frozen gates and
an admin token; promotion is separate and audited. Synthetic evidence cannot create a
production champion. The optional `compose.mlflow.yml` and `compose.lifecycle.yml` add a
localhost tracking server and on-demand jobs with persistent volumes.

Validation: 77 unit and 23 integration tests passed; seven existing adapter tests remain
skipped due to sandbox/dependency constraints. See [the full record](docs/phase4-validation.md).

## Run Phase 5 inference

```bash
python -m pip install -r services/ml-inference/requirements.txt
export PYTHONPATH="$PWD:$PWD/services/ml-inference"
python -m ml_inference check
# Set INFERENCE_API_TOKEN securely before starting HTTP:
python -m ml_inference serve --host 127.0.0.1 --port 8013
```

With the current unapproved default model, `check` returns exit 1 and HTTP readiness/prediction
return 503. There is no approval bypass. After legitimate registry approval, select a version
with `MODEL_VERSION` or resolve `MODEL_ALIAS`; see [request examples and configuration](docs/inference.md).
The optional `compose.inference.yml` connects the non-root service to MLflow and a read-only
audit volume. Docker and listening HTTP execution remain unverified in this sandbox.

Current verification: **94 unit and 36 integration tests passed**, with seven existing adapter
tests skipped. See [Phase 5 validation](docs/phase5-validation.md).

## Phase 6 durable pipeline and alerts

Install the inference/alert service dependencies and apply the first Alembic migration:

```bash
python -m pip install -r services/ml-inference/requirements.txt -r services/alert-manager/requirements.txt
export PYTHONPATH="$PWD:$PWD/services/ml-inference:$PWD/services/alert-manager"
# Export PostgreSQL and Redis connection variables from local secret configuration.
python scripts/manage_pipeline.py upgrade
python -m ml_inference worker
# Separate shells/processes:
python -m alert_manager worker
python -m alert_manager serve --host 127.0.0.1 --port 8014
```

The [stream/alert guide](docs/streams-and-alerts.md) covers secrets, explicit SQLite mode,
consumer leases/retries, dead letters, outbox relay, alert actions and optional Compose
services. Predictions/checkpoints/outbox commit before ACK; permanent receipts make replay
idempotent. The existing unapproved model leaves work recoverable instead of bypassing gates.

Current checks: **102 unit and 54 integration tests passed**, with ten environment-dependent
tests skipped. Actual live Redis/PostgreSQL and container execution remain validation gates.
See [Phase 6 results](docs/phase6-validation.md).
