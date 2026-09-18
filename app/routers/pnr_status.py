from typing import Any

from fastapi import APIRouter, HTTPException
from fastapi.responses import Response
from pydantic import BaseModel, Field

from app.services.pnr_status import (
    PNRStatusError,
    create_pnr_session,
    fetch_pnr_captcha,
    refresh_pnr_captcha,
    fetch_pnr_status,
    close_pnr_session,
)


router = APIRouter(
    prefix="/api/pnr",
    tags=["PNR Status"],
)


# ============================================================
# REQUEST MODELS
# ============================================================

class PNRStatusRequest(BaseModel):

    pnr: str = Field(
        ...,
        min_length=10,
        max_length=10,
        pattern=r"^\d{10}$",
    )

    captcha_answer: str | None = None

    session_id: str


class PNRCaptchaRequest(BaseModel):

    session_id: str


# ============================================================
# CREATE SESSION
# ============================================================

@router.post("/session")
async def pnr_session() -> dict[str, Any]:

    try:

        data = await create_pnr_session()

        return {
            "success": True,
            "data": data,
        }

    except PNRStatusError as exc:

        raise HTTPException(
            status_code=502,
            detail=str(exc),
        ) from exc


# ============================================================
# GET CAPTCHA
# ============================================================

@router.post("/captcha")
async def pnr_captcha(
    request: PNRCaptchaRequest,
) -> dict[str, Any]:

    try:

        data = await fetch_pnr_captcha(
            session_id=request.session_id,
        )

        return {
            "success": True,
            "data": data,
        }

    except PNRStatusError as exc:

        message = str(exc)

        if "session" in message.lower():
            status_code = 400
        elif "timed out" in message.lower():
            status_code = 504
        else:
            status_code = 502

        raise HTTPException(
            status_code=status_code,
            detail=message,
        ) from exc


# ============================================================
# REFRESH CAPTCHA
# ============================================================

@router.post("/captcha/refresh")
async def pnr_captcha_refresh(
    request: PNRCaptchaRequest,
) -> dict[str, Any]:

    try:

        data = await refresh_pnr_captcha(
            session_id=request.session_id,
        )

        return {
            "success": True,
            "data": data,
        }

    except PNRStatusError as exc:

        message = str(exc)

        if "session" in message.lower():
            status_code = 400
        elif "timed out" in message.lower():
            status_code = 504
        else:
            status_code = 502

        raise HTTPException(
            status_code=status_code,
            detail=message,
        ) from exc


# ============================================================
# PNR STATUS
# ============================================================

@router.post("/status")
async def pnr_status(
    request: PNRStatusRequest,
) -> dict[str, Any]:

    try:

        data = await fetch_pnr_status(
            pnr=request.pnr,
            captcha_answer=request.captcha_answer,
            session_id=request.session_id,
        )

        return {
            "success": True,
            "data": data,
        }

    except PNRStatusError as exc:

        message = str(exc)

        lower_message = message.lower()

        if "exactly 10 digits" in lower_message:

            status_code = 400

        elif (
            "captcha not matched" in lower_message
            or "captcha answer is required"
            in lower_message
            or "captcha has not been loaded"
            in lower_message
        ):

            status_code = 422

        elif "session" in lower_message:

            status_code = 400

        elif "timed out" in lower_message:

            status_code = 504

        else:

            status_code = 502

        raise HTTPException(
            status_code=status_code,
            detail=message,
        ) from exc


# ============================================================
# CLOSE SESSION
# ============================================================

@router.delete("/session/{session_id}")
async def delete_pnr_session(
    session_id: str,
) -> dict[str, Any]:

    await close_pnr_session(
        session_id
    )

    return {
        "success": True,
        "message": "PNR session closed.",
    }