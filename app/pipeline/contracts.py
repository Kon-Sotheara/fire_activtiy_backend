from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field


class CleanHotspot(BaseModel):
    """Canonical cleaned record produced before harmonization and database storage."""

    sensor: Literal["MODIS", "VIIRS"]
    platform: str
    acquired_at_utc: datetime
    latitude: float = Field(ge=-90, le=90)
    longitude: float = Field(ge=-180, le=180)
    confidence_raw: str | float | int
    confidence_standardized: Literal["low", "nominal", "high"] | None = None
    frp_mw: float | None = Field(default=None, ge=0)
    source_product: str
    source_version: str
    source_record_key: str
