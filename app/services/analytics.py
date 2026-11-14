"""Database-backed filtering, aggregation, and export helpers."""

from __future__ import annotations

import csv
from dataclasses import dataclass
from datetime import date
from io import StringIO
from statistics import median
from typing import Iterator, Literal

from sqlalchemy import func, select
from sqlalchemy.sql.elements import ColumnElement

from app.database import fire_events, get_engine, hotspots
from app.schemas.analytics import ActivityPoint, ActivitySummary, CalendarCell, FireEventPage, FireEventRecord, FrpGridCell, FrpPoint, HotspotPage, HotspotRecord, SensorComparison

SensorView = Literal["modis", "viirs", "harmonized"]
Granularity = Literal["day", "month", "year"]
CalendarGranularity = Literal["week", "month"]
FrpGranularity = Literal["week", "month", "year"]


@dataclass(frozen=True)
class HotspotFilters:
    start_date: date | None = None
    end_date: date | None = None
    years: tuple[int, ...] = ()
    months: tuple[int, ...] = ()
    sensor: SensorView = "harmonized"
    confidence: tuple[str, ...] = ()
    fire_type: int | None = None
    daynight: str | None = None
    min_lat: float | None = None
    min_lng: float | None = None
    max_lat: float | None = None
    max_lng: float | None = None


def clauses(filters: HotspotFilters, include_dates: bool = True) -> list[ColumnElement[bool]]:
    conditions: list[ColumnElement[bool]] = []
    if include_dates:
        if filters.start_date:
            conditions.append(hotspots.c.acq_date >= filters.start_date)
        if filters.end_date:
            conditions.append(hotspots.c.acq_date <= filters.end_date)
    if filters.years:
        conditions.append(func.extract("year", hotspots.c.acq_date).in_(filters.years))
    if filters.months:
        conditions.append(func.extract("month", hotspots.c.acq_date).in_(filters.months))
    if filters.sensor != "harmonized":
        conditions.append(hotspots.c.sensor == filters.sensor.upper())
    if filters.confidence:
        conditions.append(hotspots.c.confidence.in_(filters.confidence))
    if filters.fire_type is not None:
        conditions.append(hotspots.c.fire_type == filters.fire_type)
    if filters.daynight is not None:
        conditions.append(hotspots.c.daynight == filters.daynight)
    if filters.min_lat is not None:
        conditions.extend((
            hotspots.c.latitude >= filters.min_lat,
            hotspots.c.latitude <= filters.max_lat,
            hotspots.c.longitude >= filters.min_lng,
            hotspots.c.longitude <= filters.max_lng,
        ))
    return conditions


def event_clauses(filters: HotspotFilters) -> list[ColumnElement[bool]]:
    """Apply compatible dashboard filters to derived fire-event records."""
    conditions: list[ColumnElement[bool]] = []
    if filters.start_date:
        conditions.append(fire_events.c.acq_date >= filters.start_date)
    if filters.end_date:
        conditions.append(fire_events.c.acq_date <= filters.end_date)
    if filters.years:
        conditions.append(func.extract("year", fire_events.c.acq_date).in_(filters.years))
    if filters.months:
        conditions.append(func.extract("month", fire_events.c.acq_date).in_(filters.months))
    if filters.sensor == "modis":
        conditions.append(fire_events.c.modis_detections > 0)
    if filters.sensor == "viirs":
        conditions.append(fire_events.c.viirs_detections > 0)
    if filters.fire_type is not None:
        conditions.append(fire_events.c.fire_type == filters.fire_type)
    if filters.daynight is not None:
        conditions.append(fire_events.c.daynight == filters.daynight)
    if filters.min_lat is not None:
        conditions.extend((
            fire_events.c.latitude >= filters.min_lat,
            fire_events.c.latitude <= filters.max_lat,
            fire_events.c.longitude >= filters.min_lng,
            fire_events.c.longitude <= filters.max_lng,
        ))
    return conditions


def get_hotspots(filters: HotspotFilters, limit: int, offset: int) -> HotspotPage:
    conditions = clauses(filters)
    engine = get_engine()
    with engine.connect() as connection:
        total = int(connection.scalar(select(func.count()).select_from(hotspots).where(*conditions)) or 0)
        rows = connection.execute(
            select(
                hotspots.c.id, hotspots.c.latitude, hotspots.c.longitude, hotspots.c.acq_date, hotspots.c.acq_time,
                hotspots.c.sensor, hotspots.c.satellite, hotspots.c.instrument, hotspots.c.brightness, hotspots.c.bright_t31,
                hotspots.c.frp, hotspots.c.confidence, hotspots.c.daynight, hotspots.c.fire_type,
            )
            .where(*conditions)
            .order_by(hotspots.c.acq_datetime.desc().nullslast(), hotspots.c.id.desc())
            .limit(limit)
            .offset(offset)
        ).mappings().all()
    return HotspotPage(
        total=total,
        limit=limit,
        offset=offset,
        items=[
            HotspotRecord(
                id=str(row["id"]), latitude=float(row["latitude"]), longitude=float(row["longitude"]),
                acq_date=row["acq_date"], acq_time=row["acq_time"].strftime("%H:%M UTC") if row["acq_time"] else None,
                sensor=str(row["sensor"]).upper(), satellite=row["satellite"], instrument=row["instrument"],
                brightness=row["brightness"], bright_t31=row["bright_t31"], frp=row["frp"], confidence=row["confidence"],
                daynight=row["daynight"], fire_type=row["fire_type"],
            )
            for row in rows
        ],
    )


def get_fire_events(filters: HotspotFilters, limit: int, offset: int) -> FireEventPage:
    conditions = event_clauses(filters)
    with get_engine().connect() as connection:
        total = int(connection.scalar(select(func.count()).select_from(fire_events).where(*conditions)) or 0)
        rows = connection.execute(
            select(
                fire_events.c.id, fire_events.c.latitude, fire_events.c.longitude, fire_events.c.acq_date,
                fire_events.c.detection_count, fire_events.c.modis_detections, fire_events.c.viirs_detections,
                fire_events.c.sensor_agreement, fire_events.c.total_frp, fire_events.c.confidence_score,
                fire_events.c.daynight, fire_events.c.fire_type,
            )
            .where(*conditions)
            .order_by(fire_events.c.acq_date.desc(), fire_events.c.id.desc())
            .limit(limit)
            .offset(offset)
        ).mappings().all()
    return FireEventPage(
        total=total,
        limit=limit,
        offset=offset,
        items=[
            FireEventRecord(
                id=str(row["id"]), latitude=float(row["latitude"]), longitude=float(row["longitude"]),
                acq_date=row["acq_date"], detection_count=int(row["detection_count"]),
                modis_detections=int(row["modis_detections"]), viirs_detections=int(row["viirs_detections"]),
                sensor_agreement={"Both sensors": "both", "MODIS only": "modis_only", "VIIRS only": "viirs_only"}.get(str(row["sensor_agreement"]), str(row["sensor_agreement"])), total_frp=float(row["total_frp"]) if row["total_frp"] is not None else None,
                confidence_score=float(row["confidence_score"]) if row["confidence_score"] is not None else None,
                daynight=row["daynight"], fire_type=row["fire_type"],
            )
            for row in rows
        ],
    )


def get_summary(filters: HotspotFilters) -> ActivitySummary:
    engine = get_engine()
    selected_conditions = event_clauses(filters)
    historical_filters = HotspotFilters(
        years=filters.years,
        months=filters.months,
        sensor=filters.sensor,
        fire_type=filters.fire_type,
        daynight=filters.daynight,
        min_lat=filters.min_lat,
        min_lng=filters.min_lng,
        max_lat=filters.max_lat,
        max_lng=filters.max_lng,
    )
    historical_conditions = event_clauses(historical_filters)
    with engine.connect() as connection:
        selected_total = int(connection.scalar(select(func.count()).select_from(fire_events).where(*selected_conditions)) or 0)
        annual_rows = connection.execute(
            select(func.extract("year", fire_events.c.acq_date).label("year"), func.count().label("activity"))
            .where(*historical_conditions)
            .group_by(func.extract("year", fire_events.c.acq_date))
        ).all()
    annual_counts = [int(row.activity) for row in annual_rows]
    historical_median = int(median(annual_counts)) if annual_counts else 0
    anomaly_percent = round((selected_total - historical_median) / historical_median * 100, 1) if historical_median else 0.0
    anomaly_label = "Low" if anomaly_percent < -25 else "High" if anomaly_percent > 25 else "Normal"
    return ActivitySummary(
        total_detections=selected_total,
        historical_annual_median=historical_median,
        anomaly_percent=anomaly_percent,
        anomaly_label=anomaly_label,
        selected_start_date=filters.start_date,
        selected_end_date=filters.end_date,
        applied_sensor_view=filters.sensor,
    )


def get_raw_summary(filters: HotspotFilters) -> ActivitySummary:
    """Summarize raw MODIS/VIIRS observations, including their day/night values."""
    selected_conditions = clauses(filters)
    historical_filters = HotspotFilters(
        years=filters.years,
        months=filters.months,
        sensor=filters.sensor,
        confidence=filters.confidence,
        fire_type=filters.fire_type,
        daynight=filters.daynight,
        min_lat=filters.min_lat,
        min_lng=filters.min_lng,
        max_lat=filters.max_lat,
        max_lng=filters.max_lng,
    )
    historical_conditions = clauses(historical_filters)
    with get_engine().connect() as connection:
        selected_total = int(connection.scalar(select(func.count()).select_from(hotspots).where(*selected_conditions)) or 0)
        annual_rows = connection.execute(
            select(func.extract("year", hotspots.c.acq_date).label("year"), func.count().label("activity"))
            .where(*historical_conditions)
            .group_by(func.extract("year", hotspots.c.acq_date))
        ).all()
    annual_counts = [int(row.activity) for row in annual_rows]
    historical_median = int(median(annual_counts)) if annual_counts else 0
    anomaly_percent = round((selected_total - historical_median) / historical_median * 100, 1) if historical_median else 0.0
    anomaly_label = "Low" if anomaly_percent < -25 else "High" if anomaly_percent > 25 else "Normal"
    return ActivitySummary(
        total_detections=selected_total,
        historical_annual_median=historical_median,
        anomaly_percent=anomaly_percent,
        anomaly_label=anomaly_label,
        selected_start_date=filters.start_date,
        selected_end_date=filters.end_date,
        applied_sensor_view=filters.sensor,
    )


def get_trend(filters: HotspotFilters, granularity: Granularity) -> list[ActivityPoint]:
    period = func.date_trunc(granularity, hotspots.c.acq_date).label("period")
    with get_engine().connect() as connection:
        rows = connection.execute(
            select(period, func.count().label("activity"))
            .where(*clauses(filters))
            .group_by(period)
            .order_by(period)
        ).all()
    format_string = {"day": "%Y-%m-%d", "month": "%Y-%m", "year": "%Y"}[granularity]
    return [ActivityPoint(period=row.period.strftime(format_string), activity=int(row.activity)) for row in rows]


def get_event_trend(filters: HotspotFilters, granularity: Granularity) -> list[ActivityPoint]:
    """Count harmonized fire events rather than raw satellite detections."""
    period = func.date_trunc(granularity, fire_events.c.acq_date).label("period")
    with get_engine().connect() as connection:
        rows = connection.execute(select(period, func.count().label("activity")).where(*event_clauses(filters)).group_by(period).order_by(period)).all()
    format_string = {"day": "%Y-%m-%d", "month": "%Y-%m", "year": "%Y"}[granularity]
    return [ActivityPoint(period=row.period.strftime(format_string), activity=int(row.activity)) for row in rows]


def get_calendar(filters: HotspotFilters, granularity: CalendarGranularity) -> list[CalendarCell]:
    period = func.extract(granularity, hotspots.c.acq_date).label("period")
    year = func.extract("year", hotspots.c.acq_date).label("year")
    with get_engine().connect() as connection:
        rows = connection.execute(
            select(year, period, func.count().label("activity"))
            .where(*clauses(filters))
            .group_by(year, period)
            .order_by(year, period)
        ).all()
    return [CalendarCell(year=int(row.year), period=int(row.period), activity=int(row.activity)) for row in rows]


def get_event_calendar(filters: HotspotFilters, granularity: CalendarGranularity) -> list[CalendarCell]:
    """Build a calendar of unique clustered fire events."""
    period = func.extract(granularity, fire_events.c.acq_date).label("period")
    year = func.extract("year", fire_events.c.acq_date).label("year")
    with get_engine().connect() as connection:
        rows = connection.execute(select(year, period, func.count().label("activity")).where(*event_clauses(filters)).group_by(year, period).order_by(year, period)).all()
    return [CalendarCell(year=int(row.year), period=int(row.period), activity=int(row.activity)) for row in rows]


def get_sensor_comparison(filters: HotspotFilters) -> SensorComparison:
    # Ignore the sensor-view filter here: this endpoint deliberately compares both sources.
    comparison_filters = HotspotFilters(
        start_date=filters.start_date, end_date=filters.end_date, confidence=filters.confidence, fire_type=filters.fire_type, daynight=filters.daynight,
        min_lat=filters.min_lat, min_lng=filters.min_lng, max_lat=filters.max_lat, max_lng=filters.max_lng,
    )
    with get_engine().connect() as connection:
        rows = connection.execute(
            select(hotspots.c.sensor, func.count().label("activity"))
            .where(*clauses(comparison_filters))
            .group_by(hotspots.c.sensor)
        ).all()
    values = {str(row.sensor).upper(): int(row.activity) for row in rows}
    modis, viirs = values.get("MODIS", 0), values.get("VIIRS", 0)
    return SensorComparison(modis=modis, viirs=viirs, combined=modis + viirs, viirs_to_modis_ratio=round(viirs / modis, 2) if modis else None)


def get_frp_trend(filters: HotspotFilters, granularity: FrpGranularity) -> list[FrpPoint]:
    period = func.date_trunc(granularity, fire_events.c.acq_date).label("period")
    with get_engine().connect() as connection:
        rows = connection.execute(
            select(
                period,
                func.coalesce(func.sum(fire_events.c.total_frp), 0.0).label("total_frp"),
                func.count().label("event_count"),
            )
            .where(*event_clauses(filters))
            .group_by(period)
            .order_by(period)
        ).all()
    format_string = {"week": "%Y-%m-%d", "month": "%Y-%m", "year": "%Y"}[granularity]
    return [FrpPoint(period=row.period.strftime(format_string), total_frp=float(row.total_frp), event_count=int(row.event_count)) for row in rows]


def get_weekly_frp_grid(filters: HotspotFilters, grid_size: float, limit: int) -> list[FrpGridCell]:
    week = func.date_trunc("week", fire_events.c.acq_date).label("week")
    latitude = (func.floor(fire_events.c.latitude / grid_size) * grid_size).label("latitude")
    longitude = (func.floor(fire_events.c.longitude / grid_size) * grid_size).label("longitude")
    with get_engine().connect() as connection:
        rows = connection.execute(
            select(
                week,
                latitude,
                longitude,
                func.coalesce(func.sum(fire_events.c.total_frp), 0.0).label("total_frp"),
                func.count().label("event_count"),
            )
            .where(*event_clauses(filters))
            .group_by(week, latitude, longitude)
            .order_by(week.desc(), func.sum(fire_events.c.total_frp).desc())
            .limit(limit)
        ).all()
    return [
        FrpGridCell(
            week=row.week.strftime("%Y-%m-%d"),
            latitude=float(row.latitude),
            longitude=float(row.longitude),
            total_frp=float(row.total_frp),
            event_count=int(row.event_count),
        )
        for row in rows
    ]


CSV_COLUMNS = [
    "id", "latitude", "longitude", "acq_date", "acq_time", "sensor", "satellite", "instrument", "brightness",
    "bright_t31", "frp", "confidence", "daynight", "fire_type",
]


def hotspot_csv_rows(filters: HotspotFilters, limit: int) -> Iterator[str]:
    """Stream a bounded CSV response so exports do not load all records into memory."""
    output = StringIO()
    writer = csv.writer(output)
    writer.writerow(CSV_COLUMNS)
    yield output.getvalue()
    output.seek(0)
    output.truncate(0)

    statement = (
        select(*(getattr(hotspots.c, column) for column in CSV_COLUMNS))
        .where(*clauses(filters))
        .order_by(hotspots.c.acq_datetime.desc().nullslast(), hotspots.c.id.desc())
        .limit(limit)
    )
    with get_engine().connect() as connection:
        result = connection.execution_options(stream_results=True).execute(statement)
        for row in result:
            writer.writerow(row)
            yield output.getvalue()
            output.seek(0)
            output.truncate(0)


def statistics_csv(filters: HotspotFilters) -> str:
    summary = get_summary(filters)
    comparison = get_sensor_comparison(filters)
    output = StringIO()
    writer = csv.writer(output)
    writer.writerow(["metric", "value"])
    writer.writerows([
        ["total_detections", summary.total_detections],
        ["historical_annual_median", summary.historical_annual_median],
        ["anomaly_percent", summary.anomaly_percent],
        ["anomaly_label", summary.anomaly_label],
        ["modis", comparison.modis],
        ["viirs", comparison.viirs],
        ["combined", comparison.combined],
    ])
    return output.getvalue()
