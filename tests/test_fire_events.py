from datetime import date

from app.pipeline.build_fire_events import Detection, event_records, normalized_confidence


def test_nearby_same_day_cross_sensor_detections_form_one_event() -> None:
    records = list(event_records(date(2025, 1, 1), [
        Detection(12.0, 104.0, "MODIS", 0, 10.0, "80", "D"),
        Detection(12.001, 104.001, "VIIRS", 0, 4.0, "h", "N"),
        Detection(12.04, 104.04, "VIIRS", 0, 3.0, "n", "D"),
    ]))

    assert len(records) == 2
    combined = next(record for record in records if record["detection_count"] == 2)
    assert combined["source_sensors"] == "MODIS,VIIRS"
    assert combined["total_frp"] == 14.0
    assert combined["confidence_score"] == 85.0
    assert combined["daynight"] == "M"
    assert combined["sensor_agreement"] == "both"


def test_normalized_confidence_maps_viirs_and_keeps_modis_scale() -> None:
    assert normalized_confidence("VIIRS", "l") == 20.0
    assert normalized_confidence("VIIRS", "n") == 60.0
    assert normalized_confidence("VIIRS", "h") == 90.0
    assert normalized_confidence("MODIS", "73") == 73.0
