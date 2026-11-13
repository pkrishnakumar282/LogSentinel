"""Strict inference HTTP contracts. Domain processing remains dependency-light."""
from typing import Any, Literal
from pydantic import BaseModel, ConfigDict, Field

from .logs import InvalidRecord, NormalizedLog, Severity, Source


class NormalizedEventInput(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True)
    event_id: str = Field(min_length=36, max_length=36)
    timestamp: str = Field(min_length=1, max_length=64)
    source: Source
    service: str = Field(min_length=1, max_length=128)
    hostname: str = Field(min_length=1, max_length=128)
    severity: Severity
    message: str = Field(min_length=1, max_length=65536, repr=False)
    template: str = Field(min_length=1, max_length=65536, repr=False)
    status_code: int | None = Field(default=None, ge=100, le=599)
    metadata: dict[str, Any] = Field(default_factory=dict, max_length=32, repr=False)

    def domain(self):
        try:
            return NormalizedLog(**self.model_dump())
        except (ValueError, TypeError, AttributeError, UnicodeError):
            raise InvalidRecord('invalid_normalized_event') from None


class SinglePredictionRequest(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True)
    event: NormalizedEventInput
    context_id: str = Field(default='default', pattern=r'^[A-Za-z0-9_.:-]{1,64}$')


class BatchPredictionRequest(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True)
    events: list[NormalizedEventInput] = Field(min_length=1, max_length=4096)
    context_id: str = Field(default='default', pattern=r'^[A-Za-z0-9_.:-]{1,64}$')


class PredictionResponse(BaseModel):
    event_id: str
    prediction: Literal['normal', 'anomaly']
    is_anomaly: bool
    anomaly_score: float
    model_name: str
    model_version: str
    threshold: float
    context_warm: bool
    feature_version: str
    threshold_source: Literal['artifact', 'environment_override']


class BatchPredictionResponse(BaseModel):
    predictions: list[PredictionResponse]
    context_id: str


class ReloadRequest(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True)
    model_version: str | None = Field(default=None, pattern=r'^[1-9][0-9]{0,18}$')
    model_alias: Literal['staging', 'champion'] | None = None
