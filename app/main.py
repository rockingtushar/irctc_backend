# from fastapi import FastAPI
# from fastapi.middleware.cors import CORSMiddleware
# from fastapi.middleware.gzip import GZipMiddleware
# from app.routers.stations import router as stations_router
# from app.routers.trains import (router as trains_router,start_cleanup_task,stop_cleanup_task,)
# from sqlalchemy import text
# from app.database import engine
    
    


# app = FastAPI(
#     title="Indian Train Search API",
#     version="1.0.0",
# )


# # GZIP compression
# app.add_middleware(
#     GZipMiddleware,
#     minimum_size=1000,
#     compresslevel=6,
# )


# # CORS
# ALLOWED_ORIGINS = [
#     "http://localhost:5173",
#     "http://127.0.0.1:5173",
#     "http://localhost:3000",
#     "http://127.0.0.1:3000",
#     "https://irctc-peach.vercel.app",
# ]


# app.add_middleware(
#     CORSMiddleware,
#     allow_origins=ALLOWED_ORIGINS,
#     allow_credentials=True,
#     allow_methods=["*"],
#     allow_headers=["*"],
# )


# # Routers
# app.include_router(stations_router)
# app.include_router(trains_router)


# # ============================================================
# # TRAIN SESSION CLEANUP LIFECYCLE
# # ============================================================

# @app.on_event("startup")
# async def startup_train_cleanup() -> None:
#     await start_cleanup_task()


# @app.on_event("shutdown")
# async def shutdown_train_cleanup() -> None:
#     await stop_cleanup_task()

# @app.get("/")
# async def root():
#     return {
#         "message": "Indian Train Search API is running"
#     }


# @app.get("/health")
# async def health():
#     try:
#         async with engine.connect() as conn:
#             await conn.execute(text("SELECT 1"))

#         return {
#             "status": "ok",
#             "database": "connected"
#         }

#     except Exception:
#         return {
#             "status": "ok",
#             "database": "warming"
#         }


from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.middleware.gzip import GZipMiddleware
from app.routers.stations import router as stations_router
from app.routers.trains import (router as trains_router, start_cleanup_task, stop_cleanup_task)
from sqlalchemy import text
from app.database import engine
from app.routers import running_status
from app.routers import pnr_status
from app.routers import chart_vacancy
from app.routers.schedule import router as schedule_router

app = FastAPI(
    title="Indian Train Search API",
    version="1.0.0",
)

# GZIP compression
app.add_middleware(
    GZipMiddleware,
    minimum_size=1000,
    compresslevel=6,
)

# CORS
ALLOWED_ORIGINS = [
    "http://localhost:5173",
    "http://127.0.0.1:5173",
    "http://localhost:3000",
    "http://127.0.0.1:3000",
    "https://irctc-peach.vercel.app",
    "https://irctc-inky.vercel.app",
]

app.add_middleware(
    CORSMiddleware,
    allow_origins=ALLOWED_ORIGINS,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Routers
app.include_router(stations_router)
app.include_router(trains_router)
app.include_router(running_status.router)
app.include_router(pnr_status.router)
app.include_router(chart_vacancy.router)
app.include_router(schedule_router)
# ============================================================
# TRAIN SESSION CLEANUP LIFECYCLE
# ============================================================

@app.on_event("startup")
async def startup_train_cleanup() -> None:
    await start_cleanup_task()

@app.on_event("shutdown")
async def shutdown_train_cleanup() -> None:
    await stop_cleanup_task()

@app.get("/")
async def root():
    return {
        "message": "Indian Train Search API is running"
    }

@app.get("/health")
async def health():
    try:
        async with engine.connect() as conn:
            await conn.execute(text("SELECT 1"))
        return {
            "status": "ok",
            "database": "connected"
        }
    except Exception:
        return {
            "status": "ok",
            "database": "warming"
        }
