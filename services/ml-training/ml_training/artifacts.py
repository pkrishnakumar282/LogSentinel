"""Checksummed candidate artifacts; this phase never approves or promotes a model."""
import hashlib
import json
from pathlib import Path

from ml.features import FEATURE_VERSION
from ml.model import ModelBundle, runtime_versions


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def write_json(path: Path, value) -> None:
    path.write_text(json.dumps(value, indent=2, sort_keys=True, allow_nan=False) + "\n")


def write_checksums(directory: Path) -> None:
    files = sorted(path for path in directory.iterdir() if path.is_file() and path.name != "manifest.json")
    write_json(directory / "manifest.json", {
        "format_version": 1, "status": "candidate_not_approved",
        "files": {path.name: sha256_file(path) for path in files},
    })


def load_bundle(directory: str, candidate_id: str) -> ModelBundle:
    """Only load artifacts from a trusted local training run; joblib is executable pickle."""
    import joblib
    root = Path(directory)
    manifest = json.loads((root / "manifest.json").read_text())
    report = json.loads((root / "report.json").read_text())
    if "report.json" not in manifest.get("files", {}):
        raise ValueError("missing_report_checksum")
    # Validate metadata before unpickling, including the report's own checksum.
    for name, expected in manifest["files"].items():
        if Path(name).name != name or sha256_file(root / name) != expected:
            raise ValueError("artifact_checksum_mismatch")
    if manifest.get("format_version") != 1 or report.get("feature_version") != FEATURE_VERSION:
        raise ValueError("unsupported_artifact_format")
    current = runtime_versions()
    expected = report["dependencies"]
    if any(current[key] != expected[key] for key in current if key != "python"):
        raise ValueError("artifact_dependency_version_mismatch")
    if current["python"].split(".")[:2] != expected["python"].split(".")[:2]:
        raise ValueError("artifact_python_version_mismatch")
    candidates = {item["candidate_id"] for item in report["candidates"]}
    if candidate_id not in candidates:
        raise ValueError("unknown_candidate_id")
    filename = f"{candidate_id}.joblib"
    if filename not in manifest["files"]:
        raise ValueError("missing_candidate_checksum")
    bundle = joblib.load(root / filename)
    if not isinstance(bundle, ModelBundle) or bundle.candidate_id != candidate_id:
        raise ValueError("invalid_model_bundle")
    return bundle
