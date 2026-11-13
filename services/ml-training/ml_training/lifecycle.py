"""Calibration is frozen before independent test scoring. Training artifacts stay immutable."""
from dataclasses import replace
from datetime import UTC, datetime
import json
from pathlib import Path
import shutil
import tempfile
from time import perf_counter
from uuid import uuid4

from ml.features import FeatureExtractor
from shared.schemas.logs import utc_time

from .artifacts import load_bundle, sha256_file, write_json
from .data import digest_ids
from .evaluation import GatePolicy, calibrate_threshold, gate_result, load_labels, supervised_metrics
from .integrity import read_sealed, seal
from .train import distribution


def ensure_future(history, future):
    if utc_time(history[-1].timestamp) >= utc_time(future[0].timestamp):
        raise ValueError("evaluation_must_be_strictly_after_prior_data")
    if {event.event_id for event in history} & {event.event_id for event in future}:
        raise ValueError("evaluation_event_overlap")


def score(bundle, history, events):
    extractor = FeatureExtractor(bundle.feature_config)
    for event in history:
        extractor.transform_one(event)
    started = perf_counter()
    rows = extractor.transform(events)
    features_seconds = perf_counter() - started
    started = perf_counter()
    scores = bundle.score_rows(rows)
    scoring_seconds = perf_counter() - started
    return scores, {"feature_seconds": features_seconds, "score_seconds": scoring_seconds,
                    "batch_ms_per_event": (features_seconds + scoring_seconds) * 1000 / len(events),
                    "latency_scope": "batch_features_and_scoring_excluding_history_warmup"}


def _stage(output: str, prefix: str):
    root = Path(output)
    root.mkdir(parents=True, exist_ok=True)
    name = prefix + "-" + datetime.now(UTC).strftime("%Y%m%dT%H%M%S") + "-" + uuid4().hex[:12]
    return Path(tempfile.mkdtemp(prefix=".pending-", dir=root)), root / name


def _training_report(run_directory, history):
    report = json.loads((Path(run_directory) / "report.json").read_text())
    if report["dataset_version"] != history.version:
        raise ValueError("training_history_version_mismatch")
    # load_bundle verifies all candidate artifacts and runtime versions before use.
    if history.events[-1].timestamp != report["split"]["holdout_end"]:
        raise ValueError("training_history_boundary_mismatch")
    return report


def calibrate(run_directory: str, history, validation, labels_path: str | None, output: str,
              policy: GatePolicy | None = None, candidate_id: str | None = None) -> Path:
    policy = policy or GatePolicy()
    report = _training_report(run_directory, history)
    ensure_future(history.events, validation.events)
    labels, label_version = load_labels(labels_path, validation)
    if labels is None and candidate_id is None:
        raise ValueError("unlabeled_calibration_requires_explicit_candidate")
    candidates = []
    for item in report["candidates"]:
        bundle = load_bundle(run_directory, item["candidate_id"])
        scores, timing = score(bundle, history.events, validation.events)
        if labels is not None:
            truth = [labels[event.event_id] for event in validation.events]
            threshold, metrics = calibrate_threshold(truth, scores, policy.max_false_positive_rate)
        else:
            threshold, metrics = bundle.threshold, None
        candidates.append({"candidate_id": item["candidate_id"], "threshold": threshold,
                           "validation_metrics": metrics, "validation_scores": distribution(scores),
                           "timing": timing, "artifact_sha256": sha256_file(Path(run_directory) / (item["candidate_id"] + ".joblib"))})
    if candidate_id is not None:
        if candidate_id not in {item["candidate_id"] for item in candidates}:
            raise ValueError("unknown_candidate_id")
        selected = candidate_id
    else:
        selected = sorted(candidates, key=lambda item: (
            -(item["validation_metrics"]["f1"] or 0),
            -(item["validation_metrics"]["precision"] or 0), item["candidate_id"]))[0]["candidate_id"]
    plan = {"format_version": 1, "status": "frozen_before_test", "created_at": datetime.now(UTC).isoformat(),
            "training_manifest_sha256": sha256_file(Path(run_directory) / "manifest.json"),
            "training_dataset_version": history.version, "training_provenance": history.provenance,
            "validation_dataset_version": validation.version, "validation_provenance": validation.provenance,
            "validation_label_version": label_version, "validation_ids_sha256": digest_ids(validation.events),
            "validation_start": validation.events[0].timestamp, "validation_end": validation.events[-1].timestamp,
            "policy": policy.to_dict(), "selected_candidate_id": selected, "candidates": candidates,
            "selection_source": "validation_f1_only" if candidate_id is None else "explicit_operator_choice"}
    staging, destination = _stage(output, "calibration")
    try:
        write_json(staging / "plan.json", plan)
        seal(staging, "plan.json")
        staging.rename(destination)
    except BaseException:
        shutil.rmtree(staging, ignore_errors=True)
        raise
    return destination


def evaluate(run_directory: str, plan_directory: str, history, validation, test,
             test_labels_path: str | None, output: str) -> Path:
    import joblib
    plan = read_sealed(plan_directory, "plan.json")
    _training_report(run_directory, history)
    if (plan["status"] != "frozen_before_test" or plan["training_dataset_version"] != history.version
            or plan["validation_dataset_version"] != validation.version
            or plan["validation_ids_sha256"] != digest_ids(validation.events)
            or plan["training_manifest_sha256"] != sha256_file(Path(run_directory) / "manifest.json")):
        raise ValueError("calibration_lineage_mismatch")
    ensure_future(history.events, validation.events)
    ensure_future(tuple(history.events) + tuple(validation.events), test.events)
    labels, label_version = load_labels(test_labels_path, test)
    policy = GatePolicy(**plan["policy"])
    staging, destination = _stage(output, "evaluation")
    try:
        results = []
        for item in plan["candidates"]:
            if item["artifact_sha256"] != sha256_file(Path(run_directory) / (item["candidate_id"] + ".joblib")):
                raise ValueError("calibration_artifact_mismatch")
            bundle = load_bundle(run_directory, item["candidate_id"])
            scores, timing = score(bundle, tuple(history.events) + tuple(validation.events), test.events)
            metrics = supervised_metrics([labels[event.event_id] for event in test.events], scores,
                                         item["threshold"]) if labels is not None else None
            result = {"candidate_id": item["candidate_id"], "threshold": item["threshold"],
                      "test_metrics": metrics, "timing": timing, "test_scores": distribution(scores),
                      "predicted_anomaly_fraction": sum(value > item["threshold"] for value in scores) / len(scores)}
            results.append(result)
            with (staging / (item["candidate_id"] + ".predictions.jsonl")).open("w") as stream:
                for event, value in zip(test.events, scores, strict=True):
                    stream.write(json.dumps({"event_id": event.event_id, "anomaly_score": value,
                                             "is_anomaly": value > item["threshold"]}) + "\n")
            if item["candidate_id"] == plan["selected_candidate_id"]:
                joblib.dump(replace(bundle, threshold=item["threshold"]), staging / "selected.joblib", compress=3)
        selected = next(item for item in results if item["candidate_id"] == plan["selected_candidate_id"])
        kinds = [history.provenance["kind"], validation.provenance["kind"], test.provenance["kind"]]
        gate = gate_result(selected["test_metrics"], selected["timing"]["batch_ms_per_event"], policy, kinds)
        report = {"format_version": 1, "status": "evaluated", "evaluated_at": datetime.now(UTC).isoformat(),
                  "calibration_plan_sha256": sha256_file(Path(plan_directory) / "plan.json"),
                  "training_dataset_version": history.version, "validation_dataset_version": validation.version,
                  "test_dataset_version": test.version, "test_ids_sha256": digest_ids(test.events),
                  "test_label_version": label_version, "provenance_kinds": kinds,
                  "test_start": test.events[0].timestamp, "test_end": test.events[-1].timestamp,
                  "selected_candidate_id": selected["candidate_id"], "selected_result": selected,
                  "policy": policy.to_dict(), "gate": gate, "candidates": results,
                  "selection_source": plan["selection_source"],
                  "dependencies": bundle.dependencies, "feature_version": bundle.feature_version,
                  "training_manifest_sha256": plan["training_manifest_sha256"],
                  "selection_frozen_before_test": True}
        write_json(staging / "evaluation.json", report)
        seal(staging, "evaluation.json")
        staging.rename(destination)
    except BaseException:
        shutil.rmtree(staging, ignore_errors=True)
        raise
    return destination
