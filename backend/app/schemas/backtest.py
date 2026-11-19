"""Validated inputs for the isolated historical simulation."""
from datetime import date
from decimal import Decimal
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from backend.app.services.sma import MAX_PERIOD


class BacktestRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    symbol: Literal["AAPL"]
    start_date: date
    end_date: date
    initial_cash: Decimal = Field(gt=0, max_digits=16, decimal_places=6)
    short_period: int = Field(strict=True, ge=1, le=MAX_PERIOD)
    long_period: int = Field(strict=True, ge=1, le=MAX_PERIOD)

    @model_validator(mode="after")
    def validate_range_and_periods(self):
        if self.start_date > self.end_date or self.end_date == date.max:
            raise ValueError("Select an inclusive date range with start on or before end")
        if self.short_period >= self.long_period:
            raise ValueError("Short period must be smaller than long period")
        return self
