"""Serve saved machine-learning fire-risk prediction layers."""

from pathlib import Path

from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse

router = APIRouter(tags=["predictions"])

PREDICTION_FILE = Path(__file__).resolve().parents[2] / "assets" / "09_fire_prediction_results.geojson"


@router.get("/predictions/fire-risk")
def fire_risk_prediction() -> FileResponse:
    """Return the verified 3-day fire-risk snapshot as GeoJSON."""
    if not PREDICTION_FILE.is_file():
        raise HTTPException(status_code=404, detail="Fire-risk prediction snapshot is not available.")
    return FileResponse(PREDICTION_FILE, media_type="application/geo+json", filename="fire-risk-3-day-snapshot.geojson")
