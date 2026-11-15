from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api.routes.analytics import router as analytics_router
from app.api.routes.dashboard import router as dashboard_router
from app.api.routes.health import router as health_router
from app.api.routes.predictions import router as predictions_router
from app.database import ensure_analytics_indexes, settings

app = FastAPI(title="Fire Activity API", version="0.1.0")


@app.on_event("startup")
def prepare_database_for_dashboard() -> None:
    """Ensure the first deployed dashboard request is not slowed by table scans."""
    ensure_analytics_indexes()

allowed_origins = [
    "http://localhost:5173",
    "http://127.0.0.1:5173",
    "https://fire-activity-frontend.vercel.app",
]
allowed_origins.extend(origin.strip() for origin in settings.frontend_origin.split(",") if origin.strip())

app.add_middleware(
    CORSMiddleware,
    allow_origins=allowed_origins,
    allow_credentials=True,
    allow_methods=["GET"],
    allow_headers=["*"],
)

app.include_router(health_router, prefix="/api")
app.include_router(dashboard_router, prefix="/api")
app.include_router(analytics_router, prefix="/api")
app.include_router(predictions_router, prefix="/api")
