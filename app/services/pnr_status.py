from __future__ import annotations

import base64
import re
import time
import uuid
from dataclasses import dataclass, field
from typing import Any

from curl_cffi import requests as curl_requests


# ============================================================
# INDIAN RAILWAYS PNR CONFIG
# ============================================================

BASE_URL = "https://www.indianrail.gov.in"

PNR_PAGE_URL = (
    f"{BASE_URL}/enquiry/PNR/PnrEnquiry.html?locale=en"
)

CAPTCHA_CONFIG_URL = (
    f"{BASE_URL}/enquiry/CaptchaConfig"
)

CAPTCHA_DRAW_URL = (
    f"{BASE_URL}/enquiry/captchaDraw.png"
)

COMMON_CAPTCHA_URL = (
    f"{BASE_URL}/enquiry/CommonCaptcha"
)


# Same general browser identity used by your Chrome request.
USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/153.0.0.0 Safari/537.36"
)

PNR_REFERER = PNR_PAGE_URL

SESSION_TTL_SECONDS = 30 * 60


# ============================================================
# ERRORS
# ============================================================

class PNRStatusError(Exception):
    pass


# ============================================================
# SESSION
# ============================================================

@dataclass
class PNRSession:
    client: curl_requests.AsyncSession
    created_at: float
    last_used: float
    captcha_config: str = "0"
    captcha_loaded: bool = False
    captcha_verified: bool = False
    captcha_loaded_at: float | None = None
    lock: Any = field(default_factory=lambda: None)


pnr_sessions: dict[str, PNRSession] = {}


# ============================================================
# HEADERS
# ============================================================

def page_headers() -> dict[str, str]:
    """
    Headers for normal PNR HTML page request.
    """

    return {
        "User-Agent": USER_AGENT,
        "Accept": (
            "text/html,application/xhtml+xml,"
            "application/xml;q=0.9,image/avif,image/webp,"
            "image/apng,*/*;q=0.8"
        ),
        "Accept-Language": (
            "en-GB,en-US;q=0.9,en;q=0.8,hi;q=0.7"
        ),
        "Referer": PNR_REFERER,
    }


def ajax_headers() -> dict[str, str]:
    """
    Headers used by jQuery $.ajax() calls in pnrEnquiryJS.js.
    """

    return {
        "User-Agent": USER_AGENT,
        "Accept": "*/*",
        "Accept-Language": (
            "en-GB,en-US;q=0.9,en;q=0.8,hi;q=0.7"
        ),
        "Referer": PNR_REFERER,
        "X-Requested-With": "XMLHttpRequest",
    }


def image_headers() -> dict[str, str]:
    """
    Headers for captchaDraw.png.
    """

    return {
        "User-Agent": USER_AGENT,
        "Accept": "image/avif,image/webp,image/apng,image/svg+xml,"
        "image/*,*/*;q=0.8",
        "Accept-Language": (
            "en-GB,en-US;q=0.9,en;q=0.8,hi;q=0.7"
        ),
        "Referer": PNR_REFERER,
    }


# ============================================================
# VALIDATION
# ============================================================

def validate_pnr(pnr: str) -> str:
    value = str(pnr).strip()

    if not re.fullmatch(r"\d{10}", value):
        raise PNRStatusError(
            "PNR must be exactly 10 digits."
        )

    return value


# ============================================================
# SESSION HELPERS
# ============================================================

def _get_session(session_id: str) -> PNRSession:
    session = pnr_sessions.get(session_id)

    if session is None:
        raise PNRStatusError(
            "PNR session not found or expired."
        )

    now = time.time()

    if now - session.created_at > SESSION_TTL_SECONDS:
        pnr_sessions.pop(session_id, None)

        try:
            # Close asynchronously later through explicit cleanup.
            pass
        except Exception:
            pass

        raise PNRStatusError(
            "PNR session expired."
        )

    session.last_used = now

    return session


# ============================================================
# CREATE PNR SESSION
# ============================================================

async def _create_session() -> tuple[str, PNRSession]:

    client = curl_requests.AsyncSession(
        impersonate="chrome",
        allow_redirects=True,
        timeout=20.0,
    )

    try:

        # ----------------------------------------------------
        # STEP 1
        # Browser opens:
        #
        # /enquiry/PNR/PnrEnquiry.html?locale=en
        # ----------------------------------------------------

        page_response = await client.get(
            PNR_PAGE_URL,
            headers=page_headers(),
        )

        page_response.raise_for_status()

        # ----------------------------------------------------
        # STEP 2
        # Exact JS flow:
        #
        # $.ajax({
        #     url: "../CaptchaConfig",
        #     async: false
        # })
        #
        # JS stores response in captchaConfig.
        # ----------------------------------------------------

        captcha_response = await client.get(
            CAPTCHA_CONFIG_URL,
            headers=ajax_headers(),
        )

        captcha_response.raise_for_status()

        captcha_config = captcha_response.text.strip()

        if not captcha_config:
            captcha_config = "0"

        session_id = uuid.uuid4().hex

        session = PNRSession(
            client=client,
            created_at=time.time(),
            last_used=time.time(),
            captcha_config=captcha_config,
            captcha_loaded=False,
            captcha_verified=False,
            captcha_loaded_at=None,
        )

        pnr_sessions[session_id] = session

        # print()
        # print("========== PNR SESSION CREATED ==========")
        # print("SESSION:", session_id)
        # print("PNR PAGE STATUS:", page_response.status_code)
        # print("CAPTCHA CONFIG:", repr(captcha_config))
        # print("COOKIES:", list(client.cookies.keys()))
        # print("=========================================")
        # print()

        return session_id, session

    except Exception as exc:

        await client.aclose()  # type: ignore[attr-defined]  # type: ignore[attr-defined]

        raise PNRStatusError(
            "Unable to initialize Indian Railways PNR session."
        ) from exc


async def create_pnr_session() -> dict[str, Any]:

    session_id, session = await _create_session()

    captcha_required = session.captcha_config != "0"

    return {
        "session_id": session_id,
        "expires_in": SESSION_TTL_SECONDS,
        "captcha_required": captcha_required,
    }


# ============================================================
# LOAD CAPTCHA
# ============================================================

async def fetch_pnr_captcha(
    session_id: str,
) -> dict[str, Any]:

    session = _get_session(session_id)

    # --------------------------------------------------------
    # If server says CAPTCHA is disabled, don't fetch image.
    # --------------------------------------------------------

    if session.captcha_config == "0":

        return {
            "session_id": session_id,
            "captcha_required": False,
            "content_type": None,
            "image_base64": None,
        }

    try:

        # ----------------------------------------------------
        # EXACT JS:
        #
        # $('#CaptchaImgID').attr(
        #     'src',
        #     '../captchaDraw.png?' + new Date().getTime()
        # );
        #
        # ----------------------------------------------------

        timestamp = str(int(time.time() * 1000))

        response = await session.client.get(
            CAPTCHA_DRAW_URL,
            params={
                "_": timestamp,
            },
            headers=image_headers(),
        )

        response.raise_for_status()

        content_type = (
            response.headers.get(
                "content-type",
                "image/png",
            )
        )

        if not response.content:
            raise PNRStatusError(
                "Indian Railways returned an empty CAPTCHA image."
            )

        session.captcha_loaded = True
        session.captcha_verified = False
        session.captcha_loaded_at = time.time()
        session.last_used = time.time()

        encoded = base64.b64encode(
            response.content
        ).decode("ascii")

        # print()
        # print("========== PNR CAPTCHA ==========")
        # print("SESSION:", session_id)
        # print("STATUS:", response.status_code)
        # print("CONTENT TYPE:", content_type)
        # print("SIZE:", len(response.content))
        # print("=================================")
        # print()

        return {
            "session_id": session_id,
            "captcha_required": True,
            "content_type": content_type,
            "image_base64": encoded,
        }

    except Exception as exc:

        raise PNRStatusError(
            "Unable to load Indian Railways CAPTCHA."
        ) from exc


# ============================================================
# REFRESH CAPTCHA
# ============================================================

async def refresh_pnr_captcha(
    session_id: str,
) -> dict[str, Any]:

    return await fetch_pnr_captcha(session_id)


# ============================================================
# NORMALIZATION
# ============================================================

def _clean(value: Any) -> Any:

    if isinstance(value, str):

        value = value.strip()

        return value or None

    return value


def _normalize_passenger(
    item: dict[str, Any],
) -> dict[str, Any]:

    return {
        "passenger_number": item.get(
            "passengerSerialNumber"
        ),
        "booking": {
            "status": _clean(
                item.get("bookingStatus")
            ),
            "coach": _clean(
                item.get("bookingCoachId")
            ),
            "berth_number": item.get(
                "bookingBerthNo"
            ),
            "berth_code": _clean(
                item.get("bookingBerthCode")
            ),
            "details": _clean(
                item.get("bookingStatusDetails")
            ),
        },
        "current": {
            "status": _clean(
                item.get("currentStatus")
            ),
            "coach": _clean(
                item.get("currentCoachId")
            ),
            "berth_number": item.get(
                "currentBerthNo"
            ),
            "berth_code": _clean(
                item.get("currentBerthCode")
            ),
            "details": _clean(
                item.get("currentStatusDetails")
            ),
        },
    }


def _status_summary(
    passengers: list[dict[str, Any]],
) -> str | None:

    statuses = {
        str(
            passenger.get(
                "current",
                {},
            ).get(
                "status"
            )
            or ""
        ).upper()
        for passenger in passengers
    }

    statuses.discard("")

    if not statuses:
        return None

    if statuses == {"CNF"}:
        return "Confirmed"

    if statuses == {"RAC"}:
        return "RAC"

    if statuses == {"WL"}:
        return "Waitlisted"

    if statuses == {"CAN"}:
        return "Cancelled"

    if len(statuses) == 1:
        return next(iter(statuses))

    return "Mixed"


def _normalize(
    payload: dict[str, Any],
) -> dict[str, Any]:

    raw_passengers = (
        payload.get("passengerList")
        or []
    )

    passengers = [
        _normalize_passenger(item)
        for item in raw_passengers
        if isinstance(item, dict)
    ]

    messages = (
        payload.get("informationMessage")
        or []
    )

    if not isinstance(messages, list):
        messages = [messages]

    messages = [
        str(message).strip()
        for message in messages
        if str(message).strip()
    ]

    generated = payload.get(
        "generatedTimeStamp"
    )

    if not isinstance(generated, dict):
        generated = None

    return {
        "pnr": _clean(
            payload.get("pnrNumber")
        ),

        "journey": {
            "date": _clean(
                payload.get("dateOfJourney")
            ),
            "train_number": _clean(
                payload.get("trainNumber")
            ),
            "train_name": _clean(
                payload.get("trainName")
            ),
            "source_station": _clean(
                payload.get("sourceStation")
            ),
            "destination_station": _clean(
                payload.get("destinationStation")
            ),
            "reservation_upto": _clean(
                payload.get("reservationUpto")
            ),
            "boarding_point": _clean(
                payload.get("boardingPoint")
            ),
            "journey_class": _clean(
                payload.get("journeyClass")
            ),
            "distance_km": payload.get(
                "distance"
            ),
            "arrival_date": _clean(
                payload.get("arrivalDate")
            ),
        },

        "passenger_count": payload.get(
            "numberOfpassenger",
            len(passengers),
        ),

        "passengers": passengers,

        "status": _status_summary(
            passengers
        ),

        "chart_status": _clean(
            payload.get("chartStatus")
        ),

        "quota": _clean(
            payload.get("quota")
        ),

        "booking_fare": payload.get(
            "bookingFare"
        ),

        "ticket_fare": payload.get(
            "ticketFare"
        ),

        "booking_date": _clean(
            payload.get("bookingDate")
        ),

        "vikalp_status": _clean(
            payload.get("vikalpStatus")
        ),

        "waitlist_type": payload.get(
            "waitListType"
        ),

        "is_waitlisted": (
            payload.get("isWL") == "Y"
            if payload.get("isWL") is not None
            else None
        ),

        "information_messages": messages,

        "timestamp": _clean(
            payload.get("timeStamp")
        ),

        "generated_timestamp": generated,
    }


# ============================================================
# FETCH PNR STATUS
# ============================================================

async def fetch_pnr_status(
    pnr: str,
    captcha_answer: str | None,
    session_id: str,
) -> dict[str, Any]:

    pnr = validate_pnr(pnr)

    session = _get_session(session_id)

    captcha_answer = (
        captcha_answer.strip()
        if captcha_answer is not None
        else ""
    )

    # --------------------------------------------------------
    # CAPTCHA REQUIRED
    # --------------------------------------------------------

    if session.captcha_config != "0":

        if not session.captcha_loaded:
            raise PNRStatusError(
                "CAPTCHA has not been loaded. "
                "Call /api/pnr/captcha first."
            )

        if not captcha_answer:
            raise PNRStatusError(
                "CAPTCHA answer is required."
            )

    # --------------------------------------------------------
    # EXACT JS REQUEST
    #
    # $.ajax({
    #     url : '../CommonCaptcha',
    #     data : {
    #         inputCaptcha : $('#inputCaptcha').val(),
    #         inputPnrNo : $('#inputPnrNo').val(),
    #         inputPage : 'PNR',
    #         language : language
    #     }
    # });
    #
    # jQuery defaults to GET.
    # --------------------------------------------------------

    params = {
        "inputCaptcha": captcha_answer,
        "inputPnrNo": pnr,
        "inputPage": "PNR",
        "language": "en",
    }

    # print()
    # print("========== PNR REQUEST ==========")
    # print("URL:", COMMON_CAPTCHA_URL)
    # print("PARAMS:", params)
    # print("SESSION:", session_id)
    # print("COOKIES:", list(session.client.cookies.keys()))
    # print("CAPTCHA CONFIG:", repr(session.captcha_config))
    # print("CAPTCHA LOADED:", session.captcha_loaded)
    # print("=================================")
    # print()

    try:

        response = await session.client.get(
            COMMON_CAPTCHA_URL,
            params=params,
            headers=ajax_headers(),
        )

        response.raise_for_status()

    except Exception as exc:

        raise PNRStatusError(
            "Indian Railways PNR request failed."
        ) from exc

    # print()
    # print("========== PNR RESPONSE ==========")
    # print("STATUS:", response.status_code)
    # print(
        # "CONTENT TYPE:",
        # response.headers.get("content-type"),
    # )
    # print("CONTENT LENGTH:", len(response.content))
    # print("BODY:", response.text[:2000])
    # print("==================================")
    # print()

    # --------------------------------------------------------
    # Parse JSON
    # --------------------------------------------------------

    try:

        payload = response.json()

    except ValueError as exc:

        raise PNRStatusError(
            "Indian Railways returned invalid JSON."
        ) from exc

    if not isinstance(payload, dict):

        raise PNRStatusError(
            "Indian Railways returned an invalid PNR response."
        )

    error_message = str(
        payload.get("errorMessage")
        or ""
    ).strip()

    flag = str(
        payload.get("flag")
        or ""
    ).strip()

    # --------------------------------------------------------
    # EXACT JS BEHAVIOUR
    #
    # if(resp.flag == 'NO')
    #     incorrect captcha
    #
    # else if errorMessage...
    # --------------------------------------------------------

    if flag.upper() == "NO":

        session.captcha_loaded = False
        session.captcha_verified = False

        raise PNRStatusError(
            "Captcha not matched"
        )

    if error_message:

        if error_message.lower() == (
            "captcha not matched"
        ).lower():

            session.captcha_loaded = False
            session.captcha_verified = False

            raise PNRStatusError(
                "Captcha not matched"
            )

        if (
            "session out" in error_message.lower()
            or "invalid request"
            in error_message.lower()
        ):

            raise PNRStatusError(
                error_message
            )

        raise PNRStatusError(
            error_message
        )

    # --------------------------------------------------------
    # Successful PNR response
    # --------------------------------------------------------

    if not payload.get("pnrNumber"):

        raise PNRStatusError(
            "No PNR data was returned."
        )

    session.captcha_verified = True
    session.last_used = time.time()

    return _normalize(payload)


# ============================================================
# CLOSE SESSION
# ============================================================

async def close_pnr_session(
    session_id: str,
) -> None:

    session = pnr_sessions.pop(
        session_id,
        None,
    )

    if session is not None:

        try:
            await session.client.aclose()  # type: ignore[attr-defined]  # type: ignore[attr-defined]
        except Exception:
            pass


# ============================================================
# CLEANUP
# ============================================================

async def cleanup_pnr_sessions() -> None:

    now = time.time()

    expired: list[str] = []

    for session_id, session in list(
        pnr_sessions.items()
    ):

        if (
            now - session.created_at
            > SESSION_TTL_SECONDS
        ):

            expired.append(session_id)

    for session_id in expired:

        await close_pnr_session(
            session_id
        )