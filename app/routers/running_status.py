from typing import Any

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from app.services.train_running import (
    NTESRunningError,
    fetch_running_status,
)


router = APIRouter(
    prefix="/api/trains",
    tags=["Train Running Status"],
)


class RunningStatusRequest(BaseModel):
    train_no: str = Field(
        ...,
        min_length=5,
        max_length=5,
        pattern=r"^\d{5}$",
        description="5-digit Indian Railways train number",
    )

    journey_date: str = Field(
        ...,
        min_length=1,
        max_length=20,
        description="Journey date in NTES format, e.g. 15-Sep-2026",
    )


@router.post("/running-status")
async def running_status(
    request: RunningStatusRequest,
) -> dict[str, Any]:

    try:
        result = await fetch_running_status(
            train_number=request.train_no,
            journey_date=request.journey_date,
        )

        return {
            "success": True,
            "data": result,
        }

    except NTESRunningError as exc:
        raise HTTPException(
            status_code=502,
            detail=str(exc),
        ) from exc

    except Exception as exc:
        raise HTTPException(
            status_code=500,
            detail="Unexpected error while fetching train running status.",
        ) from exc
