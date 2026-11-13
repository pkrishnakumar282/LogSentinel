# Phase 3 validation record

Executed locally on 2026-11-13. All model inputs below are explicitly synthetic.

## Checks

- 60 unit tests passed on Python 3.14.10 and on the managed Python 3.12.15 interpreter.
- 14 integration tests passed on Python 3.14.10, including 12 actual ML tests and two ingestion tests.
- Seven existing API/Drain3/Redis adapter tests skipped due to unavailable dependencies/services.
- Compilation, dependency consistency (`pip check`) and combined Compose rendering passed.
- Docker image builds/startup and the compiled ML stack on Python 3.12 remain unverified.

## Actual synthetic training run

- Run: `20261113T052217-9097b7a60507`.
- Dataset SHA-256: `c29bc9296c0f9d417795333d0512f06e6bc54c259f3dcd1d05e43789d0cc6857`.
- Dataset: 600 normalized synthetic events; 480 training, 120 holdout; no duplicates removed.
- Both variants were trained with 100 and 200 trees and seed 42.
- All four Joblib bundles passed checksum/runtime checks and reloaded to score the 120 holdout records.
- Files persist locally under `ml/artifacts/20261113T052217-9097b7a60507`; datasets/artifacts are intentionally ignored by Git.

| Candidate | Features | Threshold | Holdout flagged |
|---|---:|---:|---:|
| tfidf-iforest-100 | 48 | 0.697767 | 0/120 |
| tfidf-iforest-200 | 48 | 0.703856 | 0/120 |
| numeric_frequency-iforest-100 | 21 | 0.699886 | 0/120 |
| numeric_frequency-iforest-200 | 21 | 0.696321 | 0/120 |

These thresholds are quantiles of training scores, not calibrated probabilities. No holdout
events exceeded them in this smoke test, including the controlled unusual tail scenarios.
This run therefore does not demonstrate detection effectiveness. There are no fabricated
precision/recall/F1/accuracy values. Phase 4 must evaluate detection and warm-up/calibration
policy before approving any candidate; synthetic smoke tests are not independent performance evidence.

TF-IDF ignores tokens absent from its training vocabulary, and categorical encoders ignore
unseen categories. Novel text or severity alone is not guaranteed to produce an anomaly.

See [training instructions](training.md) for reproducible commands and feature semantics.
