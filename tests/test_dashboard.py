from app.services.dashboard import get_dashboard


def test_mock_dashboard_contract() -> None:
    dashboard = get_dashboard(area="Cambodia", start_year=2026, mode="harmonized", time_view="week")

    assert dashboard.selected_area == "Cambodia"
    assert dashboard.summary.harmonized_activity > 0
    assert len(dashboard.trend) == 12
    assert len(dashboard.yearly_activity) >= 1
