from __future__ import annotations

from collections import defaultdict
from datetime import date
from statistics import median

from sqlalchemy import case, func, select

from app.database import get_engine, hotspots, settings
from app.schemas.dashboard import CalendarCell, DashboardResponse, FireAlert, Hotspot, Summary, TrendPoint, YearlyActivity


def get_dashboard(area: str, start_year: int, mode: str, time_view: str) -> DashboardResponse:
    """Use PostgreSQL when configured; retain mock data for unconfigured local UI work."""
    if settings.database_url:
        return _get_database_dashboard(area=area, start_year=start_year, mode=mode, time_view=time_view)
    return _get_mock_dashboard(area=area, start_year=start_year, mode=mode, time_view=time_view)


def _mode_filter(mode: str):
    return hotspots.c.sensor == mode.upper() if mode != "harmonized" else None


def _get_database_dashboard(area: str, start_year: int, mode: str, time_view: str) -> DashboardResponse:
    """Build dashboard aggregates from the raw hotspots table.

    The current schema stores clean source records. ``harmonized`` therefore means the
    combined MODIS + VIIRS view until a dedicated harmonized-records table is added.
    """
    sensor_filter = _mode_filter(mode)
    selected_dates = (hotspots.c.acq_date >= date(start_year, 1, 1), hotspots.c.acq_date <= date(start_year, 12, 31))
    selected_conditions = [*selected_dates, *([sensor_filter] if sensor_filter is not None else [])]
    all_conditions = [sensor_filter] if sensor_filter is not None else []
    engine = get_engine()

    with engine.connect() as connection:
        activity = int(connection.scalar(select(func.count()).select_from(hotspots).where(*selected_conditions)) or 0)

        trend_rows = connection.execute(
            select(func.extract("month", hotspots.c.acq_date).label("month"), func.count().label("activity"))
            .where(*selected_conditions)
            .group_by(func.extract("month", hotspots.c.acq_date))
            .order_by(func.extract("month", hotspots.c.acq_date))
        ).all()
        trend_by_month = {int(row.month): int(row.activity) for row in trend_rows}

        calendar_rows = connection.execute(
            select(
                func.extract("year", hotspots.c.acq_date).label("year"),
                func.extract("month", hotspots.c.acq_date).label("month"),
                func.count().label("activity"),
            )
            .where(*all_conditions)
            .group_by(func.extract("year", hotspots.c.acq_date), func.extract("month", hotspots.c.acq_date))
            .order_by(func.extract("year", hotspots.c.acq_date), func.extract("month", hotspots.c.acq_date))
        ).all()

        yearly_rows = connection.execute(
            select(
                func.extract("year", hotspots.c.acq_date).label("year"),
                hotspots.c.sensor,
                func.count().label("activity"),
            )
            .group_by(func.extract("year", hotspots.c.acq_date), hotspots.c.sensor)
            .order_by(func.extract("year", hotspots.c.acq_date))
        ).all()

        point_rows = connection.execute(
            select(
                hotspots.c.id, hotspots.c.latitude, hotspots.c.longitude, hotspots.c.sensor, hotspots.c.frp,
                hotspots.c.acq_date, hotspots.c.acq_time, hotspots.c.confidence, hotspots.c.daynight,
            )
            .where(*selected_conditions)
            .order_by(hotspots.c.acq_datetime.desc().nullslast(), hotspots.c.id.desc())
            .limit(500)
        ).mappings().all()

    yearly: dict[int, dict[str, int]] = defaultdict(lambda: {"MODIS": 0, "VIIRS": 0})
    for row in yearly_rows:
        yearly[int(row.year)][str(row.sensor).upper()] = int(row.activity)
    yearly_activity = [
        YearlyActivity(year=year, modis=counts["MODIS"], viirs=counts["VIIRS"], harmonized=counts["MODIS"] + counts["VIIRS"])
        for year, counts in sorted(yearly.items())
    ]
    historical_values = [
        item.harmonized if mode == "harmonized" else getattr(item, mode)
        for item in yearly_activity
        if item.year != start_year
    ]
    historical_median = int(median(historical_values)) if historical_values else 0
    anomaly_percent = round(((activity - historical_median) / historical_median) * 100, 1) if historical_median else 0.0
    anomaly_label = "Low" if anomaly_percent < -25 else "High" if anomaly_percent > 25 else "Normal"

    month_labels = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]
    return DashboardResponse(
        selected_area=area,
        selected_period=str(start_year),
        mode=mode,
        time_view=time_view,
        summary=Summary(
            harmonized_activity=activity,
            historical_median=historical_median,
            anomaly_percent=anomaly_percent,
            anomaly_label=anomaly_label,
        ),
        trend=[TrendPoint(month=label, activity=trend_by_month.get(index, 0)) for index, label in enumerate(month_labels, start=1)],
        calendar=[CalendarCell(year=int(row.year), month=int(row.month), activity=int(row.activity)) for row in calendar_rows],
        hotspots=[
            Hotspot(
                id=str(row["id"]), latitude=float(row["latitude"]), longitude=float(row["longitude"]), sensor=str(row["sensor"]).upper(),
                frp_mw=float(row["frp"] or 0), acquisition_date=row["acq_date"].isoformat(),
                acquisition_time=row["acq_time"].strftime("%H:%M UTC") if row["acq_time"] else "Unknown",
                confidence=str(row["confidence"] or "unknown"), daynight=str(row["daynight"] or "D"),
            )
            for row in point_rows
        ],
        yearly_activity=yearly_activity,
        alerts=[
            FireAlert(level="High" if anomaly_label == "High" else "Moderate", title=f"{anomaly_label} activity", detail=f"{anomaly_percent:+.1f}% compared with the historical median."),
            FireAlert(level="Moderate", title=f"{activity:,} records in {start_year}", detail="Filtered by the currently selected sensor view."),
        ],
        method_version="raw-firms-v1 (harmonized = combined source view)",
    )


def _get_mock_dashboard(area: str, start_year: int, mode: str, time_view: str) -> DashboardResponse:
    multiplier = {"modis": 0.51, "viirs": 0.9, "harmonized": 1}[mode]
    activity = round(842 * multiplier)
    anomaly_percent = round(((activity - 518) / 518) * 100, 1)
    anomaly_label = "Low" if anomaly_percent < -25 else "High" if anomaly_percent > 25 else "Normal"

    return DashboardResponse(
        selected_area=area,
        selected_period=f"January - November {start_year}",
        mode=mode,
        time_view=time_view,
        summary=Summary(
            harmonized_activity=activity,
            historical_median=518,
            anomaly_percent=anomaly_percent,
            anomaly_label=anomaly_label,
        ),
        trend=[TrendPoint(month=month, activity=value) for month, value in zip(
            ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"],
            [42, 57, 83, 121, 106, 74, 49, 38, 46, 71, 98, 63],
            strict=True,
        )],
        calendar=[
            CalendarCell(year=2024, month=1, activity=44), CalendarCell(year=2024, month=2, activity=68),
            CalendarCell(year=2025, month=1, activity=53), CalendarCell(year=2025, month=2, activity=72),
            CalendarCell(year=start_year, month=1, activity=42), CalendarCell(year=start_year, month=2, activity=57),
        ],
        hotspots=[
            Hotspot(id="mock-1", latitude=13.04175, longitude=106.4558, sensor="VIIRS", frp_mw=3.5, acquisition_date="2025-01-01", acquisition_time="05:57 UTC", confidence="nominal", daynight="D"),
            Hotspot(id="mock-2", latitude=13.1, longitude=105.4, sensor="VIIRS", frp_mw=6.3, acquisition_date="2025-03-17", acquisition_time="06:04 UTC", confidence="high", daynight="D"),
            Hotspot(id="mock-3", latitude=11.9, longitude=104.2, sensor="MODIS", frp_mw=12.1, acquisition_date="2025-05-09", acquisition_time="18:22 UTC", confidence="78", daynight="N"),
        ],
        yearly_activity=[
            YearlyActivity(year=2019, modis=820, viirs=0, harmonized=820),
            YearlyActivity(year=2020, modis=790, viirs=270, harmonized=860),
            YearlyActivity(year=2021, modis=660, viirs=420, harmonized=790),
            YearlyActivity(year=2022, modis=710, viirs=510, harmonized=910),
            YearlyActivity(year=2023, modis=610, viirs=570, harmonized=880),
            YearlyActivity(year=2024, modis=520, viirs=640, harmonized=900),
            YearlyActivity(year=2025, modis=490, viirs=700, harmonized=920),
            YearlyActivity(year=start_year, modis=430, viirs=760, harmonized=activity),
        ],
        alerts=[
            FireAlert(level="High", title="Above normal activity", detail="This week is 62.5% above the historical median."),
            FireAlert(level="Moderate", title="Three hotspots in selected area", detail="Two VIIRS and one MODIS detection need review."),
        ],
        method_version="mock-harmonization-v1",
    )
