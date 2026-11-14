from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api.routes.analytics import router as analytics_router
from app.api.routes.dashboard import router as dashboard_router
from app.api.routes.health import router as health_router

app = FastAPI(title="Fire Activity API", version="0.1.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173", "http://127.0.0.1:5173"],
    allow_credentials=True,
    allow_methods=["GET"],
    allow_headers=["*"],
)

app.include_router(health_router, prefix="/api")
app.include_router(dashboard_router, prefix="/api")
app.include_router(analytics_router, prefix="/api")
