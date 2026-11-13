"""Reproducible unsupervised candidate comparison; supervised evaluation is Phase 4."""
from datetime import UTC, datetime
import json
from pathlib import Path
import shutil
import tempfile
from time import perf_counter
from uuid import uuid4

from ml.features import FEATURE_VERSION, FeatureExtractor
from ml.model import ModelBundle, runtime_versions

from .artifacts import write_checksums, write_json
from .config import TrainingConfig
from .data import Dataset, temporal_split
from .pipelines import build_pipeline


def distribution(scores) -> dict:
    import numpy as np
    return {"min": float(np.min(scores)), "median": float(np.median(scores)),
            "mean": float(np.mean(scores)), "max": float(np.max(scores)),
            "p95": float(np.quantile(scores, 0.95)), "p99": float(np.quantile(scores, 0.99))}


def train(dataset: Dataset, output: str, config: TrainingConfig | None = None) -> Path:
    import joblib
    import numpy as np
    import pandas as pd
    from ml.features.contract import FEATURE_COLUMNS

    config = config or TrainingConfig()
    split = temporal_split(dataset.events, config.holdout_fraction, config.min_train, config.min_holdout)
    # Replay once. Holdout context includes only preceding observations, never future events.
    extractor = FeatureExtractor(config.feature_config)
    train_rows = extractor.transform(split.train)
    holdout_rows = extractor.transform(split.holdout)
    frame = pd.DataFrame(train_rows, columns=FEATURE_COLUMNS)
    run_id = datetime.now(UTC).strftime("%Y%m%dT%H%M%S") + "-" + uuid4().hex[:12]
    root = Path(output)
    root.mkdir(parents=True, exist_ok=True)
    staging = Path(tempfile.mkdtemp(prefix=".training-", dir=root))
    started = perf_counter()
    dependencies = runtime_versions()
    try:
        report = {"run_id": run_id, "status": "candidate_not_approved",
                  "dataset_version": dataset.version, "dataset_provenance": dataset.provenance,
                  "duplicates_removed": dataset.duplicates_removed,
                  "feature_version": FEATURE_VERSION, "dependencies": dependencies,
                  "split": split.manifest(), "config": config.to_dict(),
                  "score_semantics": "negative score_samples; higher is more unusual; not probability",
                  "threshold_source": "training_score_quantile_only",
                  "evaluation": "unlabeled_holdout_diagnostics; no supervised metrics",
                  "candidates": [], "training_timestamp": datetime.now(UTC).isoformat()}
        for variant in ("tfidf", "numeric_frequency"):
            for count in config.n_estimators:
                candidate_id = f"{variant}-iforest-{count}"
                pipeline = build_pipeline(variant, config, count)
                before_fit = perf_counter()
                pipeline.named_steps["model"].set_params(max_samples=min(config.max_samples, len(frame)))
                pipeline.fit(frame)  # No labels and no holdout data enter fit.
                fit_seconds = perf_counter() - before_fit
                train_scores = -pipeline.score_samples(frame)
                threshold = float(np.quantile(train_scores, config.threshold_quantile))
                bundle = ModelBundle(pipeline, threshold, variant, config.feature_config,
                                     dataset.version, candidate_id, dependencies)
                before_predict = perf_counter()
                scores = bundle.score_rows(holdout_rows)
                predict_seconds = perf_counter() - before_predict
                if not np.isfinite(train_scores).all() or not np.isfinite(scores).all():
                    raise ValueError("nonfinite_training_results")
                joblib.dump(bundle, staging / f"{candidate_id}.joblib", compress=3)
                # Predictions contain identity + score only, no raw or normalized messages.
                with (staging / f"{candidate_id}.holdout.jsonl").open("w") as stream:
                    for event, score in zip(split.holdout, scores, strict=True):
                        stream.write(json.dumps({"event_id": event.event_id, "anomaly_score": score,
                                                 "is_anomaly": score > threshold}) + "\n")
                report["candidates"].append({
                    "candidate_id": candidate_id, "variant": variant, "algorithm": "IsolationForest",
                    "n_estimators": count, "max_samples_effective": pipeline.named_steps["model"].max_samples_,
                    "threshold": threshold, "train_scores": distribution(train_scores),
                    "holdout_scores": distribution(scores),
                    "holdout_predicted_anomaly_fraction": float(np.mean(np.asarray(scores) > threshold)),
                    "fit_seconds": fit_seconds, "holdout_predict_seconds": predict_seconds,
                    "holdout_predict_ms_per_event": predict_seconds * 1000 / len(scores),
                    "feature_count": int(pipeline.named_steps["model"].n_features_in_),
                })
        report["duration_seconds"] = perf_counter() - started
        write_json(staging / "report.json", report)
        write_checksums(staging)
        destination = root / run_id
        staging.rename(destination)
        return destination
    except BaseException:
        shutil.rmtree(staging, ignore_errors=True)
        raise
