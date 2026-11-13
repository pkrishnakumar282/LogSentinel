"""Model package integrity shared by registration and inference; no deserialization."""
import hashlib
from pathlib import Path


def file_digest(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open('rb') as stream:
        for chunk in iter(lambda: stream.read(1 << 20), b''):
            digest.update(chunk)
    return digest.hexdigest()


def tree_digest(root: Path) -> str:
    digest = hashlib.sha256()
    for path in sorted(root.rglob('*')):
        if path.is_symlink():
            raise ValueError('model_package_symlink_forbidden')
        if not path.is_file() or '__pycache__' in path.parts or path.suffix == '.pyc':
            continue
        digest.update(str(path.relative_to(root)).encode() + b'\0')
        digest.update(file_digest(path).encode())
    return digest.hexdigest()


def artifact_location(client, source: str) -> str:
    """Resolve MLflow 3 logged-model IDs with the explicit client, never the global default.

    MLflow 3.18's models:/m-ID artifact repository constructs a default tracking client
    internally even when download_artifacts receives tracking_uri. Resolve it first.
    """
    if source.startswith('models:/m-'):
        model_id = source.removeprefix('models:/')
        if '/' in model_id:
            raise ValueError('invalid_logged_model_source')
        return client.get_logged_model(model_id).artifact_location
    return source
