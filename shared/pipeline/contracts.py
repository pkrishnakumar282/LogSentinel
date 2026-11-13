from dataclasses import dataclass
import json
import re
from uuid import UUID


class Deferred(RuntimeError):
    """Dependency or backpressure: retain pending entry without spending poison retries."""


class Retryable(RuntimeError):
    pass


class Rejected(ValueError):
    pass


class StaleCheckpoint(Deferred):
    pass


def reason_code(reason: str) -> str:
    return reason if re.fullmatch(r'[a-z_]{1,80}', reason) else 'processing_failed'


@dataclass(frozen=True)
class Delivery:
    entry_id: str
    fields: dict

    def payload(self) -> dict:
        try:
            raw = self.fields['payload']
            if not isinstance(raw, str) or len(raw.encode()) > 400000:
                raise ValueError
            result = json.loads(raw)
            if not isinstance(result, dict):
                raise ValueError
            return result
        except (KeyError, ValueError, TypeError, UnicodeError, RecursionError):
            raise Rejected('invalid_stream_payload') from None


@dataclass(frozen=True)
class PredictionNotice:
    prediction_id: str
    event_id: str
    schema_version: int = 1

    def __post_init__(self):
        try:
            UUID(self.prediction_id)
            UUID(self.event_id)
        except (ValueError, TypeError, AttributeError):
            raise Rejected('invalid_prediction_notice') from None
        if type(self.schema_version) is not int or self.schema_version != 1:
            raise Rejected('unsupported_prediction_notice')

    @classmethod
    def parse(cls, value: dict):
        try:
            return cls(**value)
        except TypeError:
            raise Rejected('invalid_prediction_notice') from None

    def to_dict(self):
        return {'schema_version': 1, 'prediction_id': self.prediction_id, 'event_id': self.event_id}
