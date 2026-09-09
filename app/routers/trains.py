import asyncio
import base64
import time
import uuid

from datetime import datetime
from typing import Any, Optional

import httpx
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from sqlalchemy import select
from dataclasses import dataclass, field
from app.database import AsyncSessionLocal
from app.models import Station


router = APIRouter(
    prefix="/api/trains",
    tags=["Trains"],
)


# ============================================================
# INDIAN RAILWAYS TBIS CONFIGURATION
# ============================================================

TBIS_PAGE_URL = (
    "https://www.indianrail.gov.in/"
    "enquiry/TBIS/TrainBetweenImportantStations.html?locale=en"
)

CAPTCHA_DRAW_URL = (
    "https://www.indianrail.gov.in/enquiry/captchaDraw.png"
)

COMMON_CAPTCHA_URL = (
    "https://www.indianrail.gov.in/enquiry/CommonCaptcha"
)

TBIS_REFERER = TBIS_PAGE_URL

USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/151.0.0.0 Safari/537.36"
)

SESSION_INACTIVITY_SECONDS = 20 * 60
CLEANUP_INTERVAL_SECONDS = 60


# ============================================================
# SESSION DATA
# ============================================================

@dataclass
class TrainSession:
    client: httpx.AsyncClient
    created_at: float
    last_used: float
    captcha_verified: bool = False
    availability_lock: asyncio.Lock = field(default_factory=asyncio.Lock)
        
    

# ============================================================
# IN-MEMORY SESSION STORE
# ============================================================

train_sessions: dict[str, TrainSession] = {}

train_sessions_lock = asyncio.Lock()
cleanup_task: Optional[asyncio.Task] = None


# ============================================================
# AVAILABILITY CACHE
# ============================================================

AVAILABILITY_CACHE_TTL_SECONDS = 120

@dataclass
class AvailabilityCacheEntry:
    data: dict[str, Any]
    fetched_at: float


availability_cache: dict[
    str,
    AvailabilityCacheEntry,
] = {}

availability_cache_lock = asyncio.Lock()


# ============================================================
# REQUEST MODELS
# ============================================================

class CaptchaRefreshRequest(BaseModel):
    session_id: str


class TrainSearchRequest(BaseModel):
    session_id: str
    captcha_answer: Optional[str] = None

    from_code: str
    from_name: str

    to_code: str
    to_name: str

    journey_date: str

    # NEW
    travel_class: str = "All Classes"
    quota: str = "General (GN)"


class TrainAvailabilityRequest(BaseModel):
    session_id: str

    train_number: str

    from_code: str
    to_code: str

    journey_date: str

    travel_class: str
    quota: str = "GN"

    train_type: Optional[str] = None


# ============================================================
# COMMON HTTP HEADERS
# ============================================================

def build_headers() -> dict[str, str]:
    return {
        "User-Agent": USER_AGENT,
        "Referer": TBIS_REFERER,
        "X-Requested-With": "XMLHttpRequest",
        "Accept": "*/*",
    }


# ============================================================
# CACHE BUSTING
# ============================================================

def timestamp_ms() -> str:
    return str(int(time.time() * 1000))


# ============================================================
# DATE CONVERSION
# ============================================================

def convert_date_to_dd_mm_yyyy(
    journey_date: str,
) -> str:

    try:
        parsed_date = datetime.strptime(
            journey_date,
            "%Y-%m-%d",
        )

    except ValueError:

        raise HTTPException(
            status_code=400,
            detail="journey_date must be in YYYY-MM-DD format.",
        )

    return parsed_date.strftime("%d-%m-%Y")


# ============================================================
# QUOTA NORMALIZATION
# ============================================================

def normalize_quota(quota: str) -> str:
    """
    Frontend may send:
        General (GN)
        GN

    Backend always sends:
        GN
    """

    value = (quota or "").strip()

    if not value:
        return "GN"

    if "(" in value and ")" in value:

        inside = value.rsplit("(", 1)[-1].split(")", 1)[0]

        if inside:
            return inside.strip().upper()

    return value.upper()


# ============================================================
# CLASS NORMALIZATION
# ============================================================

def normalize_travel_class(
    travel_class: str,
) -> Optional[str]:
    """
    Convert frontend class selection into IRCTC class code.

    Examples:
        2A -> 2A
        Sleeper (SL) -> SL
        All Classes -> None
    """

    value = (travel_class or "").strip()

    if not value:
        return None

    if value.lower() in {
        "all classes",
        "all",
        "all class",
    }:
        return None

    if "(" in value and ")" in value:

        inside = value.rsplit("(", 1)[-1].split(")", 1)[0]

        if inside:
            return inside.strip().upper()

    return value.upper()


# ============================================================
# CANONICAL STATION NAME
# ============================================================

async def get_canonical_station_name(
    station_code: str,
) -> str:

    code = station_code.strip().upper()

    async with AsyncSessionLocal() as db:

        result = await db.execute(
            select(Station.station_name).where(
                Station.station_code == code
            )
        )

        station_name = result.scalar_one_or_none()

    if not station_name:

        raise HTTPException(
            status_code=400,
            detail=f"Unknown station code: {code}",
        )

    return station_name.strip().upper()


# ============================================================
# SESSION LOOKUP
# ============================================================

async def get_session(
    session_id: str,
) -> Optional[TrainSession]:

    async with train_sessions_lock:
        return train_sessions.get(session_id)


# ============================================================
# REMOVE SESSION
# ============================================================

async def remove_session(
    session_id: str,
) -> Optional[httpx.AsyncClient]:

    async with train_sessions_lock:

        session = train_sessions.pop(
            session_id,
            None,
        )

        if session is None:
            return None

        return session.client


# ============================================================
# UPDATE SESSION
# ============================================================

async def mark_session_verified(
    session_id: str,
) -> bool:

    async with train_sessions_lock:

        session = train_sessions.get(
            session_id
        )

        if session is None:
            return False

        session.captcha_verified = True
        session.last_used = time.monotonic()

        return True


async def update_last_used(
    session_id: str,
) -> bool:

    async with train_sessions_lock:

        session = train_sessions.get(
            session_id
        )

        if session is None:
            return False

        session.last_used = time.monotonic()

        return True


# ============================================================
# CLEANUP EXPIRED SESSIONS
# ============================================================

async def cleanup_expired_sessions() -> None:

    now = time.monotonic()

    expired_clients: list[httpx.AsyncClient] = []

    async with train_sessions_lock:

        expired_session_ids = [
            session_id
            for session_id, session in train_sessions.items()
            if now - session.last_used
            > SESSION_INACTIVITY_SECONDS
        ]

        for session_id in expired_session_ids:

            session = train_sessions.pop(
                session_id,
                None,
            )

            if session is not None:
                expired_clients.append(
                    session.client
                )

    for client in expired_clients:

        try:
            await client.aclose()

        except Exception:
            pass


# ============================================================
# BACKGROUND CLEANUP LOOP
# ============================================================

async def cleanup_loop() -> None:

    while True:

        try:
            await cleanup_expired_sessions()

        except asyncio.CancelledError:
            raise

        except Exception:
            pass

        await asyncio.sleep(
            CLEANUP_INTERVAL_SECONDS
        )


async def start_cleanup_task() -> None:

    global cleanup_task

    if (
        cleanup_task is None
        or cleanup_task.done()
    ):

        cleanup_task = asyncio.create_task(
            cleanup_loop()
        )


async def stop_cleanup_task() -> None:

    global cleanup_task

    if cleanup_task is not None:

        cleanup_task.cancel()

        try:
            await cleanup_task

        except asyncio.CancelledError:
            pass

        cleanup_task = None

    clients_to_close: list[
        httpx.AsyncClient
    ] = []

    async with train_sessions_lock:

        for session in train_sessions.values():

            clients_to_close.append(
                session.client
            )

        train_sessions.clear()

    for client in clients_to_close:

        try:
            await client.aclose()

        except Exception:
            pass


# ============================================================
# CAPTCHA IMAGE
# ============================================================

async def fetch_captcha_image(
    client: httpx.AsyncClient,
) -> bytes:

    response = await client.get(
        CAPTCHA_DRAW_URL,
        params={
            "_": timestamp_ms(),
        },
        headers=build_headers(),
        timeout=20.0,
    )

    response.raise_for_status()

    if not response.content:

        raise HTTPException(
            status_code=502,
            detail=(
                "Indian Railways returned "
                "an empty captcha image."
            ),
        )

    return response.content


# ============================================================
# START CAPTCHA
# ============================================================

@router.post("/captcha/start")
async def start_captcha() -> dict[str, str]:

    client = httpx.AsyncClient(
        headers={
            "User-Agent": USER_AGENT,
        },
        follow_redirects=True,
        timeout=20.0,
    )

    try:

        tbis_response = await client.get(
            TBIS_PAGE_URL,
            headers={
                "User-Agent": USER_AGENT,
                "Accept": (
                    "text/html,application/xhtml+xml,"
                    "application/xml;q=0.9,*/*;q=0.8"
                ),
            },
        )

        tbis_response.raise_for_status()

        captcha_bytes = await fetch_captcha_image(
            client
        )

        session_id = str(uuid.uuid4())

        now = time.monotonic()

        session = TrainSession(
            client=client,
            created_at=now,
            last_used=now,
            captcha_verified=False,
        )

        async with train_sessions_lock:

            train_sessions[
                session_id
            ] = session

        captcha_base64 = base64.b64encode(
            captcha_bytes
        ).decode("ascii")

        return {
            "session_id": session_id,
            "captcha_image_base64": captcha_base64,
        }

    except HTTPException:

        await client.aclose()
        raise

    except httpx.HTTPError as exc:

        await client.aclose()

        raise HTTPException(
            status_code=502,
            detail=(
                "Unable to connect to Indian Railways "
                "for captcha."
            ),
        ) from exc

    except Exception as exc:

        await client.aclose()

        raise HTTPException(
            status_code=502,
            detail=(
                "Failed to initialize Indian Railways "
                "captcha session."
            ),
        ) from exc


# ============================================================
# REFRESH CAPTCHA
# ============================================================

@router.post("/captcha/refresh")
async def refresh_captcha(
    request: CaptchaRefreshRequest,
) -> dict[str, str]:

    session = await get_session(
        request.session_id
    )

    if session is None:

        raise HTTPException(
            status_code=404,
            detail=(
                "Session expired, please start "
                "a new captcha session."
            ),
        )

    now = time.monotonic()

    if (
        now - session.last_used
        > SESSION_INACTIVITY_SECONDS
    ):

        client = await remove_session(
            request.session_id
        )

        if client is not None:
            await client.aclose()

        raise HTTPException(
            status_code=404,
            detail=(
                "Session expired, please start "
                "a new captcha session."
            ),
        )

    try:

        captcha_bytes = await fetch_captcha_image(
            session.client
        )

        await update_last_used(
            request.session_id
        )

        captcha_base64 = base64.b64encode(
            captcha_bytes
        ).decode("ascii")

        return {
            "captcha_image_base64": captcha_base64,
        }

    except httpx.HTTPError as exc:

        client = await remove_session(
            request.session_id
        )

        if client is not None:
            await client.aclose()

        raise HTTPException(
            status_code=502,
            detail=(
                "Unable to refresh captcha from "
                "Indian Railways."
            ),
        ) from exc


# ============================================================
# TBIS RESPONSE CLASSIFICATION
# ============================================================

def classify_tbis_response(
    data: Any,
) -> str:

    if not isinstance(data, dict):
        return "UNKNOWN_ERROR"

    train_list = data.get(
        "trainBtwnStnsList"
    )

    if isinstance(train_list, list):
        return "SUCCESS"

    serialized = str(data).lower()

    captcha_words = (
        "captcha",
        "invalid captcha",
        "wrong captcha",
        "incorrect captcha",
    )

    for word in captcha_words:

        if word in serialized:
            return "INVALID_CAPTCHA"

    session_words = (
        "session expired",
        "session invalid",
        "invalid session",
        "login expired",
    )

    for word in session_words:

        if word in serialized:
            return "SESSION_INVALID"

    return "UNKNOWN_ERROR"


# ============================================================
# CLEAN TRAIN
# ============================================================

def clean_train(
    train: dict[str, Any],
) -> dict[str, Any]:

    return {
        "sNo": train.get("sNo"),
        "trainNumber": train.get("trainNumber"),
        "trainName": train.get("trainName"),
        "fromStnCode": train.get("fromStnCode"),
        "toStnCode": train.get("toStnCode"),
        "arrivalTime": train.get("arrivalTime"),
        "departureTime": train.get("departureTime"),
        "distance": train.get("distance"),
        "duration": train.get("duration"),
        "runningMon": train.get("runningMon"),
        "runningTue": train.get("runningTue"),
        "runningWed": train.get("runningWed"),
        "runningThu": train.get("runningThu"),
        "runningFri": train.get("runningFri"),
        "runningSat": train.get("runningSat"),
        "runningSun": train.get("runningSun"),
        "avlClasses": train.get(
            "avlClasses",
            [],
        ),
        "trainType": train.get(
            "trainType",
            [],
        ),
        "journeyDate": train.get(
            "journeyDate"
        ),
    }


# ============================================================
# AVAILABILITY STATUS PARSER
# ============================================================

def clean_availability_day(
    item: dict[str, Any],
) -> dict[str, Any]:

    return {
        "date": item.get("availablityDate"),
        "status": item.get("availablityStatus"),
        "reasonType": item.get("reasonType"),
        "reason": item.get("reason"),
        "availabilityType": item.get("availablityType"),
        "currentBookingFlag": item.get("currentBkgFlag"),
        "waitListType": item.get("waitListType"),
    }

def parse_availability_days(
    raw: dict[str, Any],
) -> list[dict[str, Any]]:

    raw_days = raw.get(
        "avlDayList",
        [],
    )

    if not isinstance(raw_days, list):
        return []

    days: list[dict[str, Any]] = []

    for item in raw_days:

        if not isinstance(item, dict):
            continue

        days.append(
            {
                "date": item.get(
                    "availablityDate"
                ),
                "status": item.get(
                    "availablityStatus"
                ),
                "reasonType": item.get(
                    "reasonType"
                ),
                "reason": item.get(
                    "reason"
                ),
                "availabilityType": item.get(
                    "availablityType"
                ),
                "currentBookingFlag": item.get(
                    "currentBkgFlag"
                ),
                "waitListType": item.get(
                    "waitListType"
                ),
            }
        )

    return days


# ============================================================
# AVAILABILITY RESULT STATUS
# ============================================================

def get_availability_result_status(
    days: list[dict[str, Any]],
) -> str:

    if not days:
        return "UNKNOWN"

    statuses = [
        str(day.get("status") or "").upper()
        for day in days
    ]

    if any(
        status.startswith("AVAILABLE")
        for status in statuses
    ):
        return "AVAILABLE"

    if any(
        "WL" in status
        or "RAC" in status
        for status in statuses
    ):
        return "WAITLIST"

    return "UNKNOWN"


# ============================================================
# FETCH ONE TRAIN AVAILABILITY
#
# EXACT NETWORK REQUEST:
#
# GET /enquiry/CommonCaptcha
#
# inputPage=TBIS_CALL_FOR_FARE
# trainNo=12238
# dt=07-09-2026
# sourceStation=UMB
# destinationStation=BSB
# classc=2A
# quota=GN
# traintype=SUP
# language=en
# ============================================================

async def fetch_train_availability(
    session: TrainSession,
    train: dict[str, Any],
    journey_date: str,
    quota: str,
    class_code: str,
) -> dict[str, Any]:

    train_number = str(
        train.get("trainNumber") or ""
    ).strip()

    source_station = str(
        train.get("fromStnCode") or ""
    ).strip().upper()

    destination_station = str(
        train.get("toStnCode") or ""
    ).strip().upper()

    train_types = train.get(
        "trainType",
        [],
    )

    train_type = ""

    if isinstance(train_types, list) and train_types:

        train_type = str(
            train_types[0]
        ).strip().upper()

    elif isinstance(train_types, str):

        train_type = train_types.strip().upper()

    formatted_date = convert_date_to_dd_mm_yyyy(
        journey_date
    )

    params = {
        "inputPage": "TBIS_CALL_FOR_FARE",
        "trainNo": train_number,
        "dt": formatted_date,
        "sourceStation": source_station,
        "destinationStation": destination_station,
        "classc": class_code,
        "quota": quota,
        "traintype": train_type,
        "language": "en",
        "_": timestamp_ms(),
    }

    # print(
    #     "\n========== AVAILABILITY REQUEST =========="
    # )
    # print(
    #     "TRAIN:",
    #     train_number,
    # )
    # print(
    #     "CLASS:",
    #     class_code,
    # )
    # print(
    #     "QUOTA:",
    #     quota,
    # )
    # print(
    #     "SOURCE:",
    #     source_station,
    # )
    # print(
    #     "DESTINATION:",
    #     destination_station,
    # )
    # print(
    #     "TRAIN TYPE:",
    #     train_type,
    # )
    # print(
    #     "DATE:",
    #     formatted_date,
    # )
    # print(
    #     "PARAMS:",
    #     params,
    # )
    # print(
    #     "==========================================\n"
    # )

    try:

        response = await session.client.get(
            COMMON_CAPTCHA_URL,
            params=params,
            headers=build_headers(),
            timeout=30.0,
        )

        response.raise_for_status()

    except httpx.HTTPError as exc:

        # print(
        #     "AVAILABILITY HTTP ERROR:",
        #     repr(exc),
        # )

        return {
            "class": class_code,
            "quota": quota,
            "raw": None,
            "days": [],
            "result": {
                "status": "ERROR",
                "text": str(exc),
                "raw": None,
            },
        }

    try:

        data = response.json()

    except ValueError:

        return {
            "class": class_code,
            "quota": quota,
            "raw": None,
            "days": [],
            "result": {
                "status": "ERROR",
                "text": (
                    "Indian Railways returned "
                    "invalid JSON."
                ),
                "raw": None,
            },
        }

    # print(
    #     "\n========== AVAILABILITY RESPONSE =========="
    # )
    # print(
    #     "HTTP STATUS:",
    #     response.status_code,
    # )
    # print(
    #     "TRAIN:",
    #     train_number,
    # )
    # print(
    #     "CLASS:",
    #     class_code,
    # )
    # print(
    #     "RESPONSE:",
    #     data,
    # )
    # print(
    #     "============================================\n"
    # )

    # --------------------------------------------------------
    # Upstream station validation
    # --------------------------------------------------------

    if (
        isinstance(data, dict)
        and data.get("errorMessage")
    ):

        error_message = str(
            data.get("errorMessage")
        )

        return {
            "class": class_code,
            "quota": quota,
            "raw": data,
            "days": [],
            "result": {
                "status": "ERROR",
                "text": error_message,
                "raw": data,
            },
        }

    days = parse_availability_days(
        data
        if isinstance(data, dict)
        else {}
    )

    status = get_availability_result_status(
        days
    )

    return {
        "class": class_code,
        "quota": quota,
        "raw": data,
        "days": days,
        "result": {
            "status": status,
            "text": None,
            "raw": None,
        },
    }


# ============================================================
# FETCH AVAILABILITY FOR TRAIN
# ============================================================

async def fetch_availability_for_train(
    session: TrainSession,
    train: dict[str, Any],
    journey_date: str,
    quota: str,
    requested_class: Optional[str],
) -> dict[str, Any]:

    available_classes = train.get(
        "avlClasses",
        [],
    )

    if not isinstance(
        available_classes,
        list,
    ):
        available_classes = []

    normalized_classes = []

    for item in available_classes:

        class_code = str(
            item
        ).strip().upper()

        if class_code and class_code not in normalized_classes:

            normalized_classes.append(
                class_code
            )

    # --------------------------------------------------------
    # If user selected a particular class,
    # only request that class if train supports it.
    # --------------------------------------------------------

    if requested_class:

        if requested_class not in normalized_classes:

            return {}

        classes_to_query = [
            requested_class
        ]

    else:

        classes_to_query = normalized_classes

    availability: dict[str, Any] = {}

    # --------------------------------------------------------
    # Sequential requests intentionally.
    #
    # Same Indian Railways session/client is used.
    # This is safer than firing many requests concurrently.
    # --------------------------------------------------------

    for class_code in classes_to_query:

        result = await fetch_train_availability(
            session=session,
            train=train,
            journey_date=journey_date,
            quota=quota,
            class_code=class_code,
        )

        availability[
            class_code
        ] = result

        await asyncio.sleep(0.10)

    return availability


# ============================================================
# SEARCH TRAINS
# ============================================================

@router.post("/search")
async def search_trains(
    request: TrainSearchRequest,
) -> dict[str, Any]:

    # --------------------------------------------------------
    # 1. Session
    # --------------------------------------------------------

    session = await get_session(
        request.session_id
    )

    if session is None:

        raise HTTPException(
            status_code=401,
            detail=(
                "Session expired, please solve "
                "captcha again."
            ),
        )

    # --------------------------------------------------------
    # 2. Expiration
    # --------------------------------------------------------

    now = time.monotonic()

    if (
        now - session.last_used
        > SESSION_INACTIVITY_SECONDS
    ):

        client = await remove_session(
            request.session_id
        )

        if client is not None:
            await client.aclose()

        raise HTTPException(
            status_code=401,
            detail=(
                "Session expired, please solve "
                "captcha again."
            ),
        )

    # --------------------------------------------------------
    # 3. CAPTCHA
    # --------------------------------------------------------

    if (
        not session.captcha_verified
        and not request.captcha_answer
    ):

        raise HTTPException(
            status_code=400,
            detail="Captcha answer required.",
        )

    # --------------------------------------------------------
    # 4. Date
    # --------------------------------------------------------

    formatted_date = convert_date_to_dd_mm_yyyy(
        request.journey_date
    )

    # --------------------------------------------------------
    # 5. Station names
    # --------------------------------------------------------

    from_station_name = (
        await get_canonical_station_name(
            request.from_code
        )
    )

    to_station_name = (
        await get_canonical_station_name(
            request.to_code
        )
    )

    from_code = request.from_code.strip().upper()
    to_code = request.to_code.strip().upper()

    source_station = (
        f"{from_station_name} - {from_code}"
    )

    destination_station = (
        f"{to_station_name} - {to_code}"
    )

    # print(
    #     "TBIS SOURCE EXACT:",
    #     repr(source_station),
    # )
    #
    # print(
    #     "TBIS DESTINATION EXACT:",
    #     repr(destination_station),
    # )
    #
    # print(
    #     "\n========== TBIS STATION DEBUG =========="
    # )
    #
    # print(
    #     "FRONTEND FROM NAME:",
    #     request.from_name,
    # )
    #
    # print(
    #     "FRONTEND FROM CODE:",
    #     request.from_code,
    # )
    #
    # print(
    #     "TBIS SOURCE:",
    #     source_station,
    # )
    #
    # print()
    #
    # print(
    #     "FRONTEND TO NAME:",
    #     request.to_name,
    # )
    #
    # print(
    #     "FRONTEND TO CODE:",
    #     request.to_code,
    # )
    #
    # print(
    #     "TBIS DESTINATION:",
    #     destination_station,
    # )
    #
    # print(
    #     "========================================\n"
    # )

    # --------------------------------------------------------
    # 6. TBIS train search params
    # --------------------------------------------------------

    params = {
        "inputCaptcha": (
            request.captcha_answer
            if not session.captcha_verified
            else ""
        ),
        "dt": formatted_date,
        "sourceStation": source_station,
        "destinationStation": destination_station,
        "flexiWithDate": "y",
        "inputPage": "TBIS",
        "language": "en",
        "_": timestamp_ms(),
    }

    # --------------------------------------------------------
    # 7. Train search
    # --------------------------------------------------------

    try:

        response = await session.client.get(
            COMMON_CAPTCHA_URL,
            params=params,
            headers=build_headers(),
            timeout=30.0,
        )

        response.raise_for_status()

    except httpx.HTTPStatusError as exc:

        raise HTTPException(
            status_code=502,
            detail=(
                "Indian Railways returned an HTTP "
                "error while searching trains."
            ),
        ) from exc

    except httpx.HTTPError as exc:

        raise HTTPException(
            status_code=502,
            detail=(
                "Unable to connect to Indian Railways "
                "for train search."
            ),
        ) from exc

    # --------------------------------------------------------
    # 8. JSON
    # --------------------------------------------------------

    try:

        data = response.json()

    except ValueError as exc:

        raise HTTPException(
            status_code=502,
            detail=(
                "Indian Railways returned an invalid "
                "response."
            ),
        ) from exc

    # --------------------------------------------------------
    # 9. Debug output intentionally disabled.
    # --------------------------------------------------------

    result_type = classify_tbis_response(
        data
    )

    # --------------------------------------------------------
    # INVALID CAPTCHA
    # --------------------------------------------------------

    if result_type == "INVALID_CAPTCHA":

        raise HTTPException(
            status_code=400,
            detail=(
                "Invalid captcha, please try again."
            ),
        )

    # --------------------------------------------------------
    # SESSION INVALID
    # --------------------------------------------------------

    if result_type == "SESSION_INVALID":

        client = await remove_session(
            request.session_id
        )

        if client is not None:
            await client.aclose()

        raise HTTPException(
            status_code=401,
            detail=(
                "Session expired, please solve "
                "captcha again."
            ),
        )

    # --------------------------------------------------------
    # UNKNOWN
    # --------------------------------------------------------

    if result_type != "SUCCESS":

        raise HTTPException(
            status_code=502,
            detail=(
                "Indian Railways returned an unexpected "
                "train-search response."
            ),
        )

    # --------------------------------------------------------
    # 10. Train list
    # --------------------------------------------------------

    train_list = data.get(
        "trainBtwnStnsList",
        [],
    )

    cleaned_trains = []

    for train in train_list:

        if not isinstance(train, dict):
            continue

        cleaned_trains.append(
            clean_train(train)
        )

    # --------------------------------------------------------
    # 11. CAPTCHA verified
    # --------------------------------------------------------

    verified = await mark_session_verified(
        request.session_id
    )

    if not verified:

        raise HTTPException(
            status_code=401,
            detail=(
                "Session expired, please solve "
                "captcha again."
            ),
        )

    return {
        "trains": cleaned_trains,
    }

# ============================================================
# BUILD AVAILABILITY CACHE KEY
# ============================================================

def build_availability_cache_key(
    request: TrainAvailabilityRequest,
) -> str:

    return "|".join(
        [
            request.train_number.strip(),
            request.from_code.strip().upper(),
            request.to_code.strip().upper(),
            request.journey_date.strip(),
            request.travel_class.strip().upper(),
            request.quota.strip().upper(),
            (
                request.train_type.strip().upper()
                if request.train_type
                else ""
            ),
        ]
    )


    

# ============================================================
# TRAIN AVAILABILITY
#
# IMPORTANT:
# This endpoint is intentionally lazy.
#
# Train search DOES NOT call this endpoint automatically.
#
# Frontend calls this only after the user selects:
#   train + class + quota
# ============================================================

@router.post("/availability")
async def get_train_availability(
    request: TrainAvailabilityRequest,
) -> dict[str, Any]:

    # --------------------------------------------------------
    # 1. Validate session
    # --------------------------------------------------------

    session = await get_session(
        request.session_id
    )

    if session is None:

        raise HTTPException(
            status_code=401,
            detail=(
                "Session expired, please solve "
                "captcha again."
            ),
        )

    # --------------------------------------------------------
    # 2. Validate inactivity
    # --------------------------------------------------------

    now = time.monotonic()

    if (
        now - session.last_used
        > SESSION_INACTIVITY_SECONDS
    ):

        client = await remove_session(
            request.session_id
        )

        if client is not None:
            await client.aclose()

        raise HTTPException(
            status_code=401,
            detail=(
                "Session expired, please solve "
                "captcha again."
            ),
        )

    # --------------------------------------------------------
    # 3. Validate basic values
    # --------------------------------------------------------

    train_number = request.train_number.strip()

    from_code = request.from_code.strip().upper()
    to_code = request.to_code.strip().upper()

    travel_class = request.travel_class.strip().upper()
    quota = request.quota.strip().upper()

    if not train_number:
        raise HTTPException(
            status_code=400,
            detail="train_number is required.",
        )

    if not from_code or not to_code:
        raise HTTPException(
            status_code=400,
            detail="from_code and to_code are required.",
        )

    if not travel_class:
        raise HTTPException(
            status_code=400,
            detail="travel_class is required.",
        )

    if not quota:
        raise HTTPException(
            status_code=400,
            detail="quota is required.",
        )

    # --------------------------------------------------------
    # 4. Validate date
    # --------------------------------------------------------

    formatted_date = convert_date_to_dd_mm_yyyy(
        request.journey_date
    )

    # --------------------------------------------------------
    # 5. Validate station codes against DB
    # --------------------------------------------------------

    await get_canonical_station_name(
        from_code
    )

    await get_canonical_station_name(
        to_code
    )

    # --------------------------------------------------------
    # 6. Build cache key
    # --------------------------------------------------------

    cache_key = build_availability_cache_key(
        request
    )

    # --------------------------------------------------------
    # 7. Return fresh cached result if available
    # --------------------------------------------------------

    now_monotonic = time.monotonic()

    async with availability_cache_lock:

        cached = availability_cache.get(
            cache_key
        )

        if (
            cached is not None
            and (
                now_monotonic
                - cached.fetched_at
                < AVAILABILITY_CACHE_TTL_SECONDS
            )
        ):

            await update_last_used(
                request.session_id
            )

            return cached.data

    # --------------------------------------------------------
    # 8. IMPORTANT:
    #
    # Serialize availability calls belonging to this
    # Indian Railways HTTP session.
    #
    # No Promise.all / parallel upstream calls.
    # --------------------------------------------------------

    async with session.availability_lock:

        # ----------------------------------------------------
        # Check cache AGAIN after acquiring lock.
        #
        # Another request may have populated it while this
        # request was waiting for the lock.
        # ----------------------------------------------------

        now_monotonic = time.monotonic()

        async with availability_cache_lock:

            cached = availability_cache.get(
                cache_key
            )

            if (
                cached is not None
                and (
                    now_monotonic
                    - cached.fetched_at
                    < AVAILABILITY_CACHE_TTL_SECONDS
                )
            ):

                await update_last_used(
                    request.session_id
                )

                return cached.data

        # ----------------------------------------------------
        # 9. Build Indian Railways CommonCaptcha request
        # ----------------------------------------------------

        params = {
            "inputPage": "TBIS_CALL_FOR_FARE",
            "trainNo": train_number,
            "dt": formatted_date,
            "sourceStation": from_code,
            "destinationStation": to_code,
            "classc": travel_class,
            "quota": quota,
            "traintype": (
                request.train_type.strip().upper()
                if request.train_type
                else ""
            ),
            "language": "en",
            "_": timestamp_ms(),
        }

        # print(
        #     "\n========== TBIS AVAILABILITY REQUEST =========="
        # )
        # print("TRAIN:", train_number)
        # print("FROM:", from_code)
        # print("TO:", to_code)
        # print("DATE:", formatted_date)
        # print("CLASS:", travel_class)
        # print("QUOTA:", quota)
        # print("TRAIN TYPE:", params["traintype"])
        # print("===============================================\n")

        # ----------------------------------------------------
        # 10. Call Indian Railways
        # ----------------------------------------------------

        try:

            response = await session.client.get(
                COMMON_CAPTCHA_URL,
                params=params,
                headers=build_headers(),
                timeout=30.0,
            )

            response.raise_for_status()

        except httpx.HTTPStatusError as exc:

            raise HTTPException(
                status_code=502,
                detail=(
                    "Indian Railways returned an HTTP error "
                    "while fetching train availability."
                ),
            ) from exc

        except httpx.HTTPError as exc:

            raise HTTPException(
                status_code=502,
                detail=(
                    "Unable to connect to Indian Railways "
                    "for train availability."
                ),
            ) from exc

        # ----------------------------------------------------
        # 11. Parse JSON
        # ----------------------------------------------------

        try:

            data = response.json()

        except ValueError as exc:

            raise HTTPException(
                status_code=502,
                detail=(
                    "Indian Railways returned an invalid "
                    "availability response."
                ),
            ) from exc

        # print(
        #     "\n========== TBIS AVAILABILITY RESPONSE =========="
        # )
        # print(data)
        # print("==================================================\n")

        # ----------------------------------------------------
        # 12. Handle upstream error
        # ----------------------------------------------------

        if not isinstance(data, dict):

            raise HTTPException(
                status_code=502,
                detail=(
                    "Indian Railways returned an unexpected "
                    "availability response."
                ),
            )

        error_message = data.get(
            "errorMessage"
        )

        if error_message:

            error_text = str(
                error_message
            )

            lower_error = error_text.lower()

            if "captcha" in lower_error:

                raise HTTPException(
                    status_code=400,
                    detail=error_text,
                )

            if (
                "session" in lower_error
                or "login" in lower_error
            ):

                client = await remove_session(
                    request.session_id
                )

                if client is not None:
                    await client.aclose()

                raise HTTPException(
                    status_code=401,
                    detail=(
                        "Session expired, please solve "
                        "captcha again."
                    ),
                )

            raise HTTPException(
                status_code=502,
                detail=error_text,
            )

        # ----------------------------------------------------
        # 13. Extract complete availability days
        # ----------------------------------------------------

        raw_days = data.get(
            "avlDayList",
            [],
        )

        if not isinstance(raw_days, list):

            raw_days = []

        days = [
            clean_availability_day(day)
            for day in raw_days
            if isinstance(day, dict)
        ]

        # ----------------------------------------------------
        # 14. Determine result status
        # ----------------------------------------------------

        has_available = any(
            isinstance(day.get("status"), str)
            and day["status"].upper().startswith(
                "AVAILABLE"
            )
            for day in days
        )

        if has_available:
            result_status = "AVAILABLE"

        elif days:
            result_status = "WAITLIST"

        else:
            result_status = "UNKNOWN"

        # ----------------------------------------------------
        # 15. Actual fetch timestamp
        # ----------------------------------------------------

        fetched_at = datetime.now().astimezone().isoformat()

        # ----------------------------------------------------
        # 16. Return clean but complete response
        # ----------------------------------------------------

        result = {
            "trainNumber": train_number,
            "trainName": data.get("trainName"),
            "from": data.get("from", from_code),
            "to": data.get("to", to_code),
            "journeyDate": request.journey_date,
            "class": travel_class,
            "quota": data.get(
                "quota",
                quota,
            ),
            "trainType": data.get(
                "trainTypeCode",
                params["traintype"],
            ),

            "fetchedAt": fetched_at,

            "result": {
                "status": result_status,
                "text": None,
            },

            "days": days,

            # Keep fare/booking information because the
            # upstream response provides it and the frontend
            # may need it later.
            "fare": {
                "baseFare": data.get("baseFare"),
                "reservationCharge": data.get(
                    "reservationCharge"
                ),
                "superfastCharge": data.get(
                    "superfastCharge"
                ),
                "goodsServiceTax": data.get(
                    "goodsServiceTax"
                ),
                "totalFare": data.get(
                    "totalFare"
                ),
                "totalCollectibleAmount": data.get(
                    "totalCollectibleAmount"
                ),
                "dynamicFare": data.get(
                    "dynamicFare"
                ),
                "tatkalFare": data.get(
                    "tatkalFare"
                ),
                "cateringCharge": data.get(
                    "cateringCharge"
                ),
                "otherCharge": data.get(
                    "otherCharge"
                ),
            },

            "bookingConfig": data.get(
                "bkgCfg"
            ),
        }

        # ----------------------------------------------------
        # 17. Cache result
        # ----------------------------------------------------

        async with availability_cache_lock:

            availability_cache[cache_key] = (
                AvailabilityCacheEntry(
                    data=result,
                    fetched_at=time.monotonic(),
                )
            )

        # ----------------------------------------------------
        # 18. Update session activity
        # ----------------------------------------------------

        verified = await update_last_used(
            request.session_id
        )

        if not verified:

            raise HTTPException(
                status_code=401,
                detail=(
                    "Session expired, please solve "
                    "captcha again."
                ),
            )

        return result