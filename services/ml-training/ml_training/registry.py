"""Fail-closed approval, actual MLflow model versions, explicit aliases and durable audit."""
from contextlib import contextmanager
import json
import os
from pathlib import Path
import re
import secrets
import shutil
import tempfile

from shared.common.redaction import redact
from shared.common.model_integrity import tree_digest
from shared.schemas.logs import identifier

from .artifacts import sha256_file
from .audit import LifecycleAudit
from .evaluation import GatePolicy, gate_result
from .integrity import read_sealed
from .tracking import TrackingFailure, client_for, experiment, log_values, tracking_context


class ApprovalDenied(ValueError):
    pass


def require_admin(provided: str) -> None:
    expected = os.environ.get("ADMIN_API_TOKEN", "")
    if len(expected) < 24 or not secrets.compare_digest(provided.encode(), expected.encode()):
        raise ApprovalDenied("admin_authorization_required")


def model_name(value: str) -> str:
    if not re.fullmatch(r"[A-Za-z0-9_.-]{1,128}", value):
        raise ValueError("invalid_registered_model_name")
    return value


@contextmanager
def curated_code():
    # Never let code_paths recursively upload ml/datasets, artifacts, .env or raw logs.
    root = Path(__file__).resolve().parents[3]
    with tempfile.TemporaryDirectory(prefix="logsentinel-code-") as directory:
        target = Path(directory)
        for package, paths in {
            "ml": ["__init__.py", "model.py", "mlflow_model.py", "features"],
            "shared": ["__init__.py", "common", "schemas"],
        }.items():
            for path in paths:
                source = root / package / path
                if source.is_dir():
                    for file in source.glob("*.py"):
                        destination = target / package / path / file.name
                        destination.parent.mkdir(parents=True, exist_ok=True)
                        shutil.copyfile(file, destination)
                else:
                    destination = target / package / path
                    destination.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copyfile(source, destination)
        yield [str(target / "ml"), str(target / "shared")]


def package_digest(uri: str, source: str) -> str:
    """Download without loading Python/pickle, then hash the complete model package."""
    import mlflow.artifacts
    with tempfile.TemporaryDirectory(prefix="logsentinel-verify-") as directory:
        with tracking_context(uri):
            root = Path(mlflow.artifacts.download_artifacts(artifact_uri=source, dst_path=directory))
        return tree_digest(root)


def register_evaluation(directory: str, uri: str, audit_path: str, token: str,
                        actor: str, name: str = "logsentinel-model", experiment_name: str = "LogSentinel",
                        artifact_root: str | None = None) -> dict:
    require_admin(token)
    identifier(actor)
    name = model_name(name)
    report = read_sealed(directory, "evaluation.json")
    policy = GatePolicy(**report["policy"])
    selected = report["selected_result"]
    gate = gate_result(selected["test_metrics"], selected["timing"]["batch_ms_per_event"],
                       policy, report["provenance_kinds"])
    audit = LifecycleAudit(audit_path)
    digest = sha256_file(Path(directory) / "evaluation.json")
    details = {"evaluation_sha256": digest, "model_name": name, "scope": policy.scope,
               "bundle_sha256": sha256_file(Path(directory) / "selected.joblib")}
    if not gate["passed"] or not report.get("selection_frozen_before_test"):
        audit.record("register", actor, "rejected", details | {"reasons": gate["reasons"]})
        raise ApprovalDenied("evaluation_quality_gate_failed")
    client = client_for(uri)
    with audit.lock():
        # Retrying the same approved immutable evaluation reuses its existing version.
        for existing in client.search_model_versions(f"name = '{name}'"):
            if existing.tags.get("logsentinel.evaluation_sha256") == digest:
                saved = audit.approved_registration(name, existing.version)
                if (saved and saved["details"].get("bundle_sha256") == details["bundle_sha256"]
                        and package_digest(uri, existing.source) == saved["details"].get("model_package_sha256")):
                    return {"model_name": name, "version": str(existing.version), "status": "already_registered"}
                raise ApprovalDenied("registration_requires_audit_reconciliation")
        intent = audit.record("register", actor, "pending", details)
        try:
            import mlflow
            from ml.mlflow_model import FeatureRowModel
            from ml.features.contract import CATEGORICAL_COLUMNS, NUMERIC_COLUMNS
            from mlflow.models import ModelSignature
            from mlflow.types import ColSpec, Schema
            experiment_id = experiment(client, experiment_name, artifact_root)
            run = client.create_run(experiment_id, tags={"mlflow.runName": "validated-" + digest[:12],
                "logsentinel.operation": "validated_model_registration", "logsentinel.scope": policy.scope})
            with tracking_context(uri), mlflow.start_run(run_id=run.info.run_id), curated_code() as code:
                log_values(client, run.info.run_id, params={"threshold": selected["threshold"],
                    "candidate_id": report["selected_candidate_id"], "evaluation_sha256": digest,
                    "training_dataset_version": report["training_dataset_version"], "policy": report["policy"]},
                    metrics=selected["test_metrics"])
                client.log_artifact(run.info.run_id, str(Path(directory) / "evaluation.json"))
                client.log_artifact(run.info.run_id, str(Path(directory) / "checksums.json"))
                requirements = [line for line in (Path(__file__).parents[1] / "requirements.txt").read_text().splitlines()
                                if line and not line.startswith("#")]
                tracking_requirements = Path(__file__).parents[1] / "requirements-mlflow.txt"
                if tracking_requirements.is_file():
                    requirements += [line for line in tracking_requirements.read_text().splitlines()
                                     if line and not line.startswith(("#", "-r"))]
                else:
                    requirements.append("mlflow==3.18.0")
                signature = ModelSignature(inputs=Schema(
                    [ColSpec("string", key) for key in ["text", *CATEGORICAL_COLUMNS]]
                    + [ColSpec("double", key) for key in NUMERIC_COLUMNS]),
                    outputs=Schema([ColSpec("double", "anomaly_score"), ColSpec("boolean", "is_anomaly")]))
                logged = mlflow.pyfunc.log_model(name="validated-model", python_model=FeatureRowModel(),
                    artifacts={"bundle": str(Path(directory) / "selected.joblib")}, code_paths=code,
                    pip_requirements=requirements, signature=signature,
                    metadata={"evaluation_sha256": digest, "scope": policy.scope,
                              "threshold": selected["threshold"], "feature_version": report["feature_version"]})
                version = mlflow.register_model(logged.model_uri, name, await_registration_for=30,
                    tags={"logsentinel.validation_status": "passed", "logsentinel.scope": policy.scope,
                          "logsentinel.evaluation_sha256": digest,
                          "logsentinel.bundle_sha256": details["bundle_sha256"],
                          "logsentinel.candidate_id": report["selected_candidate_id"]})
            package_hash = package_digest(uri, version.source)
            audit.record("register", actor, "succeeded", details | {"intent_id": intent,
                         "model_package_sha256": package_hash,
                         "version": str(version.version), "run_id": run.info.run_id, "source": version.source})
            return {"model_name": name, "version": str(version.version), "run_id": run.info.run_id,
                    "scope": policy.scope, "status": "registered_without_alias"}
        except Exception:
            audit.record("register", actor, "failed", details | {"intent_id": intent})
            raise TrackingFailure("mlflow_registration_failed") from None


def promote(uri: str, audit_path: str, token: str, actor: str, name: str,
            version: str, alias: str, reason: str) -> dict:
    require_admin(token)
    identifier(actor)
    name = model_name(name)
    if not version.isdigit() or alias not in ("staging", "champion"):
        raise ValueError("invalid_promotion_target")
    reason = redact(reason)[:256]
    if not reason:
        raise ValueError("promotion_reason_required")
    audit = LifecycleAudit(audit_path)
    client = client_for(uri)
    with audit.lock():
        saved = audit.approved_registration(name, version)
        if saved is None:
            raise ApprovalDenied("audited_registration_required")
        current = client.get_model_version(name, version)
        tags = current.tags
        if (tags.get("logsentinel.validation_status") != "passed"
                or tags.get("logsentinel.evaluation_sha256") != saved["details"]["evaluation_sha256"]
                or tags.get("logsentinel.bundle_sha256") != saved["details"]["bundle_sha256"]
                or tags.get("logsentinel.scope") != saved["details"]["scope"]
                or current.source != saved["details"]["source"]):
            raise ApprovalDenied("registry_validation_evidence_mismatch")
        if alias == "champion" and saved["details"]["scope"] != "production":
            audit.record("promote", actor, "rejected", {"model_name": name, "version": version,
                         "alias": alias, "reason": "production_scope_required"})
            raise ApprovalDenied("production_scope_required")
        if package_digest(uri, current.source) != saved["details"].get("model_package_sha256"):
            audit.record("promote", actor, "rejected", {"model_name": name, "version": version,
                         "alias": alias, "reason": "registered_model_package_changed"})
            raise ApprovalDenied("registered_model_package_changed")
        try:
            aliases = client.get_registered_model(name).aliases
            previous = str(aliases[alias]) if alias in aliases else None
        except Exception:
            raise TrackingFailure("mlflow_alias_lookup_failed") from None
        details = {"model_name": name, "version": version, "previous_version": previous,
                   "alias": alias, "reason": reason, "evaluation_sha256": saved["details"]["evaluation_sha256"]}
        intent = audit.record("promote", actor, "pending", details)
        try:
            client.set_registered_model_alias(name, alias, version)
        except Exception:
            audit.record("promote", actor, "failed", details | {"intent_id": intent})
            raise TrackingFailure("mlflow_alias_update_failed") from None
        audit.record("promote", actor, "succeeded", details | {"intent_id": intent})
        return {"model_name": name, "version": version, "alias": alias, "previous_version": previous}
