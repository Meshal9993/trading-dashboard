"""Validated, source-neutral candle observations."""
from datetime import datetime, timezone

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


class Observation(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, allow_inf_nan=False)

    symbol: str = Field(min_length=1)
    source: str = Field(min_length=1)
    observation_id: str = Field(min_length=1)
    event_time: datetime
    receipt_time: datetime
    open: float = Field(ge=0)
    high: float = Field(ge=0)
    low: float = Field(ge=0)
    close: float = Field(ge=0)
    volume: int = Field(ge=0, le=9223372036854775807, strict=True)
    revision: int = Field(ge=0, le=9223372036854775807, strict=True)
    is_complete: bool = Field(strict=True)

    @field_validator("symbol", "source", "observation_id")
    @classmethod
    def clean_identifier(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("Identifiers must not be blank")
        return value

    @field_validator("event_time", "receipt_time")
    @classmethod
    def utc_time(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("Timestamps must include a timezone")
        return value.astimezone(timezone.utc)

    @model_validator(mode="after")
    def valid_candle(self):
        if not self.low <= min(self.open, self.close) <= max(self.open, self.close) <= self.high:
            raise ValueError("OHLC prices must lie between low and high")
        return self
