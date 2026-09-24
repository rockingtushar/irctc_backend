from typing import Any
import os

import httpx
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field


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

    # Google Cloud Run NTES service URL
    ntes_service_url = os.getenv("NTES_SERVICE_URL")

    if not ntes_service_url:
        raise HTTPException(
            status_code=500,
            detail="NTES_SERVICE_URL is not configured on Render.",
        )

    target_url = (
        f"{ntes_service_url.rstrip('/')}"
        "/api/trains/running-status"
    )

    try:
        async with httpx.AsyncClient(
            timeout=60.0,
        ) as client:

            response = await client.post(
                target_url,
                json={
                    "train_no": request.train_no,
                    "journey_date": request.journey_date,
                },
            )

        # Google Cloud returned an error
        if response.status_code >= 400:

            try:
                error_data = response.json()
                detail = error_data.get("detail")
            except Exception:
                detail = None

            raise HTTPException(
                status_code=response.status_code,
                detail=detail or "Google Cloud NTES service returned an error.",
            )

        # Return Google Cloud response directly
        return response.json()

    except HTTPException:
        raise

    except httpx.HTTPError as exc:
        raise HTTPException(
            status_code=502,
            detail="Google Cloud NTES service is unavailable.",
        ) from exc

    except Exception as exc:
        raise HTTPException(
            status_code=500,
            detail="Unexpected error while contacting Google Cloud NTES service.",
        ) from exc
