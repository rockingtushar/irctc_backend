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
from app.routers.schedule import router as schedule_router
from app.routers.route import router as route_router



# ============================================================
# FASTAPI APP
# ============================================================

app = FastAPI(
    title="Indian Train Search API",
    version="1.0.0",
)


# ============================================================
# TEST: IRCTC SCHEDULE ACCESS
# ============================================================

@app.get("/test/schedule/{train_number}")
async def test_schedule(train_number: str):
    url = (
        "https://www.irctc.co.in/"
        f"eticketing/protected/mapps1/trnscheduleenquiry/{train_number}"
    )

    headers = {
        "Accept": "application/json, text/plain, */*",
        "Accept-Language": "en-GB,en-US;q=0.9,en;q=0.8,hi;q=0.7",
        "Origin": "https://www.irctc.co.in",
        "Referer": "https://www.irctc.co.in/",
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/153.0.0.0 Safari/537.36"
        ),
    }

    try:
        async with requests.AsyncSession(
            impersonate="chrome",
            timeout=30.0,
            headers=headers,
        ) as client:
            response = await client.get(
                url,
                headers=headers,
            )

        return {
            "success": True,
            "status": response.status_code,
            "url": str(response.url),
            "content_type": response.headers.get("content-type"),
            "length": len(response.content),
            "body": response.text[:1000],
        }

    except Exception as exc:
        raise HTTPException(
            status_code=502,
            detail=f"Schedule test failed: {exc}",
        ) from exc


# ============================================================
# TEST: NTES ACCESS
# ============================================================

@app.get("/test/ntes")
async def test_ntes():
    urls = [
        "https://enquiry.indianrail.gov.in/",
        "https://enquiry.indianrail.gov.in/mntes/",
    ]

    results = []

    for url in urls:
        try:
            response = requests.get(
                url,
                impersonate="chrome",
                timeout=15,
            )

            results.append({
                "url": url,
                "success": True,
                "status": response.status_code,
                "final_url": str(response.url),
                "content_type": response.headers.get("content-type"),
                "length": len(response.content),
                "body": response.text[:300],
            })

        except Exception as exc:
            results.append({
                "url": url,
                "success": False,
                "error_type": type(exc).__name__,
                "error": str(exc),
            })

    return {
        "success": True,
        "tests": results,
    }


# ============================================================
# TEST: INDIAN RAILWAYS TLS
# ============================================================

@app.get("/test/railway-tls")
async def test_railway_tls():
    url = (
        "https://www.indianrail.gov.in/"
        "enquiry/TBIS/TrainBetweenImportantStations.html?locale=en"
    )

    try:
        response = requests.get(
            url,
            impersonate="chrome",
            timeout=20,
        )

        return {
            "success": True,
            "status": response.status_code,
            "content_type": response.headers.get("content-type"),
            "length": len(response.content),
        }

    except Exception as exc:
        return {
            "success": False,
            "error_type": type(exc).__name__,
            "error": str(exc),
        }


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
app.include_router(schedule_router)
app.include_router(route_router)



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
