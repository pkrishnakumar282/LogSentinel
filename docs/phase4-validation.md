# Phase 4 validation record

Executed on 2026-11-13 with explicitly synthetic datasets and MLflow 3.18.0.

## Executed checks

- 77 unit tests passed on Python 3.14.10 and separately on Python 3.12.15.
- 23 integration tests passed on Python 3.14.10: 12 training, nine lifecycle/MLflow and two ingestion tests.
- Seven earlier adapter tests skipped: five HTTP tests (sandbox denies socket-pair writes), Drain3 unavailable, and Redis unavailable/not enabled.
- Python compilation, dependency consistency and all combined Compose profiles validated.
- 95 distinct package pins across the four combined requirements files have no conflicts.
- MLflow tracking context restoration is tested so local-only CLI jobs do not inherit unintended tracking URIs.
- Docker builds/startup, UI/HTTP access, PostgreSQL integration and compiled ML dependencies on Python 3.12 remain unverified.

## Persisted evaluation

- Training directory: `ml/artifacts/20261113T052217-9097b7a60507`.
- Calibration directory: `ml/artifacts/calibration-20261113T053509-556ab2dc2ae7`.
- Evaluation directory: `ml/artifacts/evaluation-20261113T053509-1cd301d53efa`.
- Validation dataset SHA-256: `0b4d190c9737e847fd62d338bab9f971661aaa8001c65e4c54589b8631e5807e`.
- Test dataset SHA-256: `0b200fc2ea3e919cf0e81a8a9a3d58f5e7357a390a6a25ad3d47750f17fb1329`.
- Validation range: `2026-01-01T00:10:00+00:00` to `2026-01-01T00:14:59+00:00`.
- Test range: `2026-01-01T00:15:00+00:00` to `2026-01-01T00:19:59+00:00`.
- Both future splits contain 300 events; test labels contain 13 controlled anomalies and 287 normal events.
- Candidate selected by validation F1 before test scoring: `numeric_frequency-iforest-100`.

| Candidate | Precision | Recall | F1 | False positive rate |
|---|---:|---:|---:|---:|
| tfidf-iforest-100 | undefined | 0.000000 | 0.000000 | 0.000000 |
| tfidf-iforest-200 | undefined | 0.000000 | 0.000000 | 0.000000 |
| numeric_frequency-iforest-100 | 0.444444 | 0.307692 | 0.363636 | 0.017422 |
| numeric_frequency-iforest-200 | 0.444444 | 0.307692 | 0.363636 | 0.017422 |

These are measured controlled synthetic test metrics, not production or public benchmark results.
The selected confusion matrix is `[[282, 5], [9, 4]]` in `[normal, anomaly]` row/column order.
The predefined default policy requires precision/recall/F1 of at least 0.70. The selected model
failed all three limits. Registration was attempted through the authorized controller and
rejected with `evaluation_quality_gate_failed`; a durable rejection audit record was written.
No default model version or champion alias was created. Gates were not weakened after the test.

## MLflow and registry verification

- The persisted SQLite tracking store has 6 runs: one training parent, four candidate children and one evaluation run.
- Persisted evaluation MLflow run: `ffe60561c318447688794e945a3f66b1`.
- Default registered models: 0.
- The separate temporary integration database exercised actual registration, idempotent retries, staging alias assignment, pyfunc reload/score parity, token rejection and denial of development-to-champion promotion.
- That registry plumbing test uses an explicitly permissive test policy frozen before scoring. It is not approval or evidence of useful model detection quality.
- Registered packages include curated source only, are checksummed, and are verified before promotion. Local audit intent/outcome records are durable.

## Limits and next phase

The current default model is unapproved. Phase 5 inference must handle an absent approved version
with clear readiness behavior. Improving detection requires fresh validation/test data and
predeclared calibration choices, not retroactive adjustment of this report.
MLflow UI/container/network behavior still needs validation on a normal local host.

See [lifecycle instructions](model-lifecycle.md) for commands, access control and policy semantics.
