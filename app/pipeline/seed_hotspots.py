"""Load FIRMS MODIS and VIIRS archive CSVs into the existing PostgreSQL hotspots table.

Run from backend/:
    .venv\\Scripts\\python.exe -m app.pipeline.seed_hotspots --dry-run
    .venv\\Scripts\\python.exe -m app.pipeline.seed_hotspots

Use --replace only when intentionally removing all existing hotspot records.
"""

from __future__ import annotations

import argparse
import re
from datetime import UTC, date, datetime, time
from pathlib import Path
from typing import Any, Iterable

import pandas as pd
from sqlalchemy import func, insert, select, text

from app.database import get_engine, hotspots

RAW_DIRECTORY = Path(__file__).resolve().parents[3] / "data" / "raw"
CHUNK_SIZE = 25_000
REQUIRED_COLUMNS = {
    "latitude", "longitude", "brightness", "scan", "track", "acq_date", "acq_time",
    "satellite", "instrument", "confidence", "version", "bright_t31", "frp", "daynight", "type",
}


def nullable_float(value: Any) -> float | None:
    if value is None or pd.isna(value):
        return None
    return float(value)


def nullable_int(value: Any) -> int | None:
    if value is None or pd.isna(value):
        return None
    return int(float(value))


def nullable_text(value: Any) -> str | None:
    if value is None or pd.isna(value):
        return None
    text_value = str(value).strip()
    return text_value or None


def parse_firms_time(value: Any) -> time | None:
    """Convert FIRMS HHMM values such as 400 and 0512 to a Python time."""
    text_value = nullable_text(value)
    if not text_value:
        return None
    digits = re.sub(r"\D", "", text_value).zfill(4)
    if len(digits) != 4:
        return None
    hour, minute = int(digits[:2]), int(digits[2:])
    return time(hour, minute) if hour < 24 and minute < 60 else None


def sensor_from_row(row: dict[str, Any]) -> str:
    instrument = (nullable_text(row.get("instrument")) or "").upper()
    if instrument == "MODIS":
        return "MODIS"
    return "VIIRS"


def normalize_row(row: dict[str, Any]) -> dict[str, Any] | None:
    latitude, longitude = nullable_float(row.get("latitude")), nullable_float(row.get("longitude"))
    if latitude is None or longitude is None or not (-90 <= latitude <= 90 and -180 <= longitude <= 180):
        return None

    try:
        acquisition_date = date.fromisoformat(str(row["acq_date"]))
    except (KeyError, TypeError, ValueError):
        return None

    acquisition_time = parse_firms_time(row.get("acq_time"))
    acquired_at = datetime.combine(acquisition_date, acquisition_time or time.min, tzinfo=UTC)
    return {
        "latitude": latitude,
        "longitude": longitude,
        "acq_date": acquisition_date,
        "acq_time": acquisition_time,
        "acq_datetime": acquired_at,
        "sensor": sensor_from_row(row),
        "satellite": nullable_text(row.get("satellite")),
        "instrument": nullable_text(row.get("instrument")),
        "brightness": nullable_float(row.get("brightness")),
        "bright_t31": nullable_float(row.get("bright_t31")),
        "frp": nullable_float(row.get("frp")),
        "scan": nullable_float(row.get("scan")),
        "track": nullable_float(row.get("track")),
        "confidence": nullable_text(row.get("confidence")),
        "daynight": nullable_text(row.get("daynight")),
        "fire_type": nullable_int(row.get("type")),
        "version": nullable_text(row.get("version")),
    }


def rows_from_csv(path: Path, limit: int | None = None) -> Iterable[list[dict[str, Any]]]:
    remaining = limit
    for chunk in pd.read_csv(path, chunksize=CHUNK_SIZE, dtype={"acq_time": "string", "confidence": "string"}):
        missing = REQUIRED_COLUMNS.difference(chunk.columns)
        if missing:
            raise ValueError(f"{path.name} is missing required columns: {', '.join(sorted(missing))}")
        records = [record for row in chunk.to_dict(orient="records") if (record := normalize_row(row))]
        if remaining is not None:
            records = records[:remaining]
            remaining -= len(records)
        if records:
            yield records
        if remaining is not None and remaining <= 0:
            return


def input_files(names: list[str] | None = None) -> list[Path]:
    files = [RAW_DIRECTORY / name for name in names] if names else sorted(RAW_DIRECTORY.glob("*.csv"))
    missing = [path.name for path in files if not path.is_file()]
    if missing:
        raise FileNotFoundError(f"CSV file(s) not found in {RAW_DIRECTORY}: {', '.join(missing)}")
    if not files:
        raise FileNotFoundError(f"No CSV files found in {RAW_DIRECTORY}")
    return files


def seed(replace: bool, append: bool, dry_run: bool, limit: int | None, names: list[str] | None = None) -> tuple[int, int]:
    files = input_files(names)
    loaded, rejected = 0, 0

    if dry_run:
        for csv_path in files:
            file_loaded = sum(len(records) for records in rows_from_csv(csv_path, limit=limit))
            loaded += file_loaded
            print(f"{csv_path.name}: {file_loaded:,} valid rows")
        return loaded, rejected

    engine = get_engine()
    with engine.begin() as connection:
        existing = connection.scalar(select(func.count()).select_from(hotspots)) or 0
        if existing and not (replace or append):
            raise RuntimeError(
                f"hotspots already contains {existing:,} rows. Use --append for new, non-overlapping source files, or --replace to clear and reload."
            )
        if replace:
            connection.execute(text("TRUNCATE TABLE hotspots RESTART IDENTITY"))

        for csv_path in files:
            file_loaded = 0
            for records in rows_from_csv(csv_path, limit=limit):
                file_loaded += len(records)
                connection.execute(insert(hotspots), records)
            loaded += file_loaded
            print(f"{csv_path.name}: {file_loaded:,} valid rows")

    return loaded, rejected


def main() -> None:
    parser = argparse.ArgumentParser(description="Seed the existing hotspots table from FIRMS CSV archives.")
    parser.add_argument("--replace", action="store_true", help="Delete all existing hotspot rows before importing.")
    parser.add_argument("--append", action="store_true", help="Append only new, non-overlapping source files to an existing table.")
    parser.add_argument("--dry-run", action="store_true", help="Validate input and report counts without inserting rows.")
    parser.add_argument("--limit", type=int, default=None, help="Maximum rows to read from each source file (for a small test import).")
    parser.add_argument("--files", nargs="+", metavar="CSV", help="Specific CSV file name(s) from data/raw to import.")
    args = parser.parse_args()
    if args.limit is not None and args.limit <= 0:
        parser.error("--limit must be a positive integer")
    if args.replace and args.append:
        parser.error("Choose either --replace or --append, not both")

    loaded, rejected = seed(replace=args.replace, append=args.append, dry_run=args.dry_run, limit=args.limit, names=args.files)
    operation = "Validated" if args.dry_run else "Imported"
    print(f"{operation} {loaded:,} rows; rejected {rejected:,} malformed rows.")


if __name__ == "__main__":
    main()
