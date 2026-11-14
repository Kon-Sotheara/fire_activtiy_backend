from datetime import date
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import StreamingResponse

from app.schemas.analytics import ActivityPoint, ActivitySummary, CalendarCell, FireEventPage, FrpGridCell, FrpPoint, HotspotPage, SensorComparison
from app.services.analytics import HotspotFilters, get_calendar, get_event_calendar, get_event_trend, get_fire_events, get_hotspots, get_frp_trend, get_raw_summary, get_sensor_comparison, get_summary, get_trend, get_weekly_frp_grid, hotspot_csv_rows, statistics_csv

router = APIRouter(tags=["fire activity"])


def query_filters(
    start_date: date | None = Query(default=None),
    end_date: date | None = Query(default=None),
    years: str | None = Query(default=None, description="Comma-separated years, for example 2023,2024."),
    months: str | None = Query(default=None, description="Comma-separated month numbers, for example 3 or 3,4."),
    sensor: Literal["modis", "viirs", "harmonized"] = Query(default="harmonized"),
    confidence: str | None = Query(default=None, description="Comma-separated raw FIRMS confidence values, e.g. h,n or 30,50,80."),
    fire_type: int | None = Query(default=None, ge=0, le=3, description="FIRMS detection type: 0=vegetation fire, 1=active volcano, 2=static land source, 3=offshore."),
    daynight: Literal["D", "N", "M"] | None = Query(default=None, description="Observation period: D=day, N=night, M=mixed day and night event."),
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
    try:
        selected_years = tuple(sorted({int(value.strip()) for value in years.split(",") if value.strip()})) if years else ()
    except ValueError as error:
        raise HTTPException(status_code=422, detail="years must be comma-separated integers") from error
    if any(year < 2000 or year > 2100 for year in selected_years):
        raise HTTPException(status_code=422, detail="years must be between 2000 and 2100")
    try:
        selected_months = tuple(sorted({int(value.strip()) for value in months.split(",") if value.strip()})) if months else ()
    except ValueError as error:
        raise HTTPException(status_code=422, detail="months must be comma-separated integers") from error
    if any(month < 1 or month > 12 for month in selected_months):
        raise HTTPException(status_code=422, detail="months must be between 1 and 12")
    return HotspotFilters(
        start_date=start_date,
        end_date=end_date,
        years=selected_years,
        months=selected_months,
        sensor=sensor,
        confidence=confidence_values,
        fire_type=fire_type,
        daynight=daynight,
        min_lat=min_lat,
        min_lng=min_lng,
        max_lat=max_lat,
        max_lng=max_lng,
    )


Filters = Annotated[HotspotFilters, Depends(query_filters)]


@router.get("/hotspots", response_model=HotspotPage)
def hotspots_endpoint(filters: Filters, limit: int = Query(default=500, ge=1, le=2_000), offset: int = Query(default=0, ge=0)) -> HotspotPage:
    """Return bounded map records and the total count matching the current filters."""
    return get_hotspots(filters, limit, offset)


@router.get("/events", response_model=FireEventPage)
def events_endpoint(filters: Filters, limit: int = Query(default=500, ge=1, le=2_000), offset: int = Query(default=0, ge=0)) -> FireEventPage:
    """Return harmonized fire events for map rendering."""
    return get_fire_events(filters, limit, offset)


@router.get("/analytics/summary", response_model=ActivitySummary)
def summary_endpoint(filters: Filters, source: Literal["events", "raw"] = Query(default="events")) -> ActivitySummary:
    return get_raw_summary(filters) if source == "raw" else get_summary(filters)


@router.get("/analytics/trends", response_model=list[ActivityPoint])
def trends_endpoint(filters: Filters, granularity: Literal["day", "month", "year"] = Query(default="month")) -> list[ActivityPoint]:
    return get_trend(filters, granularity)


@router.get("/analytics/event-trends", response_model=list[ActivityPoint])
def event_trends_endpoint(filters: Filters, granularity: Literal["day", "month", "year"] = Query(default="month")) -> list[ActivityPoint]:
    return get_event_trend(filters, granularity)


@router.get("/analytics/calendar", response_model=list[CalendarCell])
def calendar_endpoint(filters: Filters, granularity: Literal["week", "month"] = Query(default="month")) -> list[CalendarCell]:
    return get_calendar(filters, granularity)


@router.get("/analytics/event-calendar", response_model=list[CalendarCell])
def event_calendar_endpoint(filters: Filters, granularity: Literal["week", "month"] = Query(default="month")) -> list[CalendarCell]:
    return get_event_calendar(filters, granularity)


@router.get("/analytics/compare-sensors", response_model=SensorComparison)
def compare_sensors_endpoint(filters: Filters) -> SensorComparison:
    return get_sensor_comparison(filters)


@router.get("/analytics/frp/trends", response_model=list[FrpPoint])
def frp_trends_endpoint(filters: Filters, granularity: Literal["week", "month", "year"] = Query(default="month")) -> list[FrpPoint]:
    """Return summed Fire Radiative Power from harmonized fire events."""
    return get_frp_trend(filters, granularity)


@router.get("/analytics/frp/grid", response_model=list[FrpGridCell])
def frp_grid_endpoint(
    filters: Filters,
    grid_size: float = Query(default=0.05, gt=0, le=1),
    limit: int = Query(default=5_000, ge=1, le=20_000),
) -> list[FrpGridCell]:
    """Return weekly fire-intensity cells for map rendering."""
    return get_weekly_frp_grid(filters, grid_size=grid_size, limit=limit)


@router.get("/exports/hotspots.csv", response_class=StreamingResponse)
def hotspots_export(filters: Filters, limit: int = Query(default=1_000_000, ge=1, le=1_000_000)) -> StreamingResponse:
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
