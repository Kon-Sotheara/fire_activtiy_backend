from datetime import date
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import StreamingResponse

from app.schemas.analytics import ActivityPoint, ActivitySummary, CalendarCell, HotspotPage, SensorComparison
from app.services.analytics import HotspotFilters, get_calendar, get_hotspots, get_sensor_comparison, get_summary, get_trend, hotspot_csv_rows, statistics_csv

router = APIRouter(tags=["fire activity"])


def query_filters(
    start_date: date | None = Query(default=None),
    end_date: date | None = Query(default=None),
    sensor: Literal["modis", "viirs", "harmonized"] = Query(default="harmonized"),
    confidence: str | None = Query(default=None, description="Comma-separated raw FIRMS confidence values, e.g. h,n or 30,50,80."),
    min_lat: float | None = Query(default=None, ge=-90, le=90),
    min_lng: float | None = Query(default=None, ge=-180, le=180),
    max_lat: float | None = Query(default=None, ge=-90, le=90),
    max_lng: float | None = Query(default=None, ge=-180, le=180),
) -> HotspotFilters:
    if start_date and end_date and start_date > end_date:
        raise HTTPException(status_code=422, detail="start_date must be on or before end_date")
    bounds = (min_lat, min_lng, max_lat, max_lng)
    if any(value is not None for value in bounds):
        if any(value is None for value in bounds):
            raise HTTPException(status_code=422, detail="Map filtering requires min_lat, min_lng, max_lat, and max_lng together")
        if min_lat > max_lat or min_lng > max_lng:
            raise HTTPException(status_code=422, detail="Minimum map bounds must not exceed maximum bounds")
    confidence_values = tuple(value.strip() for value in confidence.split(",") if value.strip()) if confidence else ()
    return HotspotFilters(start_date, end_date, sensor, confidence_values, min_lat, min_lng, max_lat, max_lng)


Filters = Annotated[HotspotFilters, Depends(query_filters)]


@router.get("/hotspots", response_model=HotspotPage)
def hotspots_endpoint(filters: Filters, limit: int = Query(default=500, ge=1, le=2_000), offset: int = Query(default=0, ge=0)) -> HotspotPage:
    """Return bounded map records and the total count matching the current filters."""
    return get_hotspots(filters, limit, offset)


@router.get("/analytics/summary", response_model=ActivitySummary)
def summary_endpoint(filters: Filters) -> ActivitySummary:
    return get_summary(filters)


@router.get("/analytics/trends", response_model=list[ActivityPoint])
def trends_endpoint(filters: Filters, granularity: Literal["day", "month", "year"] = Query(default="month")) -> list[ActivityPoint]:
    return get_trend(filters, granularity)


@router.get("/analytics/calendar", response_model=list[CalendarCell])
def calendar_endpoint(filters: Filters, granularity: Literal["week", "month"] = Query(default="month")) -> list[CalendarCell]:
    return get_calendar(filters, granularity)


@router.get("/analytics/compare-sensors", response_model=SensorComparison)
def compare_sensors_endpoint(filters: Filters) -> SensorComparison:
    return get_sensor_comparison(filters)


@router.get("/exports/hotspots.csv", response_class=StreamingResponse)
def hotspots_export(filters: Filters, limit: int = Query(default=100_000, ge=1, le=200_000)) -> StreamingResponse:
    return StreamingResponse(
        hotspot_csv_rows(filters, limit),
        media_type="text/csv",
        headers={"Content-Disposition": "attachment; filename=hotspots.csv"},
    )


@router.get("/exports/statistics.csv", response_class=StreamingResponse)
def statistics_export(filters: Filters) -> StreamingResponse:
    return StreamingResponse(
        iter([statistics_csv(filters)]),
        media_type="text/csv",
        headers={"Content-Disposition": "attachment; filename=fire-activity-statistics.csv"},
    )
