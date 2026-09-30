from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.middleware.gzip import GZipMiddleware

from sqlalchemy import text

from curl_cffi import requests

from app.database import engine
from app.routers.stations import router as stations_router
from app.routers.trains import (
    router as trains_router,
    start_cleanup_task,
    stop_cleanup_task,
)
from app.routers import running_status
from app.routers import pnr_status
from app.routers import chart_vacancy

from app.routers.route import router as route_router
from app.routers.alternate_availability import router as alternate_availability_router
from app.routers.feedback import router as feedback_router





# ============================================================
# FASTAPI APP
# ============================================================

app = FastAPI(
    title="Indian Train Search API",
    version="1.0.0",
)




# ============================================================
# GZIP COMPRESSION
# ============================================================

app.add_middleware(
    GZipMiddleware,
    minimum_size=1000,
    compresslevel=6,
)


# ============================================================
# CORS
# ============================================================

ALLOWED_ORIGINS = [
    "http://localhost:5173",
    "http://127.0.0.1:5173",
    "http://localhost:3000",
    "http://127.0.0.1:3000",
    "https://irctc-woad.vercel.app",
    "https://irctc-mu.vercel.app",
]

app.add_middleware(
    CORSMiddleware,
    allow_origins=ALLOWED_ORIGINS,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# ============================================================
# ROUTERS
# ============================================================

app.include_router(stations_router)
app.include_router(trains_router)
app.include_router(running_status.router)
app.include_router(pnr_status.router)
app.include_router(chart_vacancy.router)
app.include_router(route_router)
app.include_router(alternate_availability_router)
app.include_router(feedback_router)



# ============================================================
# TRAIN SESSION CLEANUP LIFECYCLE
# ============================================================

@app.on_event("startup")
async def startup_train_cleanup() -> None:
    await start_cleanup_task()


@app.on_event("shutdown")
async def shutdown_train_cleanup() -> None:
    await stop_cleanup_task()


# ============================================================
# ROOT
# ============================================================

@app.get("/")
async def root():
    return {
        "message": "Indian Train Search API is running"
    }


# ============================================================
# HEALTH
# ============================================================

@app.get("/health")
async def health():
    try:
        async with engine.connect() as conn:
            await conn.execute(text("SELECT 1"))

        return {
            "status": "ok",
            "database": "connected",
        }

    except Exception:
        return {
            "status": "ok",
            "database": "warming",
        }
