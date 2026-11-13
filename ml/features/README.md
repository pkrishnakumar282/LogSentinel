# Shared features

`contract.py` is the versioned predictor allowlist. `causal.py` provides ordered, bounded
rolling-window extraction, used identically by training replay and scoring sessions.
Feature extraction learns no vocabulary or scaling parameters; fitted preprocessing lives
inside each serialized scikit-learn pipeline. See [feature semantics](../../docs/training.md).
