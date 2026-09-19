# from typing import Any
# import socket
# import httpx
# from fastapi import APIRouter, HTTPException
# from pydantic import BaseModel, Field

# from app.services.train_running import (
#     NTESRunningError,
#     fetch_running_status,
# )


# router = APIRouter(
#     prefix="/api/trains",
#     tags=["Train Running Status"],
# )


# class RunningStatusRequest(BaseModel):
#     train_no: str = Field(
#         ...,
#         min_length=5,
#         max_length=5,
#         pattern=r"^\d{5}$",
#         description="5-digit Indian Railways train number",
#     )

#     journey_date: str = Field(
#         ...,
#         min_length=1,
#         max_length=20,
#         description="Journey date in NTES format, e.g. 15-Sep-2026",
#     )


# @router.post("/running-status")
# async def running_status(
#     request: RunningStatusRequest,
# ) -> dict[str, Any]:

#     try:
#         result = await fetch_running_status(
#             train_number=request.train_no,
#             journey_date=request.journey_date,
#         )

#         return {
#             "success": True,
#             "data": result,
#         }

#     except NTESRunningError as exc:
#         raise HTTPException(
#             status_code=502,
#             detail=str(exc),
#         ) from exc

#     except Exception as exc:
#         # Do not expose internal exception details to API clients.
#         raise HTTPException(
#             status_code=500,
#             detail=(
#                 "Unexpected error while fetching "
#                 "train running status."
#             ),
#         ) from exc



# @router.get("/running-status-debug")
# async def running_status_debug():
#     result = {}

#     # 1. DNS
#     try:
#         ip = socket.gethostbyname("enquiry.indianrail.gov.in")
#         result["dns"] = {
#             "success": True,
#             "ip": ip,
#         }
#     except Exception as exc:
#         result["dns"] = {
#             "success": False,
#             "error": repr(exc),
#         }

#     # 2. Google - general outbound HTTPS
#     try:
#         async with httpx.AsyncClient(timeout=15.0) as client:
#             response = await client.get("https://www.google.com")

#             result["google"] = {
#                 "success": True,
#                 "status": response.status_code,
#             }

#     except Exception as exc:
#         result["google"] = {
#             "success": False,
#             "error": repr(exc),
#         }

#     try:
#         async with httpx.AsyncClient(
#             follow_redirects=True,
#             timeout=30.0,
#         ) as client:
#             response = await client.get(
#                 "https://www.irctc.co.in/"
#             )
    
#             result["irctc_homepage"] = {
#                 "success": True,
#                 "status": response.status_code,
#                 "url": str(response.url),
#                 "length": len(response.text),
#             }
#     except Exception as exc:
#         result["irctc_homepage"] = {
#             "success": False,
#             "error": repr(exc),
#         }

#     # 3. NTES using hostname
#     try:
#         async with httpx.AsyncClient(
#             follow_redirects=True,
#             timeout=30.0,
#         ) as client:
#             response = await client.get(
#                 "https://enquiry.indianrail.gov.in/mntes/"
#             )

#             result["ntes_hostname"] = {
#                 "success": True,
#                 "status": response.status_code,
#                 "url": str(response.url),
#             }

#     except Exception as exc:
#         result["ntes_hostname"] = {
#             "success": False,
#             "error": repr(exc),
#         }

#     # 4. NTES using direct IP
#     try:
#         async with httpx.AsyncClient(
#             follow_redirects=True,
#             timeout=30.0,
#             verify=False,
#         ) as client:
#             response = await client.get(
#                 "https://103.110.246.84/mntes/",
#                 headers={
#                     "Host": "enquiry.indianrail.gov.in",
#                 },
#             )

#             result["ntes_ip"] = {
#                 "success": True,
#                 "status": response.status_code,
#                 "url": str(response.url),
#             }

#     except Exception as exc:
#         result["ntes_ip"] = {
#             "success": False,
#             "error": repr(exc),
#         }

#     return result
import os
from typing import Any

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

    ntes_service_url = os.getenv("NTES_SERVICE_URL")

    if not ntes_service_url:
        raise HTTPException(
            status_code=500,
            detail="NTES_SERVICE_URL is not configured.",
        )

    url = f"{ntes_service_url.rstrip('/')}/api/trains/running-status"

    payload = {
        "train_no": request.train_no,
        "journey_date": request.journey_date,
    }

    try:
        async with httpx.AsyncClient(
            timeout=httpx.Timeout(
                connect=10.0,
                read=120.0,
                write=30.0,
                pool=10.0,
            )
        ) as client:

            response = await client.post(
                url,
                json=payload,
            )

            response.raise_for_status()

            return response.json()

    except httpx.HTTPStatusError as exc:
        try:
            detail = exc.response.json()
        except Exception:
            detail = exc.response.text or "NTES service returned an error."

        raise HTTPException(
            status_code=exc.response.status_code,
            detail=detail,
        ) from exc

    except httpx.RequestError as exc:
        raise HTTPException(
            status_code=502,
            detail="Unable to connect to NTES service.",
        ) from exc

    except Exception as exc:
        raise HTTPException(
            status_code=500,
            detail="Unexpected error while fetching train running status.",
        ) from exc