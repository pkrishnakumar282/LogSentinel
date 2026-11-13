"""Sanitized dataset import, content addressing and strictly temporal split contracts."""
from dataclasses import dataclass, replace
import hashlib
import json
import math
from pathlib import Path

from shared.common.privacy import is_authentication_record
from shared.common.redaction import redact
from shared.schemas import InvalidRecord, NormalizedLog
from shared.schemas.logs import utc_time


class DatasetError(ValueError):
    pass


@dataclass(frozen=True)
class Dataset:
    events: tuple[NormalizedLog, ...]
    version: str
    provenance: dict
    duplicates_removed: int


def digest_ids(events) -> str:
    return hashlib.sha256("\n".join(event.event_id for event in events).encode()).hexdigest()


def validate_provenance(events, provenance):
    if provenance is not None and not isinstance(provenance, dict):
        raise DatasetError("invalid_provenance_manifest")
    flags = {event.metadata.get("synthetic") for event in events}
    if flags == {True}:
        if provenance and provenance.get("kind") != "synthetic":
            raise DatasetError("synthetic_provenance_mismatch")
        return {"kind": "synthetic", "description": "Explicitly labeled development records"}
    if True in flags:
        raise DatasetError("mixed_synthetic_and_real_data")
    if flags != {False}:
        raise DatasetError("missing_synthetic_provenance_flag")
    if not isinstance(provenance, dict) or provenance.get("kind") not in ("local", "public"):
        raise DatasetError("real_data_requires_provenance_manifest")
    if not isinstance(provenance.get("dataset_name"), str) or not provenance["dataset_name"]:
        raise DatasetError("missing_dataset_name")
    # Persist only approved provenance fields, never arbitrary operator secrets.
    fields = ["kind", "dataset_name", "acquired_at", "source_url", "license", "license_url"]
    if provenance["kind"] == "public":
        if provenance.get("license_verified") is not True or not all(provenance.get(key) for key in fields):
            raise DatasetError("public_dataset_requires_license_verification")
    clean = {key: redact(str(provenance[key]))[:512] for key in fields if key in provenance}
    if provenance["kind"] == "public":
        clean["license_verified"] = True
    return clean


def load_dataset(path: str, provenance: dict | None = None, max_records: int = 1000000) -> Dataset:
    events, identities, contents = [], {}, set()
    duplicates = 0
    with Path(path).open(encoding="utf-8") as stream:
        for index in range(max_records + 1):
            line = stream.readline(400001)
            if not line:
                break
            if index == max_records:
                raise DatasetError("dataset_record_limit_exceeded")
            if len(line) > 400000:
                raise DatasetError("dataset_line_too_large")
            try:
                value = json.loads(line)
                event = NormalizedLog(**value)
                if not isinstance(event.metadata, dict):
                    raise ValueError
                if is_authentication_record(event.service, event.message):
                    raise ValueError
                # Reapply redaction at the artifact boundary, including untrusted template text.
                event = replace(event, timestamp=utc_time(event.timestamp).isoformat(),
                                message=redact(event.message), template=redact(event.template),
                                metadata={"synthetic": event.metadata.get("synthetic"),
                                          "redaction_version": "1"})
                if type(event.metadata["synthetic"]) is not bool:
                    raise ValueError
            except (InvalidRecord, ValueError, TypeError, AttributeError, UnicodeError, RecursionError):
                raise DatasetError("invalid_normalized_dataset_record") from None
            payload = event.to_dict()
            payload.pop("event_id")
            content = hashlib.sha256(json.dumps(payload, sort_keys=True).encode()).hexdigest()
            if event.event_id in identities:
                if identities[event.event_id] != content:
                    raise DatasetError("conflicting_dataset_event_id")
                duplicates += 1
                continue
            identities[event.event_id] = content
            if content in contents:
                duplicates += 1
                continue
            contents.add(content)
            events.append(event)
    if not events:
        raise DatasetError("empty_dataset")
    events.sort(key=lambda event: (utc_time(event.timestamp), event.event_id))
    version = hashlib.sha256("\n".join(event.to_json() for event in events).encode()).hexdigest()
    return Dataset(tuple(events), version, validate_provenance(events, provenance), duplicates)


@dataclass(frozen=True)
class TemporalSplit:
    train: tuple[NormalizedLog, ...]
    holdout: tuple[NormalizedLog, ...]

    def manifest(self):
        return {"strategy": "time_holdout", "train_count": len(self.train),
                "holdout_count": len(self.holdout), "train_ids_sha256": digest_ids(self.train),
                "holdout_ids_sha256": digest_ids(self.holdout),
                "train_start": self.train[0].timestamp, "train_end": self.train[-1].timestamp,
                "holdout_start": self.holdout[0].timestamp, "holdout_end": self.holdout[-1].timestamp}


def temporal_split(events, holdout_fraction: float = 0.2, min_train: int = 32,
                   min_holdout: int = 8) -> TemporalSplit:
    if (type(holdout_fraction) not in (int, float) or not math.isfinite(holdout_fraction)
            or not 0 < holdout_fraction < 1):
        raise DatasetError("invalid_holdout_fraction")
    if (type(min_train) is not int or type(min_holdout) is not int
            or min_train < 2 or min_holdout < 1):
        raise DatasetError("invalid_split_minimum")
    ordered = sorted(events, key=lambda event: (utc_time(event.timestamp), event.event_id))
    if len({event.event_id for event in ordered}) != len(ordered):
        raise DatasetError("split_requires_unique_event_ids")
    boundaries = [index for index in range(min_train, len(ordered) - min_holdout + 1)
                  if utc_time(ordered[index - 1].timestamp) < utc_time(ordered[index].timestamp)]
    if not boundaries:
        raise DatasetError("insufficient_distinct_timestamps_for_holdout")
    target = len(ordered) * (1 - holdout_fraction)
    boundary = min(boundaries, key=lambda index: abs(index - target))
    return TemporalSplit(tuple(ordered[:boundary]), tuple(ordered[boundary:]))
