"""Seed cleaned MODIS, VIIRS, event, and weekly-grid CSVs into PostgreSQL.

Run from backend::
    .venv/Scripts/python.exe -m app.pipeline.seed_processed --dry-run
    .venv/Scripts/python.exe -m app.pipeline.seed_processed --replace
"""

from __future__ import annotations

import argparse
from datetime import date
from pathlib import Path
from typing import Any, Iterable

import pandas as pd
from psycopg import connect

from app.database import ensure_analytics_indexes, fire_events, get_engine, hotspots, metadata, settings, weekly_grid
from app.pipeline.seed_hotspots import normalize_row

PROCESSED_DIRECTORY = Path(__file__).resolve().parents[3] / "data" / "processed"
CHUNK_SIZE = 25_000


def rows(path: Path) -> Iterable[list[dict[str, Any]]]:
    for chunk in pd.read_csv(path, chunksize=CHUNK_SIZE, dtype={"acq_time": "string", "confidence": "string"}):
        yield chunk.to_dict(orient="records")


def sensor_rows(path: Path, sensor: str) -> Iterable[list[dict[str, Any]]]:
    for chunk in rows(path):
        cleaned: list[dict[str, Any]] = []
        for row in chunk:
            normalized = normalize_row(row)
            if normalized:
                normalized["sensor"] = sensor
                cleaned.append(normalized)
        if cleaned:
            yield cleaned


def event_rows(path: Path) -> Iterable[list[dict[str, Any]]]:
    for chunk in rows(path):
        cleaned = []
        for row in chunk:
            sensors = str(row["sensors"]).strip()
            count = int(row["n_detections"])
            cleaned.append({
                "event_id": str(row["event_id"]),
                "acq_date": date.fromisoformat(str(row["date"])),
                "latitude": float(row["latitude"]),
                "longitude": float(row["longitude"]),
                "grid_id": str(row["grid_id"]),
                "week": date.fromisoformat(str(row["week"])),
                "detection_count": count,
                "modis_detections": count if sensors == "MODIS" else (1 if "MODIS" in sensors else 0),
                "viirs_detections": count if sensors == "VIIRS" else (1 if "VIIRS" in sensors else 0),
                "source_sensors": sensors,
                "sensor_agreement": str(row["agreement"]),
            })
        if cleaned:
            yield cleaned


def weekly_rows(path: Path) -> Iterable[list[dict[str, Any]]]:
    for chunk in rows(path):
        cleaned = [{
            "week": date.fromisoformat(str(row["week"])),
            "grid_id": str(row["grid_id"]),
            "modis_count": int(row["MODIS"]),
            "modis_frp_sum": float(row["MODIS_frp_sum"]),
            "viirs_count": int(row["VIIRS"]),
            "viirs_frp_sum": float(row["VIIRS_frp_sum"]),
            "unique_fire_events": int(row["unique_fire_events"]),
        } for row in chunk]
        if cleaned:
            yield cleaned


def count_records(source: Iterable[list[dict[str, Any]]]) -> int:
    return sum(len(batch) for batch in source)


def copy_rows(cursor: Any, statement: str, source: Iterable[list[dict[str, Any]]], columns: list[str], label: str) -> None:
    total = 0
    with cursor.copy(statement) as copy:
        for batch in source:
            for row in batch:
                copy.write_row(tuple(row.get(column) for column in columns))
            total += len(batch)
    print(f"{label}: imported {total:,} rows")


def database_url() -> str:
    if not settings.database_url:
        raise RuntimeError("DATABASE_URL is not configured.")
    return settings.database_url.replace("postgresql+psycopg://", "postgresql://", 1)


def main() -> None:
    parser = argparse.ArgumentParser(description="Seed processed Cambodia fire datasets.")
    parser.add_argument("--dry-run", action="store_true", help="Validate and count records without writing to PostgreSQL.")
    parser.add_argument("--replace", action="store_true", help="Replace data in hotspots, fire_events, and weekly_grid.")
    args = parser.parse_args()

    paths = {
        "MODIS": PROCESSED_DIRECTORY / "modis_clean.csv",
        "VIIRS": PROCESSED_DIRECTORY / "viirs_clean.csv",
        "events": PROCESSED_DIRECTORY / "fire_events.csv",
        "weekly grid": PROCESSED_DIRECTORY / "weekly_grid.csv",
    }
    missing = [str(path) for path in paths.values() if not path.is_file()]
    if missing:
        raise FileNotFoundError("Missing processed file(s): " + ", ".join(missing))

    sources = [
        ("MODIS", lambda: sensor_rows(paths["MODIS"], "MODIS")),
        ("VIIRS", lambda: sensor_rows(paths["VIIRS"], "VIIRS")),
        ("fire events", lambda: event_rows(paths["events"])),
        ("weekly grid", lambda: weekly_rows(paths["weekly grid"])),
    ]
    if args.dry_run:
        for label, source in sources:
            print(f"{label}: {count_records(source()):,} valid rows")
        return
    if not args.replace:
        parser.error("Use --replace to confirm replacing these processed-data tables.")

    # Render starts with an empty database, so create the three application tables first.
    metadata.create_all(get_engine(), tables=[hotspots, fire_events, weekly_grid])

    with connect(database_url()) as connection, connection.cursor() as cursor:
        cursor.execute("alter table fire_events add column if not exists event_id varchar(40)")
        cursor.execute("alter table fire_events add column if not exists grid_id varchar(30)")
        cursor.execute("alter table fire_events add column if not exists week date")
        cursor.execute("create unique index if not exists fire_events_event_id_idx on fire_events (event_id)")
        cursor.execute("""
            create table if not exists weekly_grid (
                id bigint generated by default as identity primary key,
                week date not null,
                grid_id varchar(30) not null,
                modis_count integer not null default 0,
                modis_frp_sum double precision not null default 0,
                viirs_count integer not null default 0,
                viirs_frp_sum double precision not null default 0,
                unique_fire_events integer not null default 0,
                unique (week, grid_id)
            )
        """)
        cursor.execute("truncate table hotspots restart identity")
        cursor.execute("delete from fire_events")
        cursor.execute("delete from weekly_grid")

        hotspot_columns = ["latitude", "longitude", "acq_date", "acq_time", "acq_datetime", "sensor", "satellite", "instrument", "brightness", "bright_t31", "frp", "scan", "track", "confidence", "daynight", "fire_type", "version"]
        copy_rows(cursor, f"copy hotspots ({', '.join(hotspot_columns)}) from stdin", sensor_rows(paths["MODIS"], "MODIS"), hotspot_columns, "MODIS")
        copy_rows(cursor, f"copy hotspots ({', '.join(hotspot_columns)}) from stdin", sensor_rows(paths["VIIRS"], "VIIRS"), hotspot_columns, "VIIRS")

        event_columns = ["event_id", "acq_date", "latitude", "longitude", "grid_id", "week", "detection_count", "modis_detections", "viirs_detections", "source_sensors", "sensor_agreement"]
        copy_rows(cursor, f"copy fire_events ({', '.join(event_columns)}) from stdin", event_rows(paths["events"]), event_columns, "fire events")

        weekly_columns = ["week", "grid_id", "modis_count", "modis_frp_sum", "viirs_count", "viirs_frp_sum", "unique_fire_events"]
        copy_rows(cursor, f"copy weekly_grid ({', '.join(weekly_columns)}) from stdin", weekly_rows(paths["weekly grid"]), weekly_columns, "weekly grid")

    ensure_analytics_indexes()
    print("Dashboard indexes: ready")


if __name__ == "__main__":
    main()
