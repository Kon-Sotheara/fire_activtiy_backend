"""PostgreSQL connection and table definition for the existing hotspots table."""

from functools import lru_cache
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict
from sqlalchemy import BIGINT, DATE, FLOAT, INTEGER, TIME, VARCHAR, Column, DateTime, MetaData, Table, create_engine, text
from sqlalchemy.engine import Engine


class Settings(BaseSettings):
    database_url: str | None = None
    frontend_origin: str = "http://localhost:5173"

    model_config = SettingsConfigDict(
        env_file=Path(__file__).resolve().parents[1] / ".env",
        extra="ignore",
    )


settings = Settings()
metadata = MetaData()

# This matches the table supplied by the team. It does not create or alter it.
hotspots = Table(
    "hotspots",
    metadata,
    Column("id", BIGINT, primary_key=True),
    Column("latitude", FLOAT, nullable=False),
    Column("longitude", FLOAT, nullable=False),
    Column("acq_date", DATE, nullable=False),
    Column("acq_time", TIME),
    Column("acq_datetime", DateTime(timezone=True)),
    Column("sensor", VARCHAR(20), nullable=False),
    Column("satellite", VARCHAR(30)),
    Column("instrument", VARCHAR(30)),
    Column("brightness", FLOAT),
    Column("bright_t31", FLOAT),
    Column("frp", FLOAT),
    Column("scan", FLOAT),
    Column("track", FLOAT),
    Column("confidence", VARCHAR(20)),
    Column("daynight", VARCHAR(1)),
    Column("fire_type", INTEGER),
    Column("version", VARCHAR(30)),
)

# Derived records: one event represents nearby detections on the same day.
fire_events = Table(
    "fire_events",
    metadata,
    Column("id", BIGINT, primary_key=True, autoincrement=True),
    Column("event_id", VARCHAR(40)),
    Column("acq_date", DATE, nullable=False),
    Column("latitude", FLOAT, nullable=False),
    Column("longitude", FLOAT, nullable=False),
    Column("grid_id", VARCHAR(30)),
    Column("week", DATE),
    Column("detection_count", INTEGER, nullable=False),
    Column("modis_detections", INTEGER, nullable=False, default=0),
    Column("viirs_detections", INTEGER, nullable=False, default=0),
    Column("source_sensors", VARCHAR(20), nullable=False),
    Column("fire_type", INTEGER),
    Column("total_frp", FLOAT),
    Column("confidence_score", FLOAT),
    Column("daynight", VARCHAR(5)),
    Column("sensor_agreement", VARCHAR(20), nullable=False),
)

weekly_grid = Table(
    "weekly_grid",
    metadata,
    Column("id", BIGINT, primary_key=True, autoincrement=True),
    Column("week", DATE, nullable=False),
    Column("grid_id", VARCHAR(30), nullable=False),
    Column("modis_count", INTEGER, nullable=False, default=0),
    Column("modis_frp_sum", FLOAT, nullable=False, default=0),
    Column("viirs_count", INTEGER, nullable=False, default=0),
    Column("viirs_frp_sum", FLOAT, nullable=False, default=0),
    Column("unique_fire_events", INTEGER, nullable=False, default=0),
)


@lru_cache
def get_engine() -> Engine:
    """Return a pooled database engine only when DATABASE_URL is configured."""
    if not settings.database_url:
        raise RuntimeError(
            "DATABASE_URL is not configured. Create backend/.env from backend/.env.example first."
        )
    database_url = settings.database_url
    if database_url.startswith("postgresql://"):
        database_url = database_url.replace("postgresql://", "postgresql+psycopg://", 1)
    return create_engine(database_url, pool_pre_ping=True)


def ensure_analytics_indexes() -> None:
    """Create the indexes used by the public dashboard, once per database."""
    # The source data is read-heavy: the explorer repeatedly filters hundreds
    # of thousands of records by date, sensor, time of observation, and map box.
    # IF NOT EXISTS makes this safe to run whenever the service starts.
    statements = (
        "create index if not exists hotspots_acq_date_idx on hotspots (acq_date)",
        "create index if not exists hotspots_acq_date_sensor_idx on hotspots (acq_date, sensor)",
        "create index if not exists hotspots_acq_date_daynight_idx on hotspots (acq_date, daynight)",
        "create index if not exists hotspots_acq_date_location_idx on hotspots (acq_date, latitude, longitude)",
        "create index if not exists fire_events_acq_date_idx on fire_events (acq_date)",
        "create index if not exists fire_events_acq_date_location_idx on fire_events (acq_date, latitude, longitude)",
        "create index if not exists weekly_grid_week_idx on weekly_grid (week)",
    )
    with get_engine().begin() as connection:
        has_hotspots = connection.scalar(text("select to_regclass('public.hotspots') is not null"))
        has_events = connection.scalar(text("select to_regclass('public.fire_events') is not null"))
        has_weekly_grid = connection.scalar(text("select to_regclass('public.weekly_grid') is not null"))
        for statement in statements:
            if statement.startswith("create index if not exists hotspots") and not has_hotspots:
                continue
            if statement.startswith("create index if not exists fire_events") and not has_events:
                continue
            if statement.startswith("create index if not exists weekly_grid") and not has_weekly_grid:
                continue
            connection.execute(text(statement))
