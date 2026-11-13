"""Explicit labels and validation-only threshold selection; test data never chooses a model."""
from dataclasses import asdict, dataclass
import hashlib
import json
import math
from pathlib import Path
from uuid import UUID


@dataclass(frozen=True)
class GatePolicy:
    scope: str = "development"
    min_test_records: int = 80
    min_positives: int = 5
    min_negatives: int = 50
    min_precision: float = 0.7
    min_recall: float = 0.7
    min_f1: float = 0.7
    max_false_positive_rate: float = 0.05
    max_batch_ms_per_event: float = 100.0

    def __post_init__(self):
        if self.scope not in ("development", "production"):
            raise ValueError("invalid_gate_scope")
        for key in ("min_test_records", "min_positives", "min_negatives"):
            if type(getattr(self, key)) is not int or getattr(self, key) < 1:
                raise ValueError("invalid_gate_sample_minimum")
        for key in ("min_precision", "min_recall", "min_f1", "max_false_positive_rate"):
            value = getattr(self, key)
            if type(value) not in (int, float) or not math.isfinite(value) or not 0 <= value <= 1:
                raise ValueError("invalid_gate_metric_limit")
        value = self.max_batch_ms_per_event
        if type(value) not in (int, float) or not math.isfinite(value) or value <= 0:
            raise ValueError("invalid_gate_latency_limit")

    def to_dict(self):
        return asdict(self)


def load_labels(path: str | None, dataset) -> tuple[dict | None, str | None]:
    if path is None:
        return None, None
    labels = {}
    synthetic = dataset.provenance["kind"] == "synthetic"
    with Path(path).open(encoding="utf-8") as stream:
        for _ in range(len(dataset.events) + 1):
            line = stream.readline(4097)
            if not line:
                break
            try:
                item = json.loads(line)
                if len(line) > 4096 or set(item) != {"event_id", "is_anomaly", "annotation_origin"}:
                    raise ValueError
                UUID(item["event_id"])
                if type(item["is_anomaly"]) is not bool or item["event_id"] in labels:
                    raise ValueError
                origin = item["annotation_origin"]
                if not isinstance(origin, str) or len(origin) > 128:
                    raise ValueError
                if synthetic and origin not in ("synthetic_scenario_not_production_ground_truth", "synthetic_test_fixture"):
                    raise ValueError
                if not synthetic and origin not in ("human_review", "benchmark_ground_truth"):
                    raise ValueError
                labels[item["event_id"]] = item
            except (ValueError, TypeError, KeyError, AttributeError):
                raise ValueError("invalid_label_sidecar") from None
    if set(labels) != {event.event_id for event in dataset.events}:
        raise ValueError("label_coverage_mismatch")
    canonical = json.dumps([labels[key] for key in sorted(labels)], sort_keys=True).encode()
    return {key: value["is_anomaly"] for key, value in labels.items()}, hashlib.sha256(canonical).hexdigest()


def counts_metrics(tn: int, fp: int, fn: int, tp: int) -> dict:
    return {
        "sample_count": tn + fp + fn + tp, "positive_count": tp + fn, "negative_count": tn + fp,
        "precision": tp / (tp + fp) if tp + fp else None,
        "recall": tp / (tp + fn) if tp + fn else None,
        "f1": 2 * tp / (2 * tp + fp + fn) if 2 * tp + fp + fn else None,
        "false_positive_rate": fp / (fp + tn) if fp + tn else None,
        "confusion_matrix": {"labels": ["normal", "anomaly"], "values": [[tn, fp], [fn, tp]]},
    }


def supervised_metrics(truth, scores, threshold: float) -> dict:
    if len(truth) != len(scores) or not truth or not math.isfinite(threshold):
        raise ValueError("invalid_evaluation_vectors")
    if any(type(value) is not bool for value in truth) or any(not math.isfinite(score) for score in scores):
        raise ValueError("invalid_evaluation_vectors")
    tn = fp = fn = tp = 0
    for target, score in zip(truth, scores, strict=True):
        predicted = score > threshold
        if target:
            tp += int(predicted)
            fn += int(not predicted)
        else:
            fp += int(predicted)
            tn += int(not predicted)
    return counts_metrics(tn, fp, fn, tp)


def calibrate_threshold(truth: list[bool], scores: list[float], max_fpr: float) -> tuple[float, dict]:
    if (type(max_fpr) not in (int, float) or not math.isfinite(max_fpr) or not 0 <= max_fpr <= 1
            or any(type(value) is not bool for value in truth)):
        raise ValueError("invalid_calibration_policy_or_labels")
    if not truth or len(truth) != len(scores) or len(set(truth)) != 2:
        raise ValueError("calibration_requires_both_label_classes")
    if any(not math.isfinite(value) for value in scores):
        raise ValueError("nonfinite_calibration_scores")
    # Sweep tied scores together: threshold equality is normal, so ties cannot be split.
    ordered = sorted(zip(scores, truth, strict=True))
    tp, fp, tn, fn = sum(truth), len(truth) - sum(truth), 0, 0
    initial = counts_metrics(tn, fp, fn, tp)
    below_minimum = math.nextafter(ordered[0][0], -math.inf)
    best = None
    if initial["false_positive_rate"] <= max_fpr and math.isfinite(below_minimum):
        best = ((initial["f1"] or 0, -initial["false_positive_rate"], below_minimum), below_minimum, initial)
    index = 0
    while index < len(ordered):
        threshold = ordered[index][0]
        while index < len(ordered) and ordered[index][0] == threshold:
            if ordered[index][1]:
                tp -= 1
                fn += 1
            else:
                fp -= 1
                tn += 1
            index += 1
        metrics = counts_metrics(tn, fp, fn, tp)
        if metrics["false_positive_rate"] > max_fpr:
            continue
        # Deterministic ties prefer fewer false positives, then the higher threshold.
        rank = (metrics["f1"] or 0.0, -(metrics["false_positive_rate"] or 0), threshold)
        if best is None or rank > best[0]:
            best = (rank, threshold, metrics)
    assert best is not None  # Maximum threshold predicts no anomalies: FPR is zero.
    return float(best[1]), best[2]


def gate_result(metrics: dict | None, latency: float, policy: GatePolicy, kinds: list[str]) -> dict:
    reasons = []
    if metrics is None:
        reasons.append("labeled_test_required")
    else:
        for key, minimum in (("sample_count", policy.min_test_records),
                             ("positive_count", policy.min_positives), ("negative_count", policy.min_negatives)):
            if metrics.get(key, 0) < minimum:
                reasons.append(f"insufficient_{key}")
        for key, limit in (("precision", policy.min_precision), ("recall", policy.min_recall), ("f1", policy.min_f1)):
            value = metrics.get(key)
            if value is None or not math.isfinite(value) or value < limit:
                reasons.append(f"{key}_below_gate")
        value = metrics.get("false_positive_rate")
        if value is None or not math.isfinite(value) or value > policy.max_false_positive_rate:
            reasons.append("false_positive_rate_above_gate")
    if not math.isfinite(latency) or latency < 0 or latency > policy.max_batch_ms_per_event:
        reasons.append("batch_latency_above_gate")
    if policy.scope == "production" and "synthetic" in kinds:
        reasons.append("production_requires_nonsynthetic_training_and_evaluation")
    return {"passed": not reasons, "scope": policy.scope, "reasons": reasons}
