"""Strict HTTP envelope; importing the dependency-free domain contracts needs no Pydantic."""
from pydantic import BaseModel, ConfigDict, Field

from .logs import Source


class Envelope(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    event_id: str = Field(max_length=36)
    source: Source
    service: str = Field(max_length=128)
    hostname: str = Field(max_length=128)
    observed_at: str = Field(max_length=64)
    raw: str = Field(min_length=1, max_length=65536, repr=False)
    synthetic: bool = False
