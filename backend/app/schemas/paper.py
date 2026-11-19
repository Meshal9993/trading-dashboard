"""Request validation for local paper orders."""
from typing import Literal, Optional
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, model_validator


class PaperOrderRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    request_id: UUID
    side: Literal["buy", "sell", "close"]
    quantity: Optional[int] = Field(default=None, strict=True, ge=1, le=1_000_000)

    @model_validator(mode="after")
    def validate_quantity(self):
        if self.side == "close":
            if self.quantity is not None:
                raise ValueError("Close Position determines the quantity from the position")
        elif self.quantity is None:
            raise ValueError("Buy and Sell require a positive whole share quantity")
        return self
