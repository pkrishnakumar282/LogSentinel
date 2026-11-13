# ML training service

An independently runnable one-shot job that imports normalized history, replays shared
causal features, and trains TF-IDF and numerical/frequency Isolation Forest candidates.
It saves fitted preprocessing, models, dataset/split provenance and real diagnostics.

See [training setup, commands, features, artifacts and limitations](../../docs/training.md).
Install `requirements.txt` in an isolated environment and add the repository root and
`services/ml-training` to `PYTHONPATH`. The Dockerfile uses a non-root Python 3.12 process;
`compose.training.yml` provides an opt-in job profile. Python 3.14 model execution was tested
offline, while Python 3.12 dependency-backed execution and image builds remain unverified.

The core `train` command preserves local candidate-only behavior. Phase 4 adds
`track`, `calibrate`, `evaluate`, `register`, `promote` and `audit` commands; see the
[lifecycle guide](../../docs/model-lifecycle.md). Install `requirements-mlflow.txt` for the
pinned optional tracking/registry stack. Default model quality gates are enforced before
registration, and aliases require explicit authorized, audited changes.
