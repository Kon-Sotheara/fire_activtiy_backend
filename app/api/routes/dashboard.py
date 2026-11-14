from fastapi import APIRouter, Query

from app.schemas.dashboard import DashboardResponse
from app.services.dashboard import get_dashboard

router = APIRouter(prefix="/dashboard", tags=["dashboard"])


@router.get("", response_model=DashboardResponse)
def dashboard(
    area: str = Query(default="Cambodia", min_length=1, max_length=120),
    start_year: int = Query(default=2026, ge=2000, le=2100),
    mode: str = Query(default="harmonized", pattern="^(modis|viirs|harmonized)$"),
    time_view: str = Query(default="week", pattern="^(week|year)$"),
) -> DashboardResponse:
    return get_dashboard(area=area, start_year=start_year, mode=mode, time_view=time_view)
