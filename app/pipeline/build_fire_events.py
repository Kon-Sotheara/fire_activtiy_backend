"""Build unique daily fire events from nearby raw MODIS and VIIRS detections.

Run from backend/:
    .venv\\Scripts\\python.exe -m app.pipeline.build_fire_events --dry-run
    .venv\\Scripts\\python.exe -m app.pipeline.build_fire_events --replace
"""

from __future__ import annotations

import argparse
from collections import defaultdict
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from datetime import date
from math import floor
from typing import Any

from sqlalchemy import delete, insert, select, text

from app.database import fire_events, get_engine, hotspots

# About 1.1 km at the equator. The clustering is per day and FIRMS type, with
# min_samples=1, so every detection belongs to a fire event.
EPSILON_DEGREES = 0.01
INSERT_BATCH_SIZE = 5_000
VIIRS_CONFIDENCE_SCORES = {"l": 20.0, "n": 60.0, "h": 90.0}


@dataclass(frozen=True)
class Detection:
    latitude: float
    longitude: float
    sensor: str
    fire_type: int | None
    frp: float | None
    confidence: str | None
    daynight: str | None


def normalized_confidence(sensor: str, confidence: str | None) -> float | None:
    """Put MODIS and VIIRS confidence values on a common 0–100 scale."""
    if confidence is None:
        return None
    value = confidence.strip().lower()
    if sensor.upper() == "VIIRS":
        return VIIRS_CONFIDENCE_SCORES.get(value)
    try:
        return min(100.0, max(0.0, float(value)))
    except ValueError:
        return None


class UnionFind:
    def __init__(self, size: int) -> None:
        self.parents = list(range(size))

    def find(self, index: int) -> int:
        while self.parents[index] != index:
            self.parents[index] = self.parents[self.parents[index]]
            index = self.parents[index]
        return index

    def union(self, left: int, right: int) -> None:
        left_root, right_root = self.find(left), self.find(right)
        if left_root != right_root:
            self.parents[right_root] = left_root


def cluster_day(detections: Sequence[Detection], epsilon: float = EPSILON_DEGREES) -> list[list[Detection]]:
    """DBSCAN-style connected components with min_samples=1 and a grid index."""
    if not detections:
        return []

    grid: dict[tuple[int, int], list[int]] = defaultdict(list)
    for index, detection in enumerate(detections):
        grid[(floor(detection.latitude / epsilon), floor(detection.longitude / epsilon))].append(index)

    squared_epsilon = epsilon * epsilon
    groups = UnionFind(len(detections))
    for index, detection in enumerate(detections):
        row, column = floor(detection.latitude / epsilon), floor(detection.longitude / epsilon)
        for neighbor_row in range(row - 1, row + 2):
            for neighbor_column in range(column - 1, column + 2):
                for neighbor_index in grid.get((neighbor_row, neighbor_column), []):
                    if neighbor_index <= index:
                        continue
                    neighbor = detections[neighbor_index]
                    distance_squared = (detection.latitude - neighbor.latitude) ** 2 + (detection.longitude - neighbor.longitude) ** 2
                    if distance_squared <= squared_epsilon:
                        groups.union(index, neighbor_index)

    clusters: dict[int, list[Detection]] = defaultdict(list)
    for index, detection in enumerate(detections):
        clusters[groups.find(index)].append(detection)
    return list(clusters.values())


def event_records(acquisition_date: date, detections: Sequence[Detection]) -> Iterable[dict[str, Any]]:
    """Create one event record per spatial cluster, without merging FIRMS types."""
    by_type: dict[int | None, list[Detection]] = defaultdict(list)
    for detection in detections:
        by_type[detection.fire_type].append(detection)

    for fire_type, typed_detections in by_type.items():
        for cluster in cluster_day(typed_detections):
            sensors = {detection.sensor.upper() for detection in cluster}
            frp_values = [detection.frp for detection in cluster if detection.frp is not None]
            confidence_scores = [
                score for detection in cluster
                if (score := normalized_confidence(detection.sensor, detection.confidence)) is not None
            ]
            observation_periods = {detection.daynight.upper() for detection in cluster if detection.daynight}
            daynight = next(iter(observation_periods)) if len(observation_periods) == 1 else "M" if observation_periods else None
            sensor_agreement = "both" if sensors == {"MODIS", "VIIRS"} else "modis_only" if "MODIS" in sensors else "viirs_only"
            yield {
                "acq_date": acquisition_date,
                "latitude": sum(detection.latitude for detection in cluster) / len(cluster),
                "longitude": sum(detection.longitude for detection in cluster) / len(cluster),
                "detection_count": len(cluster),
                "modis_detections": sum(detection.sensor.upper() == "MODIS" for detection in cluster),
                "viirs_detections": sum(detection.sensor.upper() == "VIIRS" for detection in cluster),
                "source_sensors": ",".join(sorted(sensors)),
                "fire_type": fire_type,
                "total_frp": sum(frp_values) if frp_values else None,
                "confidence_score": sum(confidence_scores) / len(confidence_scores) if confidence_scores else None,
                "daynight": daynight,
                "sensor_agreement": sensor_agreement,
            }


def grouped_detections() -> Iterable[tuple[date, list[Detection]]]:
    statement = (
        select(hotspots.c.acq_date, hotspots.c.latitude, hotspots.c.longitude, hotspots.c.sensor, hotspots.c.fire_type, hotspots.c.frp, hotspots.c.confidence, hotspots.c.daynight)
        .order_by(hotspots.c.acq_date, hotspots.c.id)
    )
    with get_engine().connect() as connection:
        result = connection.execution_options(stream_results=True).execute(statement).mappings()
        current_date: date | None = None
        daily_detections: list[Detection] = []
        for row in result:
            acquisition_date = row["acq_date"]
            if current_date is not None and acquisition_date != current_date:
                yield current_date, daily_detections
                daily_detections = []
            current_date = acquisition_date
            daily_detections.append(Detection(
                latitude=float(row["latitude"]),
                longitude=float(row["longitude"]),
                sensor=str(row["sensor"]),
                fire_type=row["fire_type"],
                frp=float(row["frp"]) if row["frp"] is not None else None,
                confidence=row["confidence"],
                daynight=row["daynight"],
            ))
        if current_date is not None:
            yield current_date, daily_detections


def build_events(replace: bool, dry_run: bool) -> tuple[int, int]:
    event_count, detection_count = 0, 0
    if dry_run:
        for acquisition_date, detections in grouped_detections():
            detection_count += len(detections)
            event_count += sum(1 for _ in event_records(acquisition_date, detections))
        return event_count, detection_count

    engine = get_engine()
    fire_events.create(engine, checkfirst=True)
    with engine.begin() as connection:
        connection.execute(text("alter table fire_events add column if not exists confidence_score double precision"))
        connection.execute(text("alter table fire_events add column if not exists daynight varchar(5)"))
        connection.execute(text("alter table fire_events add column if not exists sensor_agreement varchar(20) not null default 'unknown'"))
        if replace:
            connection.execute(delete(fire_events))
        else:
            existing = connection.scalar(select(fire_events.c.id).limit(1))
            if existing is not None:
                raise RuntimeError("fire_events already contains records. Use --replace to rebuild it from hotspots.")

        batch: list[dict[str, Any]] = []
        for acquisition_date, detections in grouped_detections():
            detection_count += len(detections)
            for record in event_records(acquisition_date, detections):
                batch.append(record)
                event_count += 1
                if len(batch) >= INSERT_BATCH_SIZE:
                    connection.execute(insert(fire_events), batch)
                    batch.clear()
        if batch:
            connection.execute(insert(fire_events), batch)
    return event_count, detection_count


def main() -> None:
    parser = argparse.ArgumentParser(description="Cluster same-day nearby FIRMS detections into fire events.")
    parser.add_argument("--replace", action="store_true", help="Replace previously derived fire events.")
    parser.add_argument("--dry-run", action="store_true", help="Report the event count without writing to PostgreSQL.")
    args = parser.parse_args()
    events, detections = build_events(replace=args.replace, dry_run=args.dry_run)
    action = "Would create" if args.dry_run else "Created"
    print(f"{action} {events:,} unique fire events from {detections:,} raw detections.")


if __name__ == "__main__":
    main()
