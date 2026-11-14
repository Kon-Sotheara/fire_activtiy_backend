"""PostgreSQL connection and table definition for the existing hotspots table."""

from functools import lru_cache
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict
from sqlalchemy import BIGINT, DATE, FLOAT, INTEGER, TIME, VARCHAR, Column, DateTime, MetaData, Table, create_engine
from sqlalchemy.engine import Engine


class Settings(BaseSettings):
    database_url: str | None = None

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


@lru_cache
def get_engine() -> Engine:
    """Return a pooled database engine only when DATABASE_URL is configured."""
    if not settings.database_url:
        raise RuntimeError(
            "DATABASE_URL is not configured. Create backend/.env from backend/.env.example first."
        )
    return create_engine(settings.database_url, pool_pre_ping=True)
