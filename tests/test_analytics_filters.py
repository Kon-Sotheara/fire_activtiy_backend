from sqlalchemy import select

from app.database import hotspots
from app.services.analytics import HotspotFilters, clauses


def test_fire_type_filter_is_included_in_query() -> None:
    statement = select(hotspots.c.id).where(*clauses(HotspotFilters(fire_type=0)))

    assert "hotspots.fire_type =" in str(statement)


def test_daynight_filter_is_included_in_query() -> None:
    statement = select(hotspots.c.id).where(*clauses(HotspotFilters(daynight="N")))

    assert "hotspots.daynight =" in str(statement)


def test_year_filter_is_included_in_query() -> None:
    statement = select(hotspots.c.id).where(*clauses(HotspotFilters(years=(2023, 2024))))

    assert "EXTRACT(year FROM hotspots.acq_date)" in str(statement)


def test_month_filter_is_included_in_query() -> None:
    statement = select(hotspots.c.id).where(*clauses(HotspotFilters(months=(3,))))

    assert "EXTRACT(month FROM hotspots.acq_date)" in str(statement)
