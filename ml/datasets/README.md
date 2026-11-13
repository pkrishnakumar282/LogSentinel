# Dataset policy

No public dataset has been downloaded. Phase 2 includes hand-authored, explicitly synthetic
fixtures in `tests/fixtures` and a deterministic simulator; public LogHub data requires a recorded source URL, license verification, format,
checksum and acquisition date before import. Real local logs require consent and redaction.
Never commit raw journal logs, authentication records or personal data.

Splits must preserve temporal order and prevent duplicate templates/events crossing the
holdout boundary where appropriate. Evaluation labels are never model input. Dataset
manifests will record provenance, redaction version and split boundaries.

Phase 3 adds a normalized synthetic generator (`python -m ml_training make-synthetic`)
with separate optional label sidecars. The importer sanitizes again, requires explicit
provenance for nonsynthetic records, rejects mixed real/synthetic data and records a
canonical SHA-256 version. Temporal holdouts never split tied timestamps. Repeated templates
can legitimately recur in future traffic; duplicate logical events are removed globally.
See [training data preparation](../../docs/training.md) for executable commands.
