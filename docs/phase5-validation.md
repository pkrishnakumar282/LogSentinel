# Phase 5 validation record

Executed locally on 2026-11-13. Real inference tests used Python 3.14.10, MLflow 3.18.0
and the existing pinned numerical stack. No public datasets or production logs were used.

## Executed checks

- 94 unit tests passed on Python 3.14.10 and separately on Python 3.12.15.
- 36 integration tests passed: 13 inference, 12 training, nine lifecycle/MLflow and two ingestion.
- Seven earlier adapter tests skipped: five HTTP TestClient tests due to sandbox socket-pair
  restrictions, one unavailable Drain3 test and one unavailable/not-enabled Redis test.
- Python compilation, installed dependency consistency and all combined Compose profiles passed.
- 95 distinct pinned packages across five combined requirements files have no conflicts.
- No unintended default `mlflow.db` was created by the final inference/regression runs.

## Actual model and API evidence

The inference fixtures trained actual scikit-learn pipelines, calibrated/evaluated them and
registered two actual MLflow versions in a temporary SQLite registry, with matching audit
records. The frozen test policy is explicitly permissive and development-scoped; it verifies
service mechanics rather than useful detection quality. No production champion was created.

The tests executed:

- Approved-version loading, batch/single-event parity and parity with the saved evaluation
  scores after replaying the same preceding observations.
- Numeric registry version identification in every returned prediction, rather than the
  Phase 3 configuration/candidate ID.
- Missing approval, rejected registry tags, production/development scope separation,
  corrupted package rejection and runtime compatibility rejection before unpickling.
- Alias pinning until explicit reload; successful new-version swaps; continued serving after
  a failed different-version reload; withdrawn readiness after confirmed current-version revocation.
- CLI `check` and `predict` subprocesses with no training package on `PYTHONPATH`.
- Actual ASGI routing, auth, Pydantic validation, body/rate/batch limits, liveness/readiness,
  single/batch predictions and authorized reload routes.
- Feature/prediction transaction rollback on failure, bounded context expiry/capacity,
  redaction, duplicate caches, conflicting IDs and concurrent duplicate requests.

The ASGI tests call the real application directly with a synchronous test executor because
this sandbox denies async socket-pair wakeups. Production uses a threadpool. No listening
HTTP socket, networked load test or container startup was executed. These results do not
claim that the production HTTP/threadpool path has been validated on this host.

## Default persisted model remains unavailable

The actual repository registry still contains no model approved under the default quality
gates. Its Phase 4 result was not changed. Executing `python -m ml_inference check` returned
exit code 1, `status: unavailable`, `model_version: null` and
`last_load_error: approved_version_not_found`. The local health snapshot is saved under
`.local/inference/phase5-model-health.json` and intentionally ignored by Git.

Default serving will therefore remain live but unready, and prediction requests return 503.
The service does not load the unapproved Phase 3 candidates as a fallback. A legitimately
validated and registered model is required for a normal deployment's successful predictions.

## Outstanding gates

Docker builds/startup, networked HTTP and its threadpool/concurrency behavior, compiled ML
dependencies on Python 3.12, and the existing Redis/Drain3/PostgreSQL integrations remain
unverified here. Redis consumption/recovery and alert management are Phase 6; durable
prediction/application records and gateway APIs are Phase 7.

See [inference setup and contracts](inference.md) for complete commands and limitations.
