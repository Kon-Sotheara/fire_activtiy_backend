from datetime import date
from typing import Literal

from pydantic import BaseModel, Field


class HotspotRecord(BaseModel):
    id: str
    latitude: float
    longitude: float
    acq_date: date
    acq_time: str | None
    sensor: Literal["MODIS", "VIIRS"]
    satellite: str | None
    instrument: str | None
    brightness: float | None
    bright_t31: float | None
    frp: float | None
    confidence: str | None
    daynight: str | None
    fire_type: int | None


class HotspotPage(BaseModel):
    total: int = Field(ge=0)
    limit: int = Field(ge=1)
    offset: int = Field(ge=0)
    items: list[HotspotRecord]


class ActivitySummary(BaseModel):
    total_detections: int = Field(ge=0)
    historical_annual_median: int = Field(ge=0)
    anomaly_percent: float
    anomaly_label: Literal["Low", "Normal", "High"]
    selected_start_date: date | None
    selected_end_date: date | None
    applied_sensor_view: Literal["modis", "viirs", "harmonized"]


class ActivityPoint(BaseModel):
    period: str
    activity: int = Field(ge=0)


class CalendarCell(BaseModel):
    year: int
    period: int
    activity: int = Field(ge=0)


class SensorComparison(BaseModel):
    modis: int = Field(ge=0)
    viirs: int = Field(ge=0)
    combined: int = Field(ge=0)
    viirs_to_modis_ratio: float | None
