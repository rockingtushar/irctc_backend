from typing import List

from fastapi import APIRouter, Depends, Query, Response
from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.models import Station
from app.schemas import StationLite


router = APIRouter(
    prefix="/api/stations",
    tags=["Stations"],
)


@router.get(
    "/all",
    response_model=List[StationLite],
)
async def get_all_stations(
    response: Response,
    db: AsyncSession = Depends(get_db),
):
    response.headers["Cache-Control"] = "public, max-age=604800"

    result = await db.execute(
        select(
            Station.station_code,
            Station.station_name,
        ).order_by(Station.station_name)
    )

    stations = result.all()

    return [
        StationLite(
            code=station.station_code,
            name=station.station_name,
        )
        for station in stations
    ]


@router.get(
    "/search",
    response_model=List[StationLite],
)
async def search_stations(
    q: str = Query(
        ...,
        min_length=2,
        max_length=100,
    ),
    db: AsyncSession = Depends(get_db),
):
    search_term = q.strip()

    if not search_term:
        return []

    pattern = f"%{search_term}%"

    result = await db.execute(
        select(
            Station.station_code,
            Station.station_name,
        )
        .where(
            or_(
                Station.station_code.ilike(pattern),
                Station.station_name.ilike(pattern),
            )
        )
        .order_by(Station.station_name)
        .limit(20)
    )

    stations = result.all()

    return [
        StationLite(
            code=station.station_code,
            name=station.station_name,
        )
        for station in stations
    ]