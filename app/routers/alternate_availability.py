import asyncio
import json
import os
import time
import uuid
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Optional

import httpx
from fastapi import APIRouter, HTTPException
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from app.routers.trains import (
    fetch_train_availability,
    get_session,
    normalize_quota,
    normalize_travel_class,
    update_last_used,
)

from app.services.redis_cache import (cache_get, cache_set,cache_delete,)
    

router = APIRouter(
    prefix="/api/trains/alternate",
    tags=["Alternate Availability"],
)


# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

NTES_ROUTE_SERVICE_URL = os.getenv(
    "NTES_ROUTE_SERVICE_URL",
    "https://railway-ntes-402829987485.asia-south1.run.app",
)

ROUTE_REQUEST_TIMEOUT = 45.0

# Completed jobs are kept for this long so that frontend can reconnect
# to the SSE stream/status endpoint.
JOB_TTL_SECONDS = 30 * 60

# Small delay between upstream availability requests.
# The existing session availability_lock also ensures that requests
# belonging to the same IRCTC session are serialized.
REQUEST_DELAY_SECONDS = 0.10



# Completed alternate availability results stay in Redis
# for 10 minutes.
ALTERNATE_REDIS_CACHE_TTL_SECONDS = 10 * 60

# ---------------------------------------------------------------------------
# Request / Job models
# ---------------------------------------------------------------------------

class AlternateAvailabilityRequest(BaseModel):
    session_id: str = Field(..., min_length=1)

    train_number: str = Field(
        ...,
        min_length=5,
        max_length=5,
        pattern=r"^\d{5}$",
    )

    from_code: str = Field(..., min_length=1, max_length=10)
    to_code: str = Field(..., min_length=1, max_length=10)

    journey_date: str = Field(..., min_length=1, max_length=20)

    # Example:
    # "SL"
    # "Sleeper (SL)"
    travel_class: str = Field(..., min_length=1, max_length=50)

    # Example:
    # "GN"
    # "General (GN)"
    quota: str = Field(default="GN", min_length=1, max_length=50)

    # This comes from the selected train returned by train search.
    # Example: "SUP"
    train_type: Optional[str] = Field(
        default=None,
        max_length=50,
    )

    # Frontend can send the already-known status of the selected
    # train/class availability.
    #
    # If CNF/RAC is supplied, backend will refuse to start alternate
    # searching because it is unnecessary.
    initial_status: Optional[str] = Field(
        default=None,
        max_length=100,
    )

    # If true, bypass the completed Redis cache
    # and start a fresh alternate availability search.
    force_refresh: bool = False


@dataclass
class AlternateJob:
    job_id: str

    created_at: float = field(default_factory=time.time)

    status: str = "starting"

    total: int = 0
    checked: int = 0
    found: int = 0
    errors: int = 0

    error: Optional[str] = None

    # All generated SSE events are retained so a client connecting
    # slightly late can still receive earlier results.
    events: list[dict[str, Any]] = field(default_factory=list)

    condition: asyncio.Condition = field(
        default_factory=asyncio.Condition
    )

    task: Optional[asyncio.Task] = None


# ---------------------------------------------------------------------------
# In-memory job storage
# ---------------------------------------------------------------------------

alternate_jobs: dict[str, AlternateJob] = {}

alternate_jobs_lock = asyncio.Lock()


# ---------------------------------------------------------------------------
# Utility helpers
# ---------------------------------------------------------------------------

def _normalize_code(value: str) -> str:
    return str(value or "").strip().upper()


def _build_alternate_redis_cache_key(
    request: AlternateAvailabilityRequest,
) -> str:

    return ":".join(
        [
            "alternate",
            request.train_number.strip(),
            _normalize_code(request.from_code),
            _normalize_code(request.to_code),
            request.journey_date.strip(),
            normalize_travel_class(
                request.travel_class
            ) or "",
            normalize_quota(
                request.quota
            ),
            (
                request.train_type.strip().upper()
                if request.train_type
                else ""
            ),
        ]
    )


async def _find_running_alternate_job(
    redis_cache_key: str,
) -> Optional[str]:

    running_key = (
        f"{redis_cache_key}:running"
    )

    running_job_id = await cache_get(
        running_key
    )

    if not isinstance(
        running_job_id,
        str,
    ):
        return None

    job = alternate_jobs.get(
        running_job_id
    )

    if job is None:
        # Stale Redis running-job reference.
        await cache_set(
            running_key,
            "",
            ttl_seconds=1,
        )
        return None

    if job.status in {
        "completed",
        "failed",
        "cancelled",
    }:
        return None

    return running_job_id


def _to_ntes_date(value: str) -> str:
    """
    Main backend normally receives YYYY-MM-DD.

    Route service accepts DD-Mon-YYYY.
    """

    value = str(value or "").strip()

    try:
        return datetime.strptime(
            value,
            "%Y-%m-%d",
        ).strftime("%d-%b-%Y")
    except ValueError:
        pass

    # Already in NTES format.
    try:
        return datetime.strptime(
            value,
            "%d-%b-%Y",
        ).strftime("%d-%b-%Y")
    except ValueError:
        raise HTTPException(
            status_code=400,
            detail=(
                "Invalid journey_date. Expected YYYY-MM-DD "
                "or DD-Mon-YYYY."
            ),
        )


async def _cleanup_old_jobs() -> None:
    now = time.time()

    async with alternate_jobs_lock:
        expired_ids = []

        for job_id, job in alternate_jobs.items():
            if (
                job.status in {"completed", "failed", "cancelled"}
                and now - job.created_at > JOB_TTL_SECONDS
            ):
                expired_ids.append(job_id)

        for job_id in expired_ids:
            job = alternate_jobs.pop(job_id)

            if job.task and not job.task.done():
                job.task.cancel()


async def _publish_event(
    job: AlternateJob,
    event_name: str,
    data: dict[str, Any],
) -> None:
    event = {
        "event": event_name,
        "data": data,
    }

    async with job.condition:
        job.events.append(event)
        job.condition.notify_all()


def _sse_encode(event: dict[str, Any]) -> str:
    event_name = event["event"]
    data = event["data"]

    return (
        f"event: {event_name}\n"
        f"data: {json.dumps(data, ensure_ascii=False)}\n\n"
    )


# ---------------------------------------------------------------------------
# Route service
# ---------------------------------------------------------------------------

async def _fetch_train_route(
    train_number: str,
    journey_date: str,
) -> dict[str, Any]:
    """
    Calls the already deployed NTES route service.

    GET:
        /train/route/{train_number}
    """

    base_url = NTES_ROUTE_SERVICE_URL.rstrip("/")

    url = (
        f"{base_url}/train/route/"
        f"{train_number}"
    )

    # Keep the existing behavior: route service works without
    # requiring the journey_date query parameter.
    #
    # The route service itself uses the requested date when supplied,
    # but the current deployed endpoint has already been verified
    # both with and without the query parameter.
    params = {}

    try:
        async with httpx.AsyncClient(
            timeout=ROUTE_REQUEST_TIMEOUT,
            follow_redirects=True,
        ) as client:

            response = await client.get(
                url,
                params=params,
            )

        response.raise_for_status()

    except httpx.HTTPError as exc:

        # Keep technical NTES/HTTP error only in server logs.
        print(
            "ALTERNATE ROUTE REQUEST ERROR:",
            repr(exc),
        )

        raise HTTPException(
            status_code=502,
            detail=(
                "Unable to fetch train route "
                "right now. Please try again."
            ),
        ) from exc

    try:
        payload = response.json()
    except ValueError as exc:
        raise HTTPException(
            status_code=502,
            detail="NTES route service returned invalid JSON.",
        ) from exc

    if not isinstance(payload, dict):
        raise HTTPException(
            status_code=502,
            detail="Invalid response from NTES route service.",
        )

    if payload.get("success") is False:

        # Keep the technical upstream message only in server logs.
        print(
            "ALTERNATE ROUTE SERVICE ERROR:",
            payload.get("message"),
        )

        raise HTTPException(
            status_code=502,
            detail=(
                "Unable to fetch train route "
                "right now. Please try again."
            ),
        )

    data = payload.get("data")

    if not isinstance(data, dict):
        raise HTTPException(
            status_code=502,
            detail="NTES route service returned no route data.",
        )

    return data


def _extract_bookable_stations(
    route_data: dict[str, Any],
) -> list[dict[str, Any]]:
    """
    Only actual stopping/bookable stations are used for
    alternate availability combinations.
    """

    stations = route_data.get("bookable_stations")

    if not isinstance(stations, list):
        raise HTTPException(
            status_code=502,
            detail=(
                "NTES route response does not contain "
                "bookable_stations."
            ),
        )

    result: list[dict[str, Any]] = []

    for station in stations:
        if not isinstance(station, dict):
            continue

        raw_code = station.get("station_code")

        if not isinstance(raw_code, str):
            continue

        code = _normalize_code(raw_code)

        name = str(
            station.get("station_name") or ""
        ).strip()

        if not code:
            continue

        result.append(
            {
                "sequence": station.get("sequence"),
                "station_code": code,
                "station_name": name,
                "distance_km": station.get("distance_km"),
            }
        )

    if len(result) < 2:
        raise HTTPException(
            status_code=422,
            detail=(
                "Train route does not contain enough "
                "bookable stations."
            ),
        )

    return result


# ---------------------------------------------------------------------------
# Candidate station pairs
# ---------------------------------------------------------------------------

def _build_station_pairs(
    stations: list[dict[str, Any]],
    original_from: str,
    original_to: str,
) -> list[dict[str, Any]]:
    """
    Generate every forward station pair from the complete train route.

    Example:

        A -> B
        A -> C
        A -> D
        B -> C
        B -> D
        C -> D

    This intentionally checks the complete route, not only the user's
    original journey segment.

    Therefore stations after the original destination are also included.
    """

    original_from = _normalize_code(original_from)
    original_to = _normalize_code(original_to)

    source_index: Optional[int] = None
    destination_index: Optional[int] = None

    for index, station in enumerate(stations):
        code = _normalize_code(
            station["station_code"]
        )

        if code == original_from:
            source_index = index

        if code == original_to:
            destination_index = index

    if source_index is None:
        raise HTTPException(
            status_code=422,
            detail=(
                f"Original source station {original_from} "
                "was not found in the train route."
            ),
        )

    if destination_index is None:
        raise HTTPException(
            status_code=422,
            detail=(
                f"Original destination station {original_to} "
                "was not found in the train route."
            ),
        )

    if source_index >= destination_index:
        raise HTTPException(
            status_code=422,
            detail=(
                "Original source station must occur before "
                "destination station in the train route."
            ),
        )

    pairs: list[dict[str, Any]] = []




    for i in range(len(stations)):
        for j in range(i + 1, len(stations)):

            from_station = stations[i]
            to_station = stations[j]

            from_code = _normalize_code(
                from_station["station_code"]
            )

            to_code = _normalize_code(
                to_station["station_code"]
            )


            # Original WL journey was already checked.
            if (
                from_code == original_from
                and to_code == original_to
            ):
                continue

            pairs.append(
                {
                    "from": from_station,
                    "to": to_station,
                    "from_index": i,
                    "to_index": j,
                }
            )

    # Put the most useful alternatives first.
    #
    # Priority:
    #   0 = same original source, different destination
    #   1 = different source, same original destination
    #   2 = nearby source + nearby destination
    #   3 = remaining route combinations
    #
    # This keeps ALL pairs. It only changes the order in which
    # availability requests are made.
    def _pair_priority(item: dict[str, Any]) -> tuple[int, int, int, int]:
        from_index = item["from_index"]
        to_index = item["to_index"]

        same_source = from_index == source_index
        same_destination = to_index == destination_index

        if same_source and not same_destination:
            priority = 0
        elif not same_source and same_destination:
            priority = 1
        elif not same_source and not same_destination:
            priority = 2
        else:
            priority = 3

        distance = (
            abs(from_index - source_index)
            + abs(to_index - destination_index)
        )

        return (priority, distance, from_index, to_index)

    pairs.sort(key=_pair_priority)

    return pairs


# ---------------------------------------------------------------------------
# Availability classification
# ---------------------------------------------------------------------------

def _normalize_availability_date(value: Any) -> Optional[str]:
    """
    Normalize availability day date into YYYY-MM-DD.

    Supported examples:
        2026-09-27
        27-Sep-2026
        27-Sep-26
        27/09/2026
        27-09-2026
    """

    if value is None:
        return None

    value = str(value).strip()

    if not value:
        return None

    formats = (
        "%Y-%m-%d",
        "%d-%b-%Y",
        "%d-%b-%y",
        "%d/%m/%Y",
        "%d-%m-%Y",
    )

    for date_format in formats:
        try:
            return datetime.strptime(
                value,
                date_format,
            ).strftime("%Y-%m-%d")
        except ValueError:
            continue

    return None


def _classify_available_result(
    result: dict[str, Any],
    journey_date: str,
) -> Optional[dict[str, Any]]:
    """
    Convert the raw availability response into an alternate result.

    IMPORTANT:
    Only the availability day matching the requested journey_date
    is considered.

    Only CNF/confirmed availability and RAC are returned.

    WL/PQWL/RLWL/etc. are ignored.
    """

    days = result.get("days")

    # print("========== ALTERNATE DEBUG ==========")
    # print("REQUESTED JOURNEY DATE:", journey_date)
    # print("AVAILABILITY DAYS:", json.dumps(days, indent=2, ensure_ascii=False))
    # print("======================================")

    if not isinstance(days, list):
        return None

    # Normalize requested date once.
    requested_date = _normalize_availability_date(journey_date)

    if not requested_date:
        return None

    # IMPORTANT:
    # Do NOT return the first AVAILABLE/RAC day.
    #
    # The upstream response may contain availability for multiple
    # dates. We must only inspect the requested journey date.
    requested_day = None

    for day in days:
        if not isinstance(day, dict):
            continue

        day_date = _normalize_availability_date(
            day.get("date")
        )

        if day_date == requested_date:
            requested_day = day
            break

    # Requested journey date was not present in the upstream response.
    if requested_day is None:
        return None

    raw_status = str(
        requested_day.get("status") or ""
    ).strip()

    status_upper = raw_status.upper()

    if not status_upper:
        return None

    # RAC
    if "RAC" in status_upper:
        return {
            "status": "RAC",
            "availability": raw_status,
            "day": requested_day,
        }

    # Confirmed / available
    if (
        status_upper.startswith("AVAILABLE")
        or status_upper.startswith("AVL")
        or "CNF" in status_upper
        or "CONFIRM" in status_upper
    ):
        return {
            "status": "CNF",
            "availability": raw_status,
            "day": requested_day,
        }

    return None


# ---------------------------------------------------------------------------
# Background alternate search
# ---------------------------------------------------------------------------

async def _run_alternate_job(
    job: AlternateJob,
    request: AlternateAvailabilityRequest,
    session: Any,
    station_pairs: list[dict[str, Any]],
) -> None:

    job.status = "running"
    job.total = len(station_pairs)



    await _publish_event(
        job,
        "started",
        {
            "job_id": job.job_id,
            "status": job.status,
            "total_candidates": job.total,
        },
    )

    quota = normalize_quota(request.quota)

    class_code = normalize_travel_class(
        request.travel_class
    )

    if not class_code:
        job.status = "failed"
        job.error = (
            "A specific travel class is required "
            "for alternate availability search."
        )

        await _publish_event(
            job,
            "error",
            {
                "job_id": job.job_id,
                "message": job.error,
            },
        )

        async with job.condition:
            job.condition.notify_all()

        return

    train_type = (
        str(request.train_type or "")
        .strip()
        .upper()
    )

    try:

        for pair in station_pairs:

            from_station = pair["from"]
            to_station = pair["to"]

            from_code = _normalize_code(
                from_station["station_code"]
            )

            to_code = _normalize_code(
                to_station["station_code"]
            )


            train_payload = {
                "trainNumber": request.train_number,
                "fromStnCode": from_code,
                "toStnCode": to_code,
                "trainType": (
                    [train_type]
                    if train_type
                    else []
                ),
            }


            # DEBUGGER CONDITION COMMENTED OUT:


            # if from_code == "SPJ" and to_code == "CPR":
               # DEBUGGER COMMENTED OUT: print("🔥 CALLING FETCH_TRAIN_AVAILABILITY: SPJ -> CPR")

            try:
                # The existing backend deliberately serializes
                # availability calls for a session.
                async with session.availability_lock:

                    result = await fetch_train_availability(
                        session=session,
                        train=train_payload,
                        journey_date=request.journey_date,
                        quota=quota,
                        class_code=class_code,
                    )


                # Keep the existing session alive while a long
                # alternate search is running.
                await update_last_used(
                    request.session_id
                )

            # except Exception:
            #     job.errors += 1
            #     job.checked += 1

            except Exception as exc:
                job.errors += 1
                job.checked += 1

                await _publish_event(
                    job,
                    "progress",
                    {
                        "job_id": job.job_id,
                        "checked": job.checked,
                        "total": job.total,
                        "found": job.found,
                        "errors": job.errors,
                        "remaining": (
                            job.total - job.checked
                        ),
                        "current": {
                            "from_code": from_code,
                            "to_code": to_code,
                        },
                    },
                )

                # One failed pair must NOT kill the complete search.
                continue

            # IMPORTANT:
            # Pass the requested journey date so the classifier
            # cannot accidentally select another date.
            classified = _classify_available_result(
                result,
                request.journey_date,
            )

            job.checked += 1

            if classified:

                job.found += 1

                alternative = {
                    "train_number": request.train_number,
                    "from": {
                        "code": from_code,
                        "name": from_station.get(
                            "station_name",
                            "",
                        ),
                    },
                    "to": {
                        "code": to_code,
                        "name": to_station.get(
                            "station_name",
                            "",
                        ),
                    },
                    "journey_date": request.journey_date,
                    "travel_class": class_code,
                    "quota": quota,
                    "status": classified["status"],
                    "availability": classified[
                        "availability"
                    ],
                    "day": classified["day"],
                }

                # IMPORTANT:
                # This event is published immediately.
                # Frontend does NOT need to wait for the complete
                # 400+ pair search.
                await _publish_event(
                    job,
                    "alternative_found",
                    {
                        "job_id": job.job_id,
                        "result": alternative,
                        "checked": job.checked,
                        "total": job.total,
                        "found": job.found,
                        "remaining": (
                            job.total - job.checked
                        ),
                    },
                )

            # Progress event after every candidate.
            await _publish_event(
                job,
                "progress",
                {
                    "job_id": job.job_id,
                    "checked": job.checked,
                    "total": job.total,
                    "found": job.found,
                    "errors": job.errors,
                    "remaining": (
                        job.total - job.checked
                    ),
                    "current": {
                        "from_code": from_code,
                        "to_code": to_code,
                    },
                },
            )

            if REQUEST_DELAY_SECONDS > 0:
                await asyncio.sleep(
                    REQUEST_DELAY_SECONDS
                )

        job.status = "completed"

        await cache_delete(
            f"{_build_alternate_redis_cache_key(request)}:running"
        )


        # ----------------------------------------------------
        # Save completed alternate result in Redis
        # ----------------------------------------------------

        redis_cache_key = (
            _build_alternate_redis_cache_key(
                request
            )
        )

        alternate_results = []

        for event in job.events:

            if event.get("event") != "alternative_found":
                continue

            event_data = event.get(
                "data",
                {}
            )

            alternative = event_data.get(
                "result"
            )

            if isinstance(
                alternative,
                dict,
            ):
                alternate_results.append(
                    alternative
                )

        fetched_at = (
            datetime.now().astimezone().isoformat()
        )

        await cache_set(
            redis_cache_key,
            {
                "results": alternate_results,
                "checked": job.checked,
                "total": job.total,
                "found": job.found,
                "errors": job.errors,
                "fetched_at": fetched_at,
            },
            ttl_seconds=(
                ALTERNATE_REDIS_CACHE_TTL_SECONDS
            ),
        )



        await _publish_event(
            job,
            "completed",
            {
                "job_id": job.job_id,
                "status": job.status,
                "checked": job.checked,
                "total": job.total,
                "found": job.found,
                "errors": job.errors,
            },
        )

    except asyncio.CancelledError:

        job.status = "cancelled"


        await cache_delete(f"{_build_alternate_redis_cache_key(request)}:running")
            
        

        await _publish_event(
            job,
            "cancelled",
            {
                "job_id": job.job_id,
                "status": job.status,
                "checked": job.checked,
                "total": job.total,
                "found": job.found,
            },
        )

        raise

    except Exception as exc:

        job.status = "failed"


        await cache_delete(f"{_build_alternate_redis_cache_key(request)}:running")

        # Keep the technical exception only in server logs.
        print(
            "ALTERNATE JOB ERROR:",
            repr(exc),
        )

        job.error = (
            "Unable to check alternate availability "
            "right now. Please try again."
        )

        await _publish_event(
            job,
            "error",
            {
                "job_id": job.job_id,
                "status": job.status,
                "message": job.error,
                "checked": job.checked,
                "total": job.total,
                "found": job.found,
            },
        )

    finally:

        async with job.condition:
            job.condition.notify_all()


# ---------------------------------------------------------------------------
# START alternate search
# ---------------------------------------------------------------------------

@router.post("/start")
async def start_alternate_availability(
    request: AlternateAvailabilityRequest,
) -> dict[str, Any]:

    await _cleanup_old_jobs()

    # ---------------------------------------------------------
    # Do not start alternate search for CNF/RAC.
    # ---------------------------------------------------------

    if request.initial_status:

        initial_status = (
            request.initial_status
            .strip()
            .upper()
        )

        if (
            "CNF" in initial_status
            or "CONFIRM" in initial_status
            or "RAC" in initial_status
        ):
            raise HTTPException(
                status_code=400,
                detail=(
                    "Alternate availability search is "
                    "not required for CNF/RAC."
                ),
            )

    # ---------------------------------------------------------
    # Validate existing IRCTC/TBIS session.
    # ---------------------------------------------------------

    session = await get_session(
        request.session_id
    )

    if session is None:
        raise HTTPException(
            status_code=404,
            detail=(
                "Train session not found or expired. "
                "Please search trains again."
            ),
        )

    # ---------------------------------------------------------
    # Normalize selected class/quota.
    # ---------------------------------------------------------

    class_code = normalize_travel_class(
        request.travel_class
    )

    if not class_code:
        raise HTTPException(
            status_code=400,
            detail=(
                "A specific travel class is required "
                "for alternate availability."
            ),
        )

    quota = normalize_quota(
        request.quota
    )


    # ---------------------------------------------------------
    # Check completed alternate result in Redis
    # ---------------------------------------------------------

    redis_cache_key = (
        _build_alternate_redis_cache_key(
            request
        )
    )

    if not request.force_refresh:

        cached_result = await cache_get(
            redis_cache_key
        )

        if isinstance(
            cached_result,
            dict,
        ):

            return {
                "success": True,
                "cached": True,
                "job_id": None,
                "status": "completed",
                "train_number": request.train_number,
                "journey_date": request.journey_date,
                "travel_class": class_code,
                "quota": quota,
                "results": cached_result.get(
                    "results",
                    [],
                ),
                "checked": cached_result.get(
                    "checked",
                    0,
                ),
                "total": cached_result.get(
                    "total",
                    0,
                ),
                "found": cached_result.get(
                    "found",
                    0,
                ),
                "errors": cached_result.get(
                    "errors",
                    0,
                ),
                "fetched_at": cached_result.get(
                    "fetched_at"
                ),
            }



    # ---------------------------------------------------------
    # Check whether the same alternate search is
    # already running.
    # ---------------------------------------------------------

    redis_cache_key = (
        _build_alternate_redis_cache_key(
            request
        )
    )

    if not request.force_refresh:

        running_job_id = (
            await _find_running_alternate_job(
                redis_cache_key
            )
        )

        if running_job_id:

            running_job = alternate_jobs.get(
                running_job_id
            )

            return {
                "success": True,
                "cached": False,
                "shared": True,
                "job_id": running_job_id,
                "status": (
                    running_job.status
                    if running_job
                    else "running"
                ),
                "train_number": request.train_number,
                "journey_date": request.journey_date,
                "travel_class": class_code,
                "quota": quota,
                "stream_url": (
                    f"/api/trains/alternate/stream/"
                    f"{running_job_id}"
                ),
                "status_url": (
                    f"/api/trains/alternate/status/"
                    f"{running_job_id}"
                ),
            }

    # ---------------------------------------------------------
    # Fetch live route from NTES route service.
    # ---------------------------------------------------------

    route_data = await _fetch_train_route(
        train_number=request.train_number,
        journey_date=request.journey_date,
    )

    stations = _extract_bookable_stations(
        route_data
    )

    # ---------------------------------------------------------
    # Generate EVERY forward station pair.
    # ---------------------------------------------------------

    station_pairs = _build_station_pairs(
        stations=stations,
        original_from=request.from_code,
        original_to=request.to_code,
    )

    if not station_pairs:
        raise HTTPException(
            status_code=422,
            detail=(
                "No alternate station pairs were found."
            ),
        )

    # ---------------------------------------------------------
    # Create background job.
    # ---------------------------------------------------------

    job_id = uuid.uuid4().hex


    running_redis_key = (
        f"{redis_cache_key}:running"
    )
    await cache_set(
        running_redis_key,
        job_id,
        ttl_seconds=30 * 60,
    )

    job = AlternateJob(
        job_id=job_id,
    )



    job.total = len(station_pairs)

    async with alternate_jobs_lock:
        alternate_jobs[job_id] = job

    # ---------------------------------------------------------
    # Start background processing.
    # ---------------------------------------------------------

    job.task = asyncio.create_task(
        _run_alternate_job(
            job=job,
            request=request,
            session=session,
            station_pairs=station_pairs,
        )
    )

    return {
        "success": True,
        "job_id": job_id,
        "status": "started",
        "train_number": request.train_number,
        "journey_date": request.journey_date,
        "travel_class": class_code,
        "quota": quota,
        "route_station_count": len(stations),
        "total_candidates": len(station_pairs),
        "stream_url": (
            f"/api/trains/alternate/stream/{job_id}"
        ),
        "status_url": (
            f"/api/trains/alternate/status/{job_id}"
        ),
    }


# ---------------------------------------------------------------------------
# JOB STATUS
# ---------------------------------------------------------------------------

@router.get("/status/{job_id}")
async def alternate_availability_status(
    job_id: str,
) -> dict[str, Any]:

    await _cleanup_old_jobs()

    job = alternate_jobs.get(job_id)

    if job is None:
        raise HTTPException(
            status_code=404,
            detail="Alternate availability job not found.",
        )

    return {
        "success": True,
        "job_id": job.job_id,
        "status": job.status,
        "checked": job.checked,
        "total": job.total,
        "found": job.found,
        "errors": job.errors,
        "remaining": max(
            job.total - job.checked,
            0,
        ),
        "error": job.error,
    }


@router.post("/cancel/{job_id}")
async def cancel_alternate_availability(
    job_id: str,
) -> dict[str, Any]:

    # Clean up old jobs first
    await _cleanup_old_jobs()

    job = alternate_jobs.get(job_id)

    if job is None:
        raise HTTPException(
            status_code=404,
            detail="Alternate availability job not found.",
        )
    # If job is already in a terminal state,
    # there is nothing left to cancel.
    if job.status in (
        "completed",
        "failed",
        "cancelled",
    ):
        return {
            "success": True,
            "job_id": job.job_id,
            "status": job.status,
            "message": "Alternate availability job is already stopped.",
            "checked": job.checked,
            "total": job.total,
            "found": job.found,
        }

    # Cancel the actual backend asyncio task.
    if (
        job.status in ("starting", "searching")
        and job.task is not None
        and not job.task.done()
    ):
        job.task.cancel()

        return {
            "success": True,
            "job_id": job.job_id,
            "status": "cancelling",
            "message": "Alternate availability search is being cancelled.",
            "checked": job.checked,
            "total": job.total,
            "found": job.found,
        }

    # Task is already finished or unavailable.
    return {
        "success": True,
        "job_id": job.job_id,
        "status": job.status,
        "message": "Alternate availability search is already stopped.",
        "checked": job.checked,
        "total": job.total,
        "found": job.found,
    }



# ---------------------------------------------------------------------------
# SSE STREAM
# ---------------------------------------------------------------------------

@router.get("/stream/{job_id}")
async def alternate_availability_stream(
    job_id: str,
):
    await _cleanup_old_jobs()

    job = alternate_jobs.get(job_id)

    if job is None:
        raise HTTPException(
            status_code=404,
            detail="Alternate availability job not found.",
        )

    async def event_generator():

        next_index = 0

        while True:

            new_events: list[dict[str, Any]] = []
            finished = False

            async with job.condition:

                while (
                    next_index >= len(job.events)
                    and job.status
                    not in {
                        "completed",
                        "failed",
                        "cancelled",
                    }
                ):
                    try:
                        await asyncio.wait_for(
                            job.condition.wait(),
                            timeout=15.0,
                        )
                    except asyncio.TimeoutError:
                        # SSE heartbeat.
                        yield ": ping\n\n"

                if next_index < len(job.events):

                    new_events = job.events[
                        next_index:
                    ]

                    next_index = len(
                        job.events
                    )

                finished = (
                    job.status
                    in {
                        "completed",
                        "failed",
                        "cancelled",
                    }
                    and next_index >= len(
                        job.events
                    )
                )

            for event in new_events:
                yield _sse_encode(event)

            if finished:
                break

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )
