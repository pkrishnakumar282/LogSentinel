"""Actual MLflow experiments with explicit local stores and no silent logging fallback."""
from contextlib import contextmanager
import json
import math
import os
from pathlib import Path
from urllib.parse import urlsplit

from .artifacts import sha256_file


class TrackingFailure(RuntimeError):
    pass


def validate_tracking_uri(uri: str) -> str:
    # Default operational boundary is local. An external URI requires deliberate opt-in.
    if uri.startswith("sqlite:///"):
        if uri == "sqlite:///:memory:":
            raise ValueError("persistent_mlflow_database_required")
        return uri
    parsed = urlsplit(uri)
    if (parsed.scheme in ("http", "https") and not parsed.username and not parsed.password
            and not parsed.query and not parsed.fragment):
        if parsed.hostname in ("localhost", "127.0.0.1", "::1", "mlflow"):
            return uri
        if os.environ.get("MLFLOW_ALLOW_REMOTE") == "true":
            return uri
    raise ValueError("unsupported_or_nonlocal_tracking_uri")


def client_for(uri: str):
    os.environ.setdefault("MLFLOW_DISABLE_AGENT_HINT", "1")
    os.environ.setdefault("MLFLOW_ENABLE_TELEMETRY", "false")
    os.environ.setdefault("MLFLOW_HTTP_REQUEST_TIMEOUT", "10")
    os.environ.setdefault("MLFLOW_HTTP_REQUEST_MAX_RETRIES", "2")
    from mlflow import MlflowClient
    if uri.startswith("sqlite:///"):
        Path(uri[len("sqlite:///"):].split("?", 1)[0]).parent.mkdir(parents=True, exist_ok=True)
    return MlflowClient(tracking_uri=validate_tracking_uri(uri), registry_uri=uri)


@contextmanager
def tracking_context(uri: str):
    import mlflow
    old_tracking, old_registry = mlflow.get_tracking_uri(), mlflow.get_registry_uri()
    previous_environment = {key: os.environ.get(key) for key in ("MLFLOW_TRACKING_URI", "MLFLOW_REGISTRY_URI")}
    mlflow.set_tracking_uri(validate_tracking_uri(uri))
    mlflow.set_registry_uri(uri)
    try:
        yield
    finally:
        mlflow.set_tracking_uri(old_tracking)
        mlflow.set_registry_uri(old_registry)
        # MLflow setters modify os.environ. Preserve absence as well as values so a later
        # local-only training subprocess does not inherit an unintended default backend.
        for key, value in previous_environment.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value


def experiment(client, name: str, artifact_root: str | None = None):
    found = client.get_experiment_by_name(name)
    if found is not None:
        return found.experiment_id
    location = Path(artifact_root).resolve().as_uri() if artifact_root else None
    try:
        return client.create_experiment(name, artifact_location=location)
    except Exception:
        found = client.get_experiment_by_name(name)
        if found is None:
            raise
        return found.experiment_id


def flat_values(value, prefix=""):
    result = {}
    for key, child in value.items():
        name = f"{prefix}.{key}" if prefix else key
        if isinstance(child, dict):
            result.update(flat_values(child, name))
        elif child is not None:
            result[name] = child
    return result


def log_values(client, run_id: str, params=None, metrics=None):
    from mlflow.entities import Metric, Param
    import time
    timestamp = int(time.time() * 1000)
    parameters = [Param(key, str(value)) for key, value in flat_values(params or {}).items()]
    measurements = [Metric(key, float(value), timestamp, 0)
                    for key, value in flat_values(metrics or {}).items()
                    if type(value) in (int, float) and math.isfinite(value)]
    client.log_batch(run_id, metrics=measurements, params=parameters)


def track_training(directory: str, uri: str, name: str = "LogSentinel", artifact_root: str | None = None) -> dict:
    root = Path(directory)
    report = json.loads((root / "report.json").read_text())
    # Validate artifacts before logging anything, using the existing trusted loader contract.
    from .artifacts import load_bundle
    for item in report["candidates"]:
        load_bundle(directory, item["candidate_id"])
    client = client_for(uri)
    experiment_id = experiment(client, name, artifact_root)
    digest = sha256_file(root / "manifest.json")
    existing = client.search_runs([experiment_id], filter_string=(
        f"tags.`logsentinel.training_manifest_sha256` = '{digest}' AND "
        "tags.`logsentinel.operation` = 'training' AND attributes.status = 'FINISHED'"))
    if existing:
        return {"run_id": existing[0].info.run_id, "status": "already_tracked"}
    run = client.create_run(experiment_id, tags={"mlflow.runName": report["run_id"],
        "logsentinel.operation": "training", "logsentinel.training_manifest_sha256": digest,
        "logsentinel.dataset_version": report["dataset_version"],
        "logsentinel.provenance": report["dataset_provenance"]["kind"]})
    run_id = run.info.run_id
    try:
        log_values(client, run_id, params={"algorithm": "IsolationForest", "config": report["config"],
            "feature_version": report["feature_version"], "dataset_version": report["dataset_version"],
            "training_timestamp": report["training_timestamp"], "dependencies": report["dependencies"]},
            metrics={"duration_seconds": report["duration_seconds"], "split": report["split"]})
        client.log_artifact(run_id, str(root / "report.json"))
        client.log_artifact(run_id, str(root / "manifest.json"))
        for item in report["candidates"]:
            candidate = client.create_run(experiment_id, tags={"mlflow.parentRunId": run_id,
                "mlflow.runName": item["candidate_id"], "logsentinel.operation": "training_candidate",
                "logsentinel.model_status": "candidate_not_approved"})
            try:
                log_values(client, candidate.info.run_id,
                           params={"candidate_id": item["candidate_id"], "variant": item["variant"],
                                   "algorithm": item["algorithm"], "n_estimators": item["n_estimators"],
                                   "dataset_version": report["dataset_version"]}, metrics=item)
                for suffix in (".joblib", ".holdout.jsonl"):
                    client.log_artifact(candidate.info.run_id, str(root / (item["candidate_id"] + suffix)))
                client.set_terminated(candidate.info.run_id, "FINISHED")
            except Exception:
                client.set_terminated(candidate.info.run_id, "FAILED")
                raise
        client.set_terminated(run_id, "FINISHED")
    except Exception:
        client.set_terminated(run_id, "FAILED")
        raise TrackingFailure("mlflow_training_tracking_failed") from None
    return {"run_id": run_id, "status": "tracked"}


def track_evaluation(directory: str, uri: str, name: str = "LogSentinel", artifact_root: str | None = None) -> str:
    from .integrity import read_sealed
    report = read_sealed(directory, "evaluation.json")
    client = client_for(uri)
    experiment_id = experiment(client, name, artifact_root)
    digest = sha256_file(Path(directory) / "evaluation.json")
    existing = client.search_runs([experiment_id], filter_string=(
        f"tags.`logsentinel.evaluation_sha256` = '{digest}' AND "
        "tags.`logsentinel.operation` = 'evaluation' AND attributes.status = 'FINISHED'"))
    if existing:
        return existing[0].info.run_id
    run = client.create_run(experiment_id, tags={"mlflow.runName": Path(directory).name,
        "logsentinel.evaluation_sha256": digest,
        "logsentinel.operation": "evaluation", "logsentinel.gate_passed": str(report["gate"]["passed"]).lower(),
        "logsentinel.selected_candidate": report["selected_candidate_id"],
        "logsentinel.test_dataset_version": report["test_dataset_version"]})
    try:
        log_values(client, run.info.run_id, params={"test_dataset_version": report["test_dataset_version"],
            "validation_dataset_version": report["validation_dataset_version"],
            "training_dataset_version": report["training_dataset_version"], "policy": report["policy"],
            "feature_version": report["feature_version"]})
        client.log_artifact(run.info.run_id, str(Path(directory) / "evaluation.json"))
        for item in report["candidates"]:
            log_values(client, run.info.run_id,
                       metrics={item["candidate_id"]: {"test": item["test_metrics"] or {}, "timing": item["timing"]}})
        client.set_terminated(run.info.run_id, "FINISHED")
    except Exception:
        client.set_terminated(run.info.run_id, "FAILED")
        raise TrackingFailure("mlflow_evaluation_tracking_failed") from None
    return run.info.run_id
