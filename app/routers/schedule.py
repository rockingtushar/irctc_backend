from datetime import datetime
import time

from curl_cffi import requests as curl_requests
from fastapi import APIRouter, HTTPException


router = APIRouter(
    prefix="/train",
    tags=["Train Schedule"],
)


# ============================================================
# IRCTC CONFIG
# ============================================================

IRCTC_BASE_URL = "https://www.irctc.co.in"

IRCTC_SCHEDULE_URL = (
    "https://www.irctc.co.in/eticketing/protected/"
    "mapps1/trnscheduleenquiry"
)


# ============================================================
# BROWSER-LIKE HEADERS
# ============================================================

IRCTC_HEADERS = {
    "Accept": "application/json, text/plain, */*",
    "Accept-Language": "en-GB,en-US;q=0.9,en;q=0.8,hi;q=0.7",
    "bmirak": "webbm",
    "DNT": "1",
    "Origin": "https://www.irctc.co.in",
    "Referer": "https://www.irctc.co.in/online-charts/",
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/153.0.0.0 Safari/537.36"
    ),
}


# ============================================================
# HELPERS
# ============================================================

def clean_time(value):
    """
    Convert IRCTC '--' / empty values to None.
    """

    if value in (None, "", "--"):
        return None

    return value


def to_int(value):
    """
    Safely convert numeric values to int.
    """

    if value is None:
        return None

    try:
        return int(value)
    except (ValueError, TypeError):
        return value


def to_bool(value):
    """
    Convert IRCTC boolean-like values to Python bool.
    """

    if isinstance(value, bool):
        return value

    if value is None:
        return False

    return str(value).strip().lower() == "true"


def normalize_station(station):
    """
    Normalize one station from IRCTC schedule response.
    """

    return {
        "code": station.get("stationCode"),
        "name": station.get("stationName"),

        "arrival": clean_time(
            station.get("arrivalTime")
        ),

        "departure": clean_time(
            station.get("departureTime")
        ),

        "route_number": station.get("routeNumber"),

        "halt": clean_time(
            station.get("haltTime")
        ),

        "distance": to_int(
            station.get("distance")
        ),

        "day": to_int(
            station.get("dayCount")
        ),

        "serial": to_int(
            station.get("stnSerialNumber")
        ),

        "boarding_disabled": to_bool(
            station.get("boardingDisabled")
        ),

        "status": station.get("status"),
    }


# ============================================================
# TRAIN SCHEDULE API
# ============================================================

@router.get("/schedule/{train_number}")
async def get_train_schedule(train_number: str):

    # --------------------------------------------------------
    # Validate train number
    # --------------------------------------------------------

    train_number = train_number.strip()

    if not train_number:
        raise HTTPException(
            status_code=400,
            detail="Train number is required",
        )

    if not train_number.isdigit():
        raise HTTPException(
            status_code=400,
            detail="Train number must contain only digits",
        )

    # --------------------------------------------------------
    # Build IRCTC URL
    # --------------------------------------------------------

    url = f"{IRCTC_SCHEDULE_URL}/{train_number}"

    # --------------------------------------------------------
    # Dynamic browser request value
    #
    # Browser sends a changing "greq" value.
    # Do NOT hard-code this.
    # --------------------------------------------------------

    greq = str(
        int(time.time() * 1000)
    )

    request_headers = {
        **IRCTC_HEADERS,
        "greq": greq,
    }

    # print()
    # print("=" * 70)
    # print("IRCTC TRAIN SCHEDULE REQUEST")
    # print("=" * 70)

    # print(
        # "TRAIN NUMBER:",
        # train_number,
    # )

    # print(
        # "IRCTC URL:",
        # url,
    # )

    # print(
        # "GREQ:",
        # greq,
    # )

    try:

        # ----------------------------------------------------
        # Create HTTP session
        # ----------------------------------------------------

        async with curl_requests.AsyncSession(
            impersonate="chrome",
            timeout=30.0,
            headers=IRCTC_HEADERS,
        ) as client:

            # ------------------------------------------------
            # STEP 1
            # Establish IRCTC session/cookies
            # ------------------------------------------------

            home_response = await client.get(
                IRCTC_BASE_URL + "/",
                headers={
                    **IRCTC_HEADERS,
                    "Referer": "https://www.irctc.co.in/",
                },
            )

            # print(
                # "IRCTC HOME STATUS:",
                # home_response.status_code,
            # )

            # print(
                # "IRCTC COOKIES:",
                # dict(client.cookies),
            # )

            # ------------------------------------------------
            # STEP 2
            # Fetch train schedule
            # ------------------------------------------------

            response = await client.get(
                url,
                headers=request_headers,
            )
            print("IRCTC STATUS:", response.status_code)
            print("IRCTC FINAL URL:", response.url)
            print("IRCTC BODY:", response.text[:1000])

            # ------------------------------------------------
            # Debug information
            # ------------------------------------------------

            # print(
                # "IRCTC SCHEDULE STATUS:",
                # response.status_code,
            # )

            # print(
                # "IRCTC CONTENT-TYPE:",
                # response.headers.get(
                    # "content-type"
                # ),
            # )

            # print(
                # "IRCTC CONTENT-LENGTH:",
                # response.headers.get(
                    # "content-length"
                # ),
            # )

            # print(
                # "IRCTC FINAL URL:",
                # str(response.url),
            # )

            # print(
                # "IRCTC RESPONSE SIZE:",
                # len(response.content),
                # "bytes",
            # )

            # print(
                # "IRCTC RESPONSE:",
                # response.text[:2000],
            # )

            # print("=" * 70)
            # print()

    except Exception as exc:

        # print(
            # "IRCTC REQUEST ERROR:",
            # repr(exc),
        # )
        print("IRCTC EXCEPTION:", repr(exc))
        raise HTTPException(
            status_code=502,
            detail=(
                "Unable to connect to IRCTC "
                "schedule service : {exc}",
            ),
        )

    # --------------------------------------------------------
    # HTTP status validation
    # --------------------------------------------------------

    if response.status_code != 200:

        raise HTTPException(
            status_code=502,
            detail=(
                "IRCTC schedule API returned "
                f"HTTP {response.status_code}"
            ),
        )

    # --------------------------------------------------------
    # Empty response validation
    # --------------------------------------------------------

    if not response.content:

        raise HTTPException(
            status_code=502,
            detail=(
                "IRCTC returned an empty schedule response"
            ),
        )

    # --------------------------------------------------------
    # Content-Type
    #
    # Some IRCTC/Akamai responses may omit the header,
    # so we don't reject the response only because
    # content-type is missing.
    # --------------------------------------------------------

    content_type = (
        response.headers
        .get("content-type", "")
        .lower()
    )

    # --------------------------------------------------------
    # Parse JSON
    # --------------------------------------------------------

    try:

        raw = response.json()

    except ValueError:

        raise HTTPException(
            status_code=502,
            detail=(
                "IRCTC returned a non-JSON schedule response"
            ),
        )

    # --------------------------------------------------------
    # Empty JSON
    # --------------------------------------------------------

    if not raw:

        raise HTTPException(
            status_code=404,
            detail="Train schedule not found",
        )

    # --------------------------------------------------------
    # Validate expected response
    # --------------------------------------------------------

    if not isinstance(raw, dict):

        raise HTTPException(
            status_code=502,
            detail=(
                "Unexpected response format from IRCTC"
            ),
        )

    # --------------------------------------------------------
    # Station list
    # --------------------------------------------------------

    raw_station_list = (
        raw.get("stationList") or []
    )

    if not isinstance(raw_station_list, list):

        raise HTTPException(
            status_code=502,
            detail=(
                "Invalid station list received from IRCTC"
            ),
        )

    stations = [
        normalize_station(station)
        for station in raw_station_list
        if isinstance(station, dict)
    ]

    # --------------------------------------------------------
    # No stations
    # --------------------------------------------------------

    if not stations:

        raise HTTPException(
            status_code=404,
            detail=(
                "No station schedule found "
                "for this train"
            ),
        )

    # --------------------------------------------------------
    # Running days
    # --------------------------------------------------------

    running_days = {
        "mon": raw.get("trainRunsOnMon") == "Y",
        "tue": raw.get("trainRunsOnTue") == "Y",
        "wed": raw.get("trainRunsOnWed") == "Y",
        "thu": raw.get("trainRunsOnThu") == "Y",
        "fri": raw.get("trainRunsOnFri") == "Y",
        "sat": raw.get("trainRunsOnSat") == "Y",
        "sun": raw.get("trainRunsOnSun") == "Y",
    }

    # --------------------------------------------------------
    # Source / destination
    # --------------------------------------------------------

    from_code = raw.get("stationFrom")
    to_code = raw.get("stationTo")

    from_name = None
    to_name = None

    for station in stations:

        station_code = station.get("code")

        if station_code == from_code:
            from_name = station.get("name")

        if station_code == to_code:
            to_name = station.get("name")

    # --------------------------------------------------------
    # Fallback names
    # --------------------------------------------------------

    if from_name is None and stations:

        from_name = stations[0].get(
            "name"
        )

    if to_name is None and stations:

        to_name = stations[-1].get(
            "name"
        )

    # --------------------------------------------------------
    # Final normalized response
    # --------------------------------------------------------

    return {
        "success": True,

        "data": {
            "train_number": raw.get(
                "trainNumber"
            ),

            "train_name": raw.get(
                "trainName"
            ),

            "from": {
                "code": from_code,
                "name": from_name,
            },

            "to": {
                "code": to_code,
                "name": to_name,
            },

            "train_owner": raw.get(
                "trainOwner"
            ),

            "running_days": running_days,

            "stations": stations,

            "server_id": raw.get(
                "serverId"
            ),

            "timestamp": raw.get(
                "timeStamp"
            ),

            "duration": raw.get(
                "duration"
            ),

            "source": "IRCTC Train Schedule",

            "fetched_at": int(
                datetime.now().timestamp()
            ),
        },
    }
