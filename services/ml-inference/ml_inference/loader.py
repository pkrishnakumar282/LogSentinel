"""Read-only registry evidence and package verification before trusted Joblib loading."""
from contextlib import closing
from dataclasses import dataclass
import json
import math
import os
from pathlib import Path
import sqlite3
import tempfile
from uuid import UUID

from ml.features import FEATURE_VERSION, FeatureExtractor
from ml.model import ModelBundle, runtime_versions
from shared.common.model_integrity import artifact_location, file_digest, tree_digest
from shared.schemas import NormalizedLog


class ModelLoadError(RuntimeError):
    def __init__(self, reason: str, resolved_version: str | None = None, definitive: bool = False):
        super().__init__(reason)
        self.resolved_version = resolved_version
        self.definitive = definitive


@dataclass(frozen=True)
class LoadedModel:
    bundle: ModelBundle
    name: str
    version: str
    scope: str
    package_sha256: str
    evaluation_sha256: str


def audited_registration(path: str, name: str, version: str) -> dict:
    audit = Path(path).resolve()
    if not audit.is_file():
        raise ModelLoadError('registration_evidence_missing')
    try:
        with closing(sqlite3.connect(audit.as_uri() + '?mode=ro', uri=True)) as db:
            rows = db.execute("SELECT details FROM audit_events WHERE action='register' "
                              "AND status='succeeded' ORDER BY rowid DESC").fetchall()
        for row in rows:
            item = json.loads(row[0])
            if item.get('model_name') == name and str(item.get('version')) == version:
                return item
    except (sqlite3.Error, ValueError, TypeError):
        raise ModelLoadError('registration_evidence_invalid') from None
    raise ModelLoadError('registration_evidence_missing')


def validate_runtime(bundle):
    if not isinstance(bundle, ModelBundle) or bundle.feature_version != FEATURE_VERSION:
        raise ModelLoadError('unsupported_model_format')
    current = runtime_versions()
    expected = bundle.dependencies
    if (current['python'].split('.')[:2] != expected.get('python', '').split('.')[:2]
            or any(current[key] != expected.get(key) for key in current if key != 'python')):
        raise ModelLoadError('model_runtime_incompatible')
    if type(bundle.threshold) not in (int, float) or not math.isfinite(bundle.threshold):
        raise ModelLoadError('invalid_artifact_threshold')


def validate_package_runtime(root: Path, metadata: dict):
    current = runtime_versions()
    python_version = metadata['flavors']['python_function'].get('python_version', '')
    if python_version.split('.')[:2] != current['python'].split('.')[:2]:
        raise ModelLoadError('model_runtime_incompatible')
    pins = {}
    for line in (root / 'requirements.txt').read_text().splitlines():
        if '==' in line and not line.startswith('#'):
            name, version = line.split('==', 1)
            pins[name.lower().replace('_', '-')] = version.strip()
    if any(pins.get(name) != version for name, version in current.items() if name != 'python'):
        raise ModelLoadError('model_runtime_incompatible')


class RegistryLoader:
    def load(self, settings) -> LoadedModel:
        os.environ.setdefault('MLFLOW_DISABLE_AGENT_HINT', '1')
        os.environ.setdefault('MLFLOW_ENABLE_TELEMETRY', 'false')
        os.environ.setdefault('MLFLOW_HTTP_REQUEST_TIMEOUT', '10')
        os.environ.setdefault('MLFLOW_HTTP_REQUEST_MAX_RETRIES', '2')
        version = None
        try:
            from mlflow import MlflowClient
            import mlflow.artifacts
            import yaml
            import joblib
            if settings.tracking_uri.startswith('sqlite:///'):
                db_path = Path(settings.tracking_uri[len('sqlite:///'):].split('?', 1)[0])
                if not db_path.is_file():
                    raise ModelLoadError('registry_unavailable')
            client = MlflowClient(tracking_uri=settings.tracking_uri, registry_uri=settings.tracking_uri)
            if settings.model_version:
                registered = client.get_model_version(settings.model_name, settings.model_version)
            else:
                registered = client.get_model_version_by_alias(settings.model_name, settings.model_alias)
            version = str(registered.version)
            if str(registered.status) != 'READY':
                raise ModelLoadError('registry_version_not_ready')
            evidence = audited_registration(settings.audit_path, settings.model_name, version)
            tags = registered.tags
            if (tags.get('logsentinel.validation_status') != 'passed'
                    or tags.get('logsentinel.evaluation_sha256') != evidence.get('evaluation_sha256')
                    or tags.get('logsentinel.bundle_sha256') != evidence.get('bundle_sha256')
                    or tags.get('logsentinel.scope') != evidence.get('scope')
                    or registered.source != evidence.get('source')
                    or registered.run_id != evidence.get('run_id')):
                raise ModelLoadError('registry_approval_mismatch')
            scope = evidence.get('scope')
            if scope not in ('production', 'development') or (settings.scope == 'production' and scope != 'production'):
                raise ModelLoadError('model_scope_not_allowed')
            if not settings.model_version and settings.model_alias == 'champion' and scope != 'production':
                raise ModelLoadError('champion_requires_production_evidence')
            # Explicit URIs avoid mutating MLflow's global tracking context in a threaded service.
            with tempfile.TemporaryDirectory(prefix='logsentinel-model-') as directory:
                root = Path(mlflow.artifacts.download_artifacts(artifact_uri=artifact_location(client, registered.source),
                    tracking_uri=settings.tracking_uri, registry_uri=settings.tracking_uri, dst_path=directory))
                digest = tree_digest(root)
                if digest != evidence.get('model_package_sha256'):
                    raise ModelLoadError('model_package_checksum_mismatch')
                metadata = yaml.safe_load((root / 'MLmodel').read_text())
                validate_package_runtime(root, metadata)
                relative = metadata['flavors']['python_function']['artifacts']['bundle']['path']
                path = (root / relative).resolve()
                if not path.is_relative_to(root.resolve()) or not path.is_file():
                    raise ModelLoadError('invalid_model_artifact_path')
                if file_digest(path) != evidence.get('bundle_sha256'):
                    raise ModelLoadError('model_bundle_checksum_mismatch')
                bundle = joblib.load(path)  # Only after approval, whole-package and bundle verification.
            validate_runtime(bundle)
            if (bundle.candidate_id != tags.get('logsentinel.candidate_id')
                    or metadata.get('metadata', {}).get('evaluation_sha256') != evidence.get('evaluation_sha256')
                    or metadata.get('metadata', {}).get('threshold') != bundle.threshold
                    or metadata.get('metadata', {}).get('feature_version') != bundle.feature_version):
                raise ModelLoadError('model_metadata_mismatch')
            probe = NormalizedLog(str(UUID(int=1)), '2000-01-01T00:00:00+00:00', 'application',
                                  'inference-probe', 'localhost', 'INFO', 'model readiness probe',
                                  'model readiness probe', metadata={'synthetic': True})
            if len(bundle.score_rows(FeatureExtractor(bundle.feature_config).transform([probe]))) != 1:
                raise ModelLoadError('model_probe_failed')
            return LoadedModel(bundle, settings.model_name, version, scope, digest, evidence['evaluation_sha256'])
        except ModelLoadError as exc:
            exc.resolved_version = version
            exc.definitive = str(exc) in {
                'registry_approval_mismatch', 'model_package_checksum_mismatch',
                'model_bundle_checksum_mismatch', 'model_metadata_mismatch',
                'model_runtime_incompatible', 'registry_version_not_ready',
            }
            raise
        except Exception as exc:
            if getattr(exc, 'error_code', None) in ('RESOURCE_DOES_NOT_EXIST', 'INVALID_PARAMETER_VALUE'):
                raise ModelLoadError('approved_version_not_found') from None
            raise ModelLoadError('model_loading_failed') from None
