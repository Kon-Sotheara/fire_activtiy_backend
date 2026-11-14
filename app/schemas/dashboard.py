from typing import Literal

from pydantic import BaseModel, Field


class Summary(BaseModel):
    harmonized_activity: int = Field(ge=0)
    historical_median: int = Field(ge=0)
    anomaly_percent: float
    anomaly_label: Literal["Low", "Normal", "High", "Very high"]


class TrendPoint(BaseModel):
    month: str
    activity: int = Field(ge=0)


class CalendarCell(BaseModel):
    year: int
    month: int = Field(ge=1, le=12)
    activity: int = Field(ge=0)


class Hotspot(BaseModel):
    id: str
    latitude: float = Field(ge=-90, le=90)
    longitude: float = Field(ge=-180, le=180)
    sensor: Literal["MODIS", "VIIRS", "HARMONIZED"]
    frp_mw: float = Field(ge=0)
    acquisition_date: str
    acquisition_time: str
    confidence: str
    daynight: Literal["D", "N"]


class YearlyActivity(BaseModel):
    year: int
    modis: int = Field(ge=0)
    viirs: int = Field(ge=0)
    harmonized: int = Field(ge=0)


class FireAlert(BaseModel):
    level: Literal["High", "Moderate"]
    title: str
    detail: str


class DashboardResponse(BaseModel):
    selected_area: str
    selected_period: str
    mode: Literal["modis", "viirs", "harmonized"]
    time_view: Literal["week", "year"]
    summary: Summary
    trend: list[TrendPoint]
    calendar: list[CalendarCell]
    hotspots: list[Hotspot]
    yearly_activity: list[YearlyActivity]
    alerts: list[FireAlert]
    method_version: str
