# Phase 3: shared features and reproducible training

## Implemented workflow

Normalized JSONL → validation/redaction/deduplication → temporal split → causal feature
replay → fitted preprocessing + Isolation Forest → shared holdout diagnostics → candidate
Joblib artifacts and checksums.

Training is an independently runnable one-shot service. It never runs inside the processor
or online scoring loop. This phase saves local candidates only. MLflow tracking, supervised
evaluation, approval gates and registry promotion are implemented separately in
[Phase 4](model-lifecycle.md); HTTP inference is Phase 5.
No candidate produced here is approved for production.

## Install and train locally

Use an isolated Python environment. Python 3.12 is the deployment target. The cached compiled
wheels available in this workspace target Python 3.14, so actual model execution was tested
on 3.14; dependency-free feature/data tests also passed on a discovered managed Python
3.12.15 installation. Python 3.12 is installed outside the current shell's PATH; the initial
preflight's earlier “unavailable” result meant unavailable on PATH.

On a host with normal package access:

```bash
# Use your Python 3.12 environment from the Arch setup guide.
python -m pip install -r services/ml-training/requirements.txt
python -m pip check
export PYTHONPATH="$PWD:$PWD/services/ml-training"
python -m ml_training make-synthetic \
  --output ml/datasets/synthetic-phase3.jsonl \
  --labels ml/datasets/synthetic-phase3.labels.jsonl --count 600 --seed 42
python -m ml_training inspect --dataset ml/datasets/synthetic-phase3.jsonl
python -m ml_training train --dataset ml/datasets/synthetic-phase3.jsonl \
  --output ml/artifacts --seed 42 --estimators 100 200
```

The generator refuses to overwrite existing files. Select new output names to generate
another dataset. All records are explicitly synthetic. The optional labels sidecar is a
controlled scenario annotation, not real production ground truth; training never reads it.
The generator intentionally includes expected ERROR retries annotated as normal, and a
small number of unusual tail scenarios. The seven Phase 2 fixture records are too small for
the default minimum split (32 training + 8 holdout); use the larger generator for training.

Other flags:

| Flag | Default | Purpose |
|---|---|---|
| `--holdout-fraction` | 0.2 | Target share of temporally last records |
| `--min-train` / `--min-holdout` | 32 / 8 | Fail on insufficient data |
| `--window-seconds` | 60 | Causal feature horizon; also `FEATURE_WINDOW_SECONDS` |
| `--max-window-events` | 100000 | Hard state capacity; refuse overflow |
| `--estimators` | 100 200 | Compare tree counts for both variants |
| `--max-samples` | 256 | Requested tree sample count, clipped to training count |
| `--max-text-features` | 2048 | Bounded TF-IDF vocabulary |
| `--threshold-quantile` | 0.99 | Quantile of training scores, not holdout labels |
| `--seed` | 42 | Reproducible seed; also `RANDOM_SEED` |
| `--output` | `ml/artifacts` | Unique run directories; also `TRAINING_OUTPUT` |

All direct and resolved runtime dependencies used in this offline run are pinned in the
training service's requirements. Platform-specific hash locking and a Python 3.12 image
build remain deployment validation work. Never reuse a 3.14 artifact inside a 3.12 service;
retrain with the deployment environment's dependency set.

## Use processor-exported data

```bash
export PYTHONPATH="$PWD:$PWD/services/log-processor:$PWD/services/ml-training"
# Export sufficient collected history, not merely the seven demonstration records:
python -m log_processor --sink .local/normalized.sqlite3 --export > .local/history.jsonl
python -m ml_training inspect --dataset .local/history.jsonl
python -m ml_training train --dataset .local/history.jsonl --output ml/artifacts
```

Every event must contain `metadata.synthetic` as a real JSON boolean. Synthetic and
nonsynthetic records cannot be mixed. Real local records require `--provenance path.json`:

```json
{
  "kind": "local",
  "dataset_name": "approved-worker-logs",
  "acquired_at": "2026-11-13T00:00:00Z"
}
```

Public datasets additionally require `kind: public`, source URL, acquisition timestamp,
license, license URL and `license_verified: true`. This records the operator's license check;
it does not download, verify licensing automatically, or attest to dataset quality. No
public benchmark dataset was downloaded in this phase. Unknown input columns (including
labels) are rejected, and unknown metadata is removed. Messages and templates are redacted
again; authentication records are refused using the same exclusion policy as ingestion.

A dataset version is SHA-256 of canonical sanitized records in `(UTC timestamp, event ID)`
order. Duplicate IDs with changed contents fail. Identical logical records with different
IDs are deduplicated; the logical key includes the timestamp, host, source, service,
severity, status, message and template. Repeated messages at different timestamps remain
legitimate observations. Reordering an input file does not change its canonical version.
Dataset versions are independent from training run IDs and model candidate IDs.

## Shared causal features

The allowlist lives in `ml/features/contract.py`; implementations are in
`ml/features/causal.py`. `FeatureConfig` is saved with each model.

| Feature | Definition |
|---|---|
| TF-IDF text | Sanitized message, lowercase, numbers normalized, unigrams/bigrams; train-fitted vocabulary |
| Severity/source/service | Train-fitted one-hot encoding with unseen categories ignored |
| HTTP status | Status code (0 if absent), presence flag, 4xx and 5xx flags |
| Message length | Length after sanitization |
| Event frequency | Earlier events for the same `(source, service)` in the window |
| Source/global frequency | Earlier events for the source / entire stream |
| Events per second | Service count divided by configured full window duration |
| Template frequency | Earlier events with the same `(source, service, sanitized template)` |
| Error rate | Prior ERROR/CRITICAL count divided by prior service count; zero with no history |
| Window context | Elapsed observation time capped to the window, plus a warm flag |

For an event at time `t`, history is strictly `(t - window, t)`. Events exactly at the
left boundary are expired; peers at the same timestamp do not count each other. Counters
and cached duplicate rows are evicted as history expires. A duplicate within the cache
returns its original row without updating counts; a changed ID payload fails. Permanent
idempotency belongs to persistent downstream event records, not this bounded cache.

The extractor requires ordered events. Offline import sorts them; online callers must
order/reject late events or implement an explicit buffering policy. Capacity overflow is
an error rather than silently discarding valid in-window history. One session owns one
ordered stream. The numerical baseline uses the same numeric and categorical features as
the TF-IDF variant, omitting only text. Labels, event IDs, host identity, timestamps and
arbitrary metadata never become predictors. Host identity remains part of data validation,
not a model category.

Time is sufficient to mark a window warm; the flag does not assert sufficient event volume.
Initial cold-window samples remain in training and in the training-score quantile. Their
transient behavior can influence thresholds; Phase 4 must evaluate warm-up/calibration
policy explicitly. No threshold was tuned against synthetic labels in this phase.

## Leakage prevention and model comparison

A boundary is selected near the target holdout fraction, subject to minimum sizes and
strictly separated timestamps. Tied timestamps never span the split. Duplicate logical
records are removed before splitting. Frequent templates intentionally recur across time:
this tests behavior on future traffic with familiar patterns. Novel-template evaluation is
a separate Phase 4 concern, not a claim made by these diagnostics.

Both variants and every tree configuration use exactly the same split and feature rows.
Preprocessing and the estimator are fitted only on training rows. Holdout features may use
preceding training events and earlier holdout observations, as an online stream would;
neither labels nor future events nor holdout aggregates enter fitting. The default
threshold is a training-score quantile and is never chosen from holdout labels.

The score is `-IsolationForest.score_samples`, so higher means more unusual. It is an
uncalibrated model score, not a probability. The decision is strictly `score > threshold`.
Reports include actual fit time, batch scoring time per event, feature dimensions, score
distributions and predicted anomaly fraction. Batch latency includes DataFrame conversion
and fitted pipeline scoring; it excludes window feature extraction, transport and database
work, and is not an online service latency guarantee.

There is no precision, recall, F1, accuracy, confusion matrix or false positive rate in an
unlabeled report. Scores alone cannot rank model quality. No “best model” is automatically
selected, registered or promoted. Phase 3 synthetic runs are exploratory smoke tests, not
independent performance evaluation. Phase 4 must freeze model/calibration choices and use
an untouched labeled test dataset before reporting independent supervised performance.

## Artifacts and shared scoring

Each unique run directory contains:

- One fitted `ModelBundle` Joblib file per variant/tree configuration.
- Per-candidate holdout JSONL with event IDs, scores and flags only.
- `report.json`: dataset version/provenance, split boundaries and ID hashes, feature version,
  dependency versions, seed, configuration, diagnostics, timestamps and candidate status.
- `manifest.json`: SHA-256 checksums for all run files; status `candidate_not_approved`.

Staging directories are renamed into place only after successful training. Failed runs
remove their partial staging directory. Raw/normalized messages are not copied into reports
or prediction files; fitted vocabulary in the Joblib pipeline contains sanitized text.
Keep the sanitized source snapshot separately using its recorded checksum for reproducibility.

To inspect a trusted locally generated artifact:

```python
from ml_training.artifacts import load_bundle
from ml_training.data import load_dataset, temporal_split

# Replace with the actual directory printed by the training command.
run_directory = "ml/artifacts/<run-id>"
bundle = load_bundle(run_directory, "tfidf-iforest-100")
split = temporal_split(load_dataset("ml/datasets/synthetic-phase3.jsonl").events)
session = bundle.new_session(warmup=split.train)
predictions = session.predict(split.holdout)
```

Checksums and runtime/feature compatibility are checked before deserialization. Joblib uses
executable pickle: checksums detect corruption, not authenticity. Only load artifacts from
your trusted training process. Loading checks Python major/minor and the recorded numerical
stack versions. `new_session()` starts cold; supplying earlier chronological warm-up events
recreates offline context. Never warm a session with future holdout records. Batch and
single-event calls use the same shared extractor and fitted pipeline without refitting.

The shared scoring result uses the candidate ID as `model_version` in this phase. It is not
an MLflow registry version. Approved registry version binding and readiness are Phase 5
inference responsibilities. Stream-window checkpointing/recovery is also later service work;
no mutable online window state is serialized into the trained model.

## Optional one-shot Docker job

After generating `ml/datasets/synthetic-phase3.jsonl` on the host:

```bash
docker compose -f docker-compose.yml -f compose.training.yml --profile training config --quiet
docker compose -f docker-compose.yml -f compose.training.yml --profile training \
  run --build --rm ml-training
```

The dataset mount is read-only; artifacts persist in the `training-artifacts` named volume.
The non-root job exits after training, does not restart, and is excluded from normal
`docker compose up`. Docker builds/startup could not be executed in this session.

## Validation commands

```bash
python -W error::ResourceWarning -m unittest discover -s tests/unit -p 'test_*.py' -v
python -W error::ResourceWarning -m unittest discover -s tests/integration -p 'test_*.py' -v
python -m pip check
```

Run integration tests inside the environment where the training dependencies are installed;
otherwise real ML tests skip visibly. Existing Phase 2 API/Drain3/Redis tests still require
their separate dependencies/services.

## Executed validation record

See [Phase 3 validation](phase3-validation.md) for exact checks, the persisted local run,
dataset hash, candidate feature counts and observed default-threshold limitations.
The final run uses the documented 600-record generator and default 100/200 tree grid.
