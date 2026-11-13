# Experiments

Phase 3 comparisons are local candidate reports inside each unique artifact directory.
They compare TF-IDF and numerical/frequency Isolation Forests on a common temporal holdout.
Phase 4 adds real MLflow parent/candidate/evaluation runs and approval-gated registry
versions. Local default tracking data is under `.local/mlflow`; Docker uses persistent
volumes. See [lifecycle setup](../../docs/model-lifecycle.md) and
[actual validation](../../docs/phase4-validation.md).
Synthetic smoke-test results do not establish production detection performance.
