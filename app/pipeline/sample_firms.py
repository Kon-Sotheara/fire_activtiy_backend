"""Create a deterministic, evenly distributed FIRMS CSV sample without changing raw data."""

from __future__ import annotations

import argparse
import csv
from pathlib import Path


def count_csv_rows(source: Path) -> int:
    with source.open("r", newline="", encoding="utf-8") as source_file:
        reader = csv.reader(source_file)
        next(reader)
        return sum(1 for _ in reader)


def sample_csv(source: Path, destination: Path, total_rows: int | None, target_rows: int) -> None:
    if destination.exists():
        raise FileExistsError(f"{destination} already exists; choose another output name or remove it explicitly.")
    source_rows = total_rows if total_rows is not None else count_csv_rows(source)
    if target_rows <= 0 or target_rows > source_rows:
        raise ValueError("target_rows must be between 1 and total_rows")

    written = 0
    with source.open("r", newline="", encoding="utf-8") as source_file, destination.open("x", newline="", encoding="utf-8") as destination_file:
        reader = csv.reader(source_file)
        writer = csv.writer(destination_file)
        writer.writerow(next(reader))
        for index, row in enumerate(reader, start=1):
            # Select exactly target_rows records, distributed from the first to the last source row.
            if (index * target_rows) // source_rows > ((index - 1) * target_rows) // source_rows:
                writer.writerow(row)
                written += 1
    if written != target_rows:
        destination.unlink(missing_ok=True)
        raise RuntimeError(f"Expected {target_rows:,} sampled rows but wrote {written:,}")
    print(f"Created {destination.name} with {written:,} evenly distributed records.")


def main() -> None:
    parser = argparse.ArgumentParser(description="Create a reproducible sampled FIRMS archive.")
    parser.add_argument("source", type=Path)
    parser.add_argument("destination", type=Path)
    parser.add_argument("--total-rows", type=int, help="Optional known logical CSV row count; omit to count first.")
    parser.add_argument("--target-rows", type=int, required=True)
    args = parser.parse_args()
    sample_csv(args.source, args.destination, args.total_rows, args.target_rows)


if __name__ == "__main__":
    main()
