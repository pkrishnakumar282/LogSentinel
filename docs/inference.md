# Phase 5: approved-version inference

The independently runnable `ml-inference` service loads an audited, approved MLflow model
version and scores normalized events through the existing fitted pipeline. It never trains,
refits preprocessing, registers models or promotes aliases. Redis consumption and prediction
persistence remain Phases 6–7; Phase 5 delivers HTTP and local CLI inference.

The persisted Phase 4 model failed its default quality gates. It remains unregistered and
unapproved. Default startup therefore stays live but unready, with predictions returning
503. No candidate-file fallback or automatic weakening of approval policy exists.

## Local setup

From the repository root, in an isolated Python environment:

```bash
python -m pip install -r services/ml-inference/requirements.txt
python -m pip check
export PYTHONPATH="$PWD:$PWD/services/ml-inference"
export MLFLOW_DISABLE_AGENT_HINT=1
export MLFLOW_ENABLE_TELEMETRY=false
export MLFLOW_TRACKING_URI="sqlite:///$PWD/.local/mlflow/tracking.db"
export REGISTRY_AUDIT_PATH="$PWD/.local/mlflow/lifecycle.sqlite3"
# Local diagnostic; exit 1 is expected until an approved model exists:
python -m ml_inference check
```

The inference requirements reuse the pinned lifecycle/numerical dependency stack, but the
service imports no training business code. The container likewise copies only inference,
shared contracts and shared ML modules. Actual ML execution was verified on Python 3.14.10;
core tests also run on Python 3.12.15. Python 3.12 model dependency execution and image builds
remain unverified. Models must be retrained with the runtime that serves them.

After a model passes a legitimately chosen evaluation policy and is registered through the
[Phase 4 lifecycle](model-lifecycle.md), select an explicit numeric version or an alias:

```bash
# Use an existing approved registry version. The number below is an example selector,
# not a claim that this repository's default registry has a version 1.
export MODEL_NAME=logsentinel-model
export MODEL_VERSION=1
# Development-scoped evidence is allowed only in an explicitly development service:
export INFERENCE_SCOPE=development
# For production, use INFERENCE_SCOPE=production and a production-approved version.
# To resolve an alias instead, unset MODEL_VERSION and set MODEL_ALIAS=staging or champion.
```

A development service can also load a production-approved version. A production service
rejects development versions. A champion alias always requires production evidence. Alias
resolution occurs at startup/reload and pins the resulting numeric version for predictions;
an alias change alone does not silently switch the running model.

Generate a separate API secret, export it in shells making requests, and start locally:

```bash
export INFERENCE_API_TOKEN="$(openssl rand -hex 24)"
python -m ml_inference serve --host 127.0.0.1 --port 8013
```

Do not generate a different value in the caller shell. HTTP startup requires at least 24
characters; diagnostic/prediction CLI commands do not need an API token. The Python service
reads process environment, not `.env` automatically. Without an approved model, the server
still starts and exposes useful liveness/readiness information.

## Endpoints

| Route | Authorization | Behavior |
|---|---|---|
| `GET /health/live` | None, localhost boundary | Process is alive |
| `GET /health/ready` | None | 200 with a loaded model; otherwise 503 |
| `GET /health/model` | None | Model/version, scope, threshold, last load result, bounded counters/latency |
| `GET /api/v1/model` | Inference API token | Detailed model health |
| `POST /api/v1/predict` | Inference API token | One normalized event |
| `POST /api/v1/predict/batch` | Inference API token | Ordered normalized events, one context |
| `POST /api/v1/models/reload` | Admin token | Verified model reload/swap |
| `POST /api/v1/contexts/{id}/reset` | Admin token | Clear one context and its retry cache |
| `/docs`, `/openapi.json` | None | Static API interface descriptions |

Prediction examples, using explicitly synthetic input:

```bash
curl --fail-with-body http://127.0.0.1:8013/api/v1/predict \
  -H "Authorization: Bearer $INFERENCE_API_TOKEN" -H 'Content-Type: application/json' \
  --data-binary @docs/inference-request.example.json
curl --fail-with-body http://127.0.0.1:8013/api/v1/predict/batch \
  -H "Authorization: Bearer $INFERENCE_API_TOKEN" -H 'Content-Type: application/json' \
  --data-binary @docs/inference-batch.example.json
```

A successful single response includes `event_id`, `prediction`, `is_anomaly`, `anomaly_score`,
`model_name`, numeric registry `model_version`, `threshold`, `threshold_source`,
`context_warm` and `feature_version`. Batch responses contain `predictions` and `context_id`.
No message, arbitrary metadata, secret, feature text, probability or unsupported explanation
is returned. ERROR severity is a feature, never an automatic anomaly rule.

## Loading and compatibility checks

Before deserialization the loader verifies:

1. The registered version exists, is READY and has passed-validation tags.
2. A matching successful registration exists in the read-only lifecycle audit ledger.
3. Source, run ID, evidence hashes and approval scope match the ledger.
4. The downloaded whole-package hash and bundle hash match audited values.
5. The MLmodel Python version and exact numerical package pins match the serving runtime.

It then loads the trusted Joblib bundle, verifies its type, feature version, candidate ID,
threshold and evidence metadata, and scores an isolated readiness probe. That probe never
enters a live context or prediction counters. The loader does not execute the downloaded
pyfunc source or import training code. Joblib itself is executable serialization, so the
registry/audit are trusted inputs; checksums are integrity evidence, not authenticity signatures.
Symlinks and bundle paths outside the verified package are refused.

MLflow 3.18's `models:/m-ID` artifact lookup can construct a global default client even
when a tracking URI is supplied. The loader resolves that ID through its explicit client
before downloading the concrete artifact location. It does not change global tracking URIs
or create an unintended default tracking database.

The audit file must exist and be readable. Inference does not create an empty ledger to
manufacture approval. HTTP registry timeouts/retries are bounded; a missing SQLite backend
is reported as unavailable rather than initialized by inference. Existing SQLite stores
are queried through MLflow's SDK; inference performs no registry mutations.

## Ordered contexts, warm-up and transactions

`context_id` identifies one ordered stream, with a default of `default`. Separate exploratory
requests from independent streams by using separate contexts. Within a context, timestamps
must be nondecreasing. Equal-time peers cannot count each other; late events return 409.
Requests are not automatically reordered, because that could change a caller's intended
stream state. Warm-up observations can be supplied as preceding events; do not replay future
events into a past context. Shared causal feature semantics remain documented in [training](training.md).

A context starts cold. Predictions still return scores, with `context_warm=false`, until
elapsed observation time reaches the model's configured window. This flag does not promise
sufficient event volume or establish detection quality. Phase 6 alert policy must account
for startup/warm-up explicitly; this phase generates no alerts.

Each batch works on an independent copy of the feature state's mutable containers. Only
successful full-batch scoring commits it. Malformed input, conflicting IDs, out-of-order
events or scoring failure leave the previous context untouched. The copy cost is proportional
to retained window state and is included in recorded batch latency. A single engine lock
serializes predictions, making duplicate/concurrent updates deterministic on one worker.

Event IDs are canonical UUID strings. Duplicate events retained in the window return their
cached response without increasing prediction counts. Changed contents under the same ID
are rejected. Cache entries expire with the feature window; this is not permanent deduplication.
A replay older than retained context can be rejected as out of order. Durable stream/database
idempotency will be added in Phase 6. Metadata is allowlisted, text/templates are redacted
again, and authentication records are refused before entering context state.

Contexts are bounded and expire after idle TTL; exhausted capacity returns 429 instead of
evicting an active stream silently. TTL/reset/restart clears history. State is in memory,
not crash-persistent. Use one Uvicorn worker; multiple workers would have independent state.
The server bounds concurrency to 32 and validates body, batch and request-rate limits.
General request body/validation errors never echo raw input.

## Reloads and threshold configuration

Set/export `ADMIN_API_TOKEN` from the same local secret configuration used by lifecycle
commands, then send it as a Bearer token to admin routes:

```bash
# Recheck the active target:
curl --fail-with-body http://127.0.0.1:8013/api/v1/models/reload \
  -H "Authorization: Bearer $ADMIN_API_TOKEN" -H 'Content-Type: application/json' --data '{}'
# Select a different existing approved version:
curl --fail-with-body http://127.0.0.1:8013/api/v1/models/reload \
  -H "Authorization: Bearer $ADMIN_API_TOKEN" -H 'Content-Type: application/json' \
  --data '{"model_version":"2"}'
# Or resolve an approved alias; do not provide both selectors:
curl --fail-with-body http://127.0.0.1:8013/api/v1/models/reload \
  -H "Authorization: Bearer $ADMIN_API_TOKEN" -H 'Content-Type: application/json' \
  --data '{"model_alias":"staging"}'
```

A successful new version swaps atomically and clears contexts; reloading the identical
package/version preserves contexts. An explicit successful selector becomes the active
reload target for this process. Restart still uses environment configuration. Downloads and
validation occur outside the prediction lock, so a previous model can keep serving during
reload. Only one reload runs at once. A failed new-version load or unavailable registry
keeps the last healthy version, exposes a safe error code, and returns 503 for that reload
operation even while readiness remains 200.

If reload confirms that the currently served version has been revoked or its approved
package/runtime evidence no longer matches, that version is withdrawn: readiness becomes
503 and its contexts are cleared. Registry metadata is not polled on every prediction;
operators must reload/restart to apply revocations. No cached model is loaded across restarts
when the registry is unavailable. Alias changes/reloads are not registry promotions; the
Phase 4 controller remains responsible for approved, audited alias writes.

Default thresholds come from the approved calibrated bundle. The decision is strictly
`score > threshold`; scores are uncalibrated and are not probabilities. `INFERENCE_THRESHOLD`
can override the score threshold only with `INFERENCE_SCOPE=development`. The response and
health identify the override, and `evaluation_applies_to_threshold=false`. Production must
recalibrate, evaluate and register a new version to change a validated threshold.

## Configuration defaults

| Variable | Default |
|---|---|
| `MLFLOW_TRACKING_URI` | `sqlite:///.local/mlflow/tracking.db` |
| `REGISTRY_AUDIT_PATH` | `.local/mlflow/lifecycle.sqlite3` |
| `MODEL_NAME` | `logsentinel-model` |
| `MODEL_VERSION` / `MODEL_ALIAS` | empty / `champion`; explicit version takes precedence |
| `INFERENCE_SCOPE` | `production` |
| `INFERENCE_API_TOKEN` | Required for HTTP startup; no default credential |
| `INFERENCE_PORT` | 8013 |
| `INFERENCE_MAX_BATCH` | 256 |
| `INFERENCE_MAX_BODY_BYTES` | 1048576 |
| `INFERENCE_MAX_CONTEXTS` | 32 |
| `INFERENCE_CONTEXT_TTL_SECONDS` | 900 |
| `INFERENCE_RATE_PER_MINUTE` | 6000 per process |

Health reports bounded process-local counts and last batch latency, including context copy,
feature generation and model scoring. They reset with process restart and do not replace
Prometheus/Grafana monitoring planned for Phase 10. No event IDs/messages/context IDs are
used as metric labels. API tokens, registry URIs, filesystem paths and raw exceptions are
never returned in health or prediction bodies.

## CLI and Docker

A local normalized JSONL file can be scored without starting an HTTP listener:

```bash
python -m ml_inference predict --input ml/datasets/phase4-v1/test.jsonl --context-id local-replay
```

This uses the same approval checks and engine, and starts a fresh ordered context. It exits
without predictions when no approved model exists. `check` exits 0 if ready, 1 if a model is
unavailable, and 2 on configuration errors. CLI `serve` binds localhost by default.

For Docker, set the required secrets in `.env`, then:

```bash
docker compose -f docker-compose.yml -f compose.mlflow.yml -f compose.inference.yml config --quiet
docker compose -f docker-compose.yml -f compose.mlflow.yml -f compose.inference.yml up --build -d
```

The application runs as UID 10001 with resource limits and localhost-only port publication.
It connects to the internal MLflow server and mounts the lifecycle `registry-audit` volume
read-only. That volume must contain the ledger written by `compose.lifecycle.yml`; a host
ledger is not automatically copied into it. The MLflow server and inference must use the
same registry/artifact backend. Do not point the container at host-only SQLite/file-URI
artifact paths. With no approved version the container stays running but is unhealthy by
readiness design. Docker startup was not executed in this restricted session.

## Tests and limits

```bash
python -W error::ResourceWarning -m unittest discover -s tests/unit -p 'test_*.py' -v
MLFLOW_DISABLE_AGENT_HINT=1 MLFLOW_ENABLE_TELEMETRY=false \
  python -W error::ResourceWarning -m unittest discover -s tests/integration -p 'test_*.py' -v
```

Real integration fixtures train, evaluate and register two temporary development versions
using an explicitly permissive test policy. They verify inference mechanics, not useful
model quality, and never create a production champion or change the persisted default model.
The new API tests invoke the real ASGI app with a synchronous test executor because this
sandbox denies async socket-pair wakeups. Production uses a threadpool. Listening sockets,
threadpool HTTP behavior and container startup remain unverified here.

See [Phase 5 validation](phase5-validation.md) for executed results. API startup follows
[FastAPI lifespan guidance](https://fastapi.tiangolo.com/advanced/events/); registry resolution
uses the [MLflow client](https://mlflow.org/docs/latest/api_reference/python_api/mlflow.client.html).

Phase 6 now provides the separate `worker` command, durable checkpoints/outbox and Redis
consumer adapter. HTTP exploratory contexts remain independent. See
[streams and alerts](streams-and-alerts.md) for current delivery guarantees and validation.
