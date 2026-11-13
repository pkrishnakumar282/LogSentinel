"""Flat, checksummed local evaluation directories, not an authenticity/signature mechanism."""
import json
from pathlib import Path

from .artifacts import sha256_file, write_json


def seal(directory: Path, required: str) -> None:
    write_json(directory / "checksums.json", {
        "format_version": 1, "required": required,
        "files": {path.name: sha256_file(path) for path in sorted(directory.iterdir())
                  if path.is_file() and path.name != "checksums.json"},
    })


def read_sealed(directory: str, required: str) -> dict:
    root = Path(directory)
    manifest = json.loads((root / "checksums.json").read_text())
    if manifest.get("format_version") != 1 or required not in manifest.get("files", {}):
        raise ValueError("invalid_evaluation_manifest")
    for name, expected in manifest["files"].items():
        if Path(name).name != name or not (root / name).is_file() or sha256_file(root / name) != expected:
            raise ValueError("evaluation_checksum_mismatch")
    return json.loads((root / required).read_text())
