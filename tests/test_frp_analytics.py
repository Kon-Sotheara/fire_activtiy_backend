from app.services.analytics import HotspotFilters, event_clauses


def test_event_filters_keep_sensor_and_fire_type_constraints() -> None:
    sql = " ".join(str(condition) for condition in event_clauses(HotspotFilters(sensor="viirs", fire_type=0)))

    assert "fire_events.viirs_detections" in sql
    assert "fire_events.fire_type" in sql
