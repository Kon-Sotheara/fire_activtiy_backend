from datetime import time

from app.pipeline.seed_hotspots import normalize_row, parse_firms_time


def test_parse_firms_time_preserves_leading_zeroes() -> None:
    assert parse_firms_time("0400") == time(4, 0)
    assert parse_firms_time("512") == time(5, 12)


def test_normalize_modis_row() -> None:
    row = {
        "latitude": 13.9656, "longitude": 104.421, "acq_date": "2020-01-01", "acq_time": "0400",
        "instrument": "MODIS", "satellite": "Terra", "brightness": 313.5, "scan": 1.7, "track": 1.3,
        "confidence": "33", "version": "6.03", "bright_t31": 293.9, "frp": 9.7, "daynight": "D", "type": 0,
    }
    record = normalize_row(row)
    assert record is not None
    assert record["sensor"] == "MODIS"
    assert record["acq_time"] == time(4, 0)
    assert record["acq_datetime"].isoformat() == "2020-01-01T04:00:00+00:00"
