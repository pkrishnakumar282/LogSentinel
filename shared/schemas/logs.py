"""Wire contracts also validated by Pydantic at the HTTP boundary."""
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
import json
import re
from typing import Literal
from uuid import UUID

Source = Literal["linux", "nginx", "application"]
Severity = Literal["INFO", "WARNING", "ERROR", "CRITICAL"]
MAX_MESSAGE_BYTES = 65536
IDENTIFIER = re.compile(r"[a-zA-Z0-9_.@:-]{1,128}\Z")


class InvalidRecord(ValueError):
    """Reason codes only: never attach untrusted log contents to exceptions."""


def identifier(value: str) -> str:
    if not isinstance(value, str) or not IDENTIFIER.fullmatch(value):
        raise InvalidRecord("invalid_identifier")
    return value


def utc_time(value: str) -> datetime:
    try:
        result = datetime.fromisoformat(value.replace("Z", "+00:00"))
        if result.tzinfo is None or result.utcoffset() is None:
            raise ValueError
        return result.astimezone(UTC)
    except (ValueError, TypeError, AttributeError, OverflowError):
        raise InvalidRecord("invalid_timestamp") from None


@dataclass(frozen=True)
class RawLog:
    event_id: str
    source: Source
    service: str
    hostname: str
    observed_at: str
    raw: str = field(repr=False)
    synthetic: bool = False

    def __post_init__(self) -> None:
        try:
            UUID(self.event_id)
        except (ValueError, AttributeError, TypeError):
            raise InvalidRecord("invalid_event_id") from None
        if self.source not in ("linux", "nginx", "application"):
            raise InvalidRecord("invalid_source")
        identifier(self.service)
        identifier(self.hostname)
        utc_time(self.observed_at)
        if not isinstance(self.raw, str) or not self.raw.strip():
            raise InvalidRecord("empty_message")
        try:
            size = len(self.raw.encode("utf-8"))
        except UnicodeError:
            raise InvalidRecord("invalid_text_encoding") from None
        if size > MAX_MESSAGE_BYTES:
            raise InvalidRecord("record_too_large")
        if not isinstance(self.synthetic, bool):
            raise InvalidRecord("invalid_synthetic_flag")

    @classmethod
    def from_dict(cls, value: dict) -> "RawLog":
        try:
            return cls(**value)
        except TypeError:
            raise InvalidRecord("invalid_envelope") from None

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass(frozen=True)
class NormalizedLog:
    event_id: str
    timestamp: str
    source: Source
    service: str
    hostname: str
    severity: Severity
    message: str = field(repr=False)
    template: str = field(repr=False)
    status_code: int | None = None
    metadata: dict = field(default_factory=dict)

    def __post_init__(self) -> None:
        # Reuse the envelope's strict identity and timestamp validation.
        RawLog(self.event_id, self.source, self.service, self.hostname, self.timestamp, self.message)
        if self.severity not in ("INFO", "WARNING", "ERROR", "CRITICAL"):
            raise InvalidRecord("invalid_severity")
        if self.status_code is not None and (
            type(self.status_code) is not int or not 100 <= self.status_code <= 599
        ):
            raise InvalidRecord("invalid_status_code")
        if not isinstance(self.template, str) or not self.template:
            raise InvalidRecord("invalid_template")
        try:
            if len(self.template.encode()) > MAX_MESSAGE_BYTES:
                raise InvalidRecord("invalid_template")
        except UnicodeError:
            raise InvalidRecord("invalid_template_encoding") from None
        if not isinstance(self.metadata, dict):
            raise InvalidRecord("invalid_metadata")
        try:
            if len(json.dumps(self.metadata, allow_nan=False).encode()) > 4096:
                raise InvalidRecord("metadata_too_large")
        except (ValueError, TypeError, RecursionError, UnicodeError):
            raise InvalidRecord("invalid_metadata") from None

    def to_dict(self) -> dict:
        return asdict(self)

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), sort_keys=True, separators=(",", ":"), allow_nan=False)
