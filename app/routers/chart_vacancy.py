# from datetime import date
# from typing import Any, Optional

# import httpx
# from fastapi import APIRouter, HTTPException
# from pydantic import BaseModel, Field


# router = APIRouter(
#     prefix="/chart",
#     tags=["Chart / Vacant Berths"],
# )


# # ============================================================
# # IRCTC ONLINE CHARTS
# # ============================================================

# TRAIN_COMPOSITION_URL = (
#     "https://www.irctc.co.in/online-charts/api/trainComposition"
# )

# COACH_COMPOSITION_URL = (
#     "https://www.irctc.co.in/online-charts/api/coachComposition"
# )


# IRCTC_HEADERS = {
#     "Accept": "application/json, text/plain, */*",
#     "Content-Type": "application/json",
#     "Origin": "https://www.irctc.co.in",
#     "Referer": "https://www.irctc.co.in/online-charts/",
#     "User-Agent": (
#         "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
#         "AppleWebKit/537.36 (KHTML, like Gecko) "
#         "Chrome/153.0.0.0 Safari/537.36"
#     ),
# }


# # ============================================================
# # REQUEST MODELS
# # ============================================================

# class ChartTrainRequest(BaseModel):
#     train_number: str = Field(
#         ...,
#         min_length=1,
#         max_length=10,
#     )

#     journey_date: date

#     boarding_station: str = Field(
#         ...,
#         min_length=2,
#         max_length=10,
#     )


# class ChartCoachRequest(BaseModel):
#     train_number: str = Field(
#         ...,
#         min_length=1,
#         max_length=10,
#     )

#     journey_date: date

#     boarding_station: str = Field(
#         ...,
#         min_length=2,
#         max_length=10,
#     )

#     remote_station: str = Field(
#         ...,
#         min_length=2,
#         max_length=10,
#     )

#     train_source_station: str = Field(
#         ...,
#         min_length=2,
#         max_length=10,
#     )

#     travel_class: str = Field(
#         ...,
#         min_length=1,
#         max_length=5,
#     )

#     coach: str = Field(
#         ...,
#         min_length=1,
#         max_length=10,
#     )


# # ============================================================
# # NORMALIZATION HELPERS
# # ============================================================

# def normalize_train_number(value: str) -> str:
#     """
#     Normalize train number.

#     Example:
#         " 12560 " -> "12560"
#     """
#     return str(value).strip()


# def normalize_station(value: str) -> str:
#     """
#     Normalize railway station code.

#     Example:
#         " ndls " -> "NDLS"
#     """
#     return str(value).strip().upper()


# def normalize_class(value: str) -> str:
#     """
#     Normalize class code.

#     Example:
#         " sl " -> "SL"
#     """
#     return str(value).strip().upper()


# def normalize_coach(value: str) -> str:
#     """
#     Normalize coach name.

#     Example:
#         " s1 " -> "S1"
#     """
#     return str(value).strip().upper()


# def clean_optional(value: Any) -> Optional[Any]:
#     """
#     Convert empty string to None.
#     """
#     if value is None:
#         return None

#     if isinstance(value, str):
#         value = value.strip()

#         if value == "":
#             return None

#     return value


# def normalize_bool(value: Any) -> bool:
#     """
#     Safely normalize IRCTC boolean-like values.

#     Supported:
#         True / False
#         "true" / "false"
#         1 / 0
#     """

#     if isinstance(value, bool):
#         return value

#     if isinstance(value, str):
#         return value.strip().lower() in {
#             "true",
#             "1",
#             "yes",
#             "y",
#         }

#     if isinstance(value, (int, float)):
#         return value != 0

#     return False


# # ============================================================
# # HTTP HELPER
# # ============================================================

# async def irctc_post(
#     url: str,
#     payload: dict[str, Any],
# ) -> dict[str, Any]:

#     try:
#         async with httpx.AsyncClient(
#             timeout=httpx.Timeout(
#                 connect=15.0,
#                 read=120.0,
#                 write=30.0,
#                 pool=10.0,
#             ),
#             follow_redirects=True,
#         ) as client:

#             response = await client.post(
#                 url,
#                 json=payload,
#                 headers=IRCTC_HEADERS,
#             )

#     except httpx.RequestError as exc:
#         print("CHART IRCTC REQUEST ERROR:", repr(exc))
#         print("CHART IRCTC URL:", url)
#         print("CHART IRCTC PAYLOAD:", payload)
#         raise HTTPException(
#             status_code=502,
#             detail=f"IRCTC request failed: {str(exc)}",
#         )

#     print("CHART IRCTC STATUS:", response.status_code)
#     print("CHART IRCTC HEADERS:", dict(response.headers))
#     print("CHART IRCTC BODY:", response.text[:2000])


#     if response.status_code != 200:
#         raise HTTPException(
#             status_code=502,
#             detail=(
#                 f"IRCTC returned HTTP "
#                 f"{response.status_code}"
#             ),
#         )

#     try:
#         data = response.json()

#     except ValueError:
#         raise HTTPException(
#             status_code=502,
#             detail="IRCTC returned invalid JSON",
#         )

#     if not isinstance(data, dict):
#         raise HTTPException(
#             status_code=502,
#             detail="Unexpected IRCTC response format",
#         )

#     return data


# # ============================================================
# # BERTH STATUS CALCULATOR
# # ============================================================

# def calculate_berth_status(
#     segments: list[dict[str, Any]],
# ) -> dict[str, Any]:
#     """
#     Calculate useful derived status from IRCTC berth segments.

#     IMPORTANT:
#     This does NOT change the source occupancy meaning.

#     occupancy=True
#         -> occupied for that segment

#     occupancy=False
#         -> not occupied for that segment

#     We deliberately do NOT call occupancy=False "bookable",
#     because the source response does not establish booking
#     eligibility merely from occupancy.
#     """

#     occupied_segments: list[dict[str, Any]] = []
#     vacant_segments: list[dict[str, Any]] = []

#     for segment in segments:

#         occupancy = normalize_bool(
#             segment.get("occupancy")
#         )

#         normalized_segment = {
#             "split_no": segment.get("split_no"),
#             "from": clean_optional(segment.get("from")),
#             "to": clean_optional(segment.get("to")),
#             "quota": clean_optional(segment.get("quota")),
#         }

#         if occupancy:
#             occupied_segments.append(
#                 normalized_segment
#             )
#         else:
#             vacant_segments.append(
#                 normalized_segment
#             )

#     total_segments = len(segments)
#     occupied_count = len(occupied_segments)
#     vacant_count = len(vacant_segments)

#     fully_occupied = (
#         total_segments > 0
#         and occupied_count == total_segments
#     )

#     fully_vacant = (
#         total_segments > 0
#         and vacant_count == total_segments
#     )

#     partially_occupied = (
#         total_segments > 0
#         and occupied_count > 0
#         and vacant_count > 0
#     )

#     return {
#         "occupied_segments": occupied_segments,
#         "vacant_segments": vacant_segments,

#         "total_segments": total_segments,
#         "occupied_segment_count": occupied_count,
#         "vacant_segment_count": vacant_count,

#         "fully_occupied": fully_occupied,
#         "partially_occupied": partially_occupied,
#         "fully_vacant": fully_vacant,
#     }


# # ============================================================
# # NORMALIZE COACH BERTH
# # ============================================================

# def normalize_berth(
#     berth: dict[str, Any],
# ) -> dict[str, Any]:

#     raw_segments = berth.get("bsd")

#     if not isinstance(raw_segments, list):
#         raw_segments = []

#     normalized_segments: list[dict[str, Any]] = []

#     for segment in raw_segments:

#         if not isinstance(segment, dict):
#             continue

#         normalized_segments.append(
#             {
#                 "split_no": segment.get("splitNo"),
#                 "from": clean_optional(
#                     segment.get("from")
#                 ),
#                 "to": clean_optional(
#                     segment.get("to")
#                 ),
#                 "quota": clean_optional(
#                     segment.get("quota")
#                 ),
#                 "occupancy": normalize_bool(
#                     segment.get("occupancy")
#                 ),
#             }
#         )

#     computed_status = calculate_berth_status(
#         normalized_segments
#     )

#     return {
#         "berth_no": berth.get("berthNo"),
#         "berth_code": clean_optional(
#             berth.get("berthCode")
#         ),

#         "cabin_coupe": clean_optional(
#             berth.get("cabinCoupe")
#         ),

#         "cabin_coupe_name_no": clean_optional(
#             berth.get("cabinCoupeNameNo")
#         ),

#         "from": clean_optional(
#             berth.get("from")
#         ),

#         "to": clean_optional(
#             berth.get("to")
#         ),

#         "quota_count_station": clean_optional(
#             berth.get("quotaCntStn")
#         ),

#         "enable": normalize_bool(
#             berth.get("enable")
#         ),

#         # Original normalized source segments
#         "segments": normalized_segments,

#         # Computed fields
#         "occupied_segments": computed_status[
#             "occupied_segments"
#         ],

#         "vacant_segments": computed_status[
#             "vacant_segments"
#         ],

#         "total_segments": computed_status[
#             "total_segments"
#         ],

#         "occupied_segment_count": computed_status[
#             "occupied_segment_count"
#         ],

#         "vacant_segment_count": computed_status[
#             "vacant_segment_count"
#         ],

#         "fully_occupied": computed_status[
#             "fully_occupied"
#         ],

#         "partially_occupied": computed_status[
#             "partially_occupied"
#         ],

#         "fully_vacant": computed_status[
#             "fully_vacant"
#         ],
#     }


# # ============================================================
# # TRAIN COMPOSITION
# # ============================================================

# @router.post("/train")
# async def get_train_chart(
#     request: ChartTrainRequest,
# ):

#     train_number = normalize_train_number(
#         request.train_number
#     )

#     boarding_station = normalize_station(
#         request.boarding_station
#     )

#     journey_date = request.journey_date.isoformat()

#     payload = {
#         "trainNo": train_number,
#         "jDate": journey_date,
#         "boardingStation": boarding_station,
#     }

#     response_data = await irctc_post(
#         TRAIN_COMPOSITION_URL,
#         payload,
#     )

#     raw_coaches = response_data.get("cdd")

#     if not isinstance(raw_coaches, list):
#         raw_coaches = []

#     coaches: list[dict[str, Any]] = []

#     for coach in raw_coaches:

#         if not isinstance(coach, dict):
#             continue

#         coaches.append(
#             {
#                 "coach_name": clean_optional(
#                     coach.get("coachName")
#                 ),

#                 "class_code": clean_optional(
#                     coach.get("classCode")
#                 ),

#                 "position_from_engine": coach.get(
#                     "positionFromEngine"
#                 ),

#                 "vacant_berths": coach.get(
#                     "vacantBerths"
#                 ),
#             }
#         )

#     chart_status_raw = response_data.get(
#         "chartStatusResponseDto"
#     )

#     chart_status = None

#     if isinstance(chart_status_raw, dict):
#         chart_status = {
#             "messageIndex": chart_status_raw.get(
#                 "messageIndex"
#             ),
#             "chartOneFlag": chart_status_raw.get(
#                 "chartOneFlag"
#             ),
#             "chartTwoFlag": chart_status_raw.get(
#                 "chartTwoFlag"
#             ),
#             "trainStartDate": clean_optional(
#                 chart_status_raw.get(
#                     "trainStartDate"
#                 )
#             ),
#             "remoteStationCode": clean_optional(
#                 chart_status_raw.get(
#                     "remoteStationCode"
#                 )
#             ),
#             "messageType": clean_optional(
#                 chart_status_raw.get(
#                     "messageType"
#                 )
#             ),
#         }

#     return {
#         "success": True,
#         "data": {
#             "train_number": clean_optional(
#                 response_data.get("trainNo")
#             ) or train_number,

#             "train_name": clean_optional(
#                 response_data.get("trainName")
#             ),

#             "from": clean_optional(
#                 response_data.get("from")
#             ),

#             "to": clean_optional(
#                 response_data.get("to")
#             ),

#             "train_start_date": clean_optional(
#                 response_data.get(
#                     "trainStartDate"
#                 )
#             ),

#             "remote_location_chart_date": clean_optional(
#                 response_data.get(
#                     "remoteLocationChartDate"
#                 )
#             ),

#             "remote": clean_optional(
#                 response_data.get("remote")
#             ),

#             "next_remote": clean_optional(
#                 response_data.get("nextRemote")
#             ),

#             "available_remote_for_booking": clean_optional(
#                 response_data.get(
#                     "avlRemoteForBooking"
#                 )
#             ),

#             "destination_station": clean_optional(
#                 response_data.get(
#                     "destinationStation"
#                 )
#             ),

#             "chart_one_date": clean_optional(
#                 response_data.get(
#                     "chartOneDate"
#                 )
#             ),

#             "chart_two_date": clean_optional(
#                 response_data.get(
#                     "chartTwoDate"
#                 )
#             ),

#             "chart_status": chart_status,

#             "error": clean_optional(
#                 response_data.get("error")
#             ),

#             "coaches": coaches,

#             "source": "IRCTC Online Charts",
#         },
#     }


# # ============================================================
# # COACH COMPOSITION / BERTH DETAILS
# # ============================================================

# @router.post("/coach")
# async def get_coach_chart(
#     request: ChartCoachRequest,
# ):

#     train_number = normalize_train_number(
#         request.train_number
#     )

#     boarding_station = normalize_station(
#         request.boarding_station
#     )

#     remote_station = normalize_station(
#         request.remote_station
#     )

#     train_source_station = normalize_station(
#         request.train_source_station
#     )

#     travel_class = normalize_class(
#         request.travel_class
#     )

#     coach = normalize_coach(
#         request.coach
#     )

#     journey_date = request.journey_date.isoformat()

#     payload = {
#         "trainNo": train_number,
#         "boardingStation": boarding_station,
#         "remoteStation": remote_station,
#         "trainSourceStation": train_source_station,
#         "cls": travel_class,
#         "coach": coach,
#         "jDate": journey_date,
#     }

#     response_data = await irctc_post(
#         COACH_COMPOSITION_URL,
#         payload,
#     )

#     raw_berths = response_data.get("bdd")

#     if not isinstance(raw_berths, list):
#         raw_berths = []

#     berths: list[dict[str, Any]] = []

#     for berth in raw_berths:

#         if not isinstance(berth, dict):
#             continue

#         berths.append(
#             normalize_berth(berth)
#         )

#     # ========================================================
#     # SUMMARY
#     # ========================================================

#     fully_occupied_count = 0
#     partially_occupied_count = 0
#     fully_vacant_count = 0

#     for berth in berths:

#         if berth["fully_occupied"]:
#             fully_occupied_count += 1

#         elif berth["partially_occupied"]:
#             partially_occupied_count += 1

#         elif berth["fully_vacant"]:
#             fully_vacant_count += 1

#     return {
#         "success": True,
#         "data": {
#             "train_number": train_number,

#             "journey_date": journey_date,

#             "boarding_station": boarding_station,

#             "remote_station": remote_station,

#             "train_source_station": train_source_station,

#             "class_code": travel_class,

#             "coach": clean_optional(
#                 response_data.get("coachName")
#             ) or coach,

#             "error": clean_optional(
#                 response_data.get("error")
#             ),

#             "berth_count": len(berths),

#             "berth_summary": {
#                 "fully_occupied": fully_occupied_count,
#                 "partially_occupied": (
#                     partially_occupied_count
#                 ),
#                 "fully_vacant": fully_vacant_count,
#             },

#             "berths": berths,

#             "source": "IRCTC Online Charts",
#         },
#     }
import os

# ============================================================
# PLAYWRIGHT BROWSER PATH
# ============================================================
# Must match the Render Build Command.
os.environ.setdefault(
    "PLAYWRIGHT_BROWSERS_PATH",
    "/opt/render/project/src/.playwright",
)

from datetime import date
from typing import Any, Optional
import asyncio
import json

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field
from playwright.async_api import (
    async_playwright,
    Browser,
    BrowserContext,
    Page,
    Playwright,
)


router = APIRouter(
    prefix="/chart",
    tags=["Chart / Vacant Berths"],
)


# ============================================================
# IRCTC ONLINE CHARTS
# ============================================================

TRAIN_COMPOSITION_URL = (
    "https://www.irctc.co.in/online-charts/api/trainComposition"
)

COACH_COMPOSITION_URL = (
    "https://www.irctc.co.in/online-charts/api/coachComposition"
)

IRCTC_PAGE_URL = (
    "https://www.irctc.co.in/online-charts/"
)


# ============================================================
# PLAYWRIGHT BROWSER
# ============================================================

class IRCTCBrowser:

    def __init__(self):
        self.playwright: Optional[Playwright] = None
        self.browser: Optional[Browser] = None
        self.context: Optional[BrowserContext] = None
        self.page: Optional[Page] = None

        self.lock = asyncio.Lock()

    async def start(self):

        if self.browser is not None:
            return

        print(
            "CHART: PLAYWRIGHT_BROWSERS_PATH:",
            os.environ.get(
                "PLAYWRIGHT_BROWSERS_PATH"
            ),
        )

        print(
            "CHART: Starting Playwright Chromium..."
        )

        self.playwright = (
            await async_playwright().start()
        )

        self.browser = (
            await self.playwright.chromium.launch(
                headless=True,
                args=[
                    "--no-sandbox",
                    "--disable-setuid-sandbox",
                    "--disable-dev-shm-usage",
                    "--disable-gpu",
                    "--disable-software-rasterizer",
                ],
            )
        )

        print(
            "CHART: Chromium started successfully"
        )

        self.context = (
            await self.browser.new_context(
                user_agent=(
                    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) "
                    "Chrome/153.0.0.0 Safari/537.36"
                ),
                locale="en-GB",
                viewport={
                    "width": 1366,
                    "height": 768,
                },
            )
        )

        self.page = (
            await self.context.new_page()
        )

        print(
            "CHART: Browser context created"
        )

    async def ensure_page(self):

        await self.start()

        if self.page is None:
            raise RuntimeError(
                "Playwright page is not available"
            )

        current_url = self.page.url or ""

        if (
            "irctc.co.in/online-charts"
            not in current_url
        ):

            print(
                "CHART: Opening IRCTC Online Charts..."
            )

            await self.page.goto(
                IRCTC_PAGE_URL,
                wait_until="domcontentloaded",
                timeout=60000,
            )

            print(
                "CHART: IRCTC page loaded:",
                self.page.url,
            )

    async def post(
        self,
        url: str,
        payload: dict[str, Any],
    ) -> dict[str, Any]:

        async with self.lock:

            await self.ensure_page()

            if self.page is None:
                raise RuntimeError(
                    "Playwright page is not available"
                )

            print(
                "CHART: Browser POST URL:",
                url,
            )

            print(
                "CHART: Browser POST PAYLOAD:",
                payload,
            )

            result = await self.page.evaluate(
                """
                async ({ url, payload }) => {

                    try {

                        const response = await fetch(
                            url,
                            {
                                method: "POST",

                                headers: {
                                    "Accept": "application/json",
                                    "Content-Type": "application/json",
                                    "Origin": "https://www.irctc.co.in",
                                    "Referer": "https://www.irctc.co.in/online-charts/"
                                },

                                body: JSON.stringify(payload)
                            }
                        );

                        const text =
                            await response.text();

                        return {
                            ok: true,
                            status: response.status,
                            text: text
                        };

                    } catch (error) {

                        return {
                            ok: false,
                            status: 0,
                            text: "",
                            error: String(error)
                        };
                    }
                }
                """,
                {
                    "url": url,
                    "payload": payload,
                },
            )

            print(
                "CHART: IRCTC browser status:",
                result.get("status"),
            )

            if not result.get("ok"):

                error_message = result.get(
                    "error",
                    "Unknown browser fetch error",
                )

                print(
                    "CHART: Browser fetch error:",
                    error_message,
                )

                raise HTTPException(
                    status_code=502,
                    detail=(
                        "IRCTC browser request failed: "
                        f"{error_message}"
                    ),
                )

            status = result.get(
                "status"
            )

            response_text = result.get(
                "text",
                "",
            )

            print(
                "CHART: IRCTC RESPONSE STATUS:",
                status,
            )

            print(
                "CHART: IRCTC RESPONSE BODY:",
                response_text[:3000],
            )

            if status != 200:

                raise HTTPException(
                    status_code=502,
                    detail=(
                        f"IRCTC returned HTTP {status}"
                    ),
                )

            try:

                data = json.loads(
                    response_text
                )

            except json.JSONDecodeError:

                raise HTTPException(
                    status_code=502,
                    detail=(
                        "IRCTC returned invalid JSON"
                    ),
                )

            if not isinstance(data, dict):

                raise HTTPException(
                    status_code=502,
                    detail=(
                        "Unexpected IRCTC "
                        "response format"
                    ),
                )

            return data

    async def close(self):

        print(
            "CHART: Closing Playwright..."
        )

        try:

            if self.context is not None:
                await self.context.close()

        except Exception as exc:

            print(
                "CHART: Context close error:",
                repr(exc),
            )

        try:

            if self.browser is not None:
                await self.browser.close()

        except Exception as exc:

            print(
                "CHART: Browser close error:",
                repr(exc),
            )

        try:

            if self.playwright is not None:
                await self.playwright.stop()

        except Exception as exc:

            print(
                "CHART: Playwright stop error:",
                repr(exc),
            )

        self.page = None
        self.context = None
        self.browser = None
        self.playwright = None


# Global browser
irctc_browser = IRCTCBrowser()


# ============================================================
# REQUEST MODELS
# ============================================================

class ChartTrainRequest(BaseModel):

    train_number: str = Field(
        ...,
        min_length=1,
        max_length=10,
    )

    journey_date: date

    boarding_station: str = Field(
        ...,
        min_length=2,
        max_length=10,
    )


class ChartCoachRequest(BaseModel):

    train_number: str = Field(
        ...,
        min_length=1,
        max_length=10,
    )

    journey_date: date

    boarding_station: str = Field(
        ...,
        min_length=2,
        max_length=10,
    )

    remote_station: str = Field(
        ...,
        min_length=2,
        max_length=10,
    )

    train_source_station: str = Field(
        ...,
        min_length=2,
        max_length=10,
    )

    travel_class: str = Field(
        ...,
        min_length=1,
        max_length=5,
    )

    coach: str = Field(
        ...,
        min_length=1,
        max_length=10,
    )


# ============================================================
# NORMALIZATION HELPERS
# ============================================================

def normalize_train_number(
    value: str,
) -> str:

    return str(value).strip()


def normalize_station(
    value: str,
) -> str:

    return str(value).strip().upper()


def normalize_class(
    value: str,
) -> str:

    return str(value).strip().upper()


def normalize_coach(
    value: str,
) -> str:

    return str(value).strip().upper()


def clean_optional(
    value: Any,
) -> Optional[Any]:

    if value is None:
        return None

    if isinstance(value, str):

        value = value.strip()

        if value == "":
            return None

    return value


def normalize_bool(
    value: Any,
) -> bool:

    if isinstance(value, bool):
        return value

    if isinstance(value, str):

        return value.strip().lower() in {
            "true",
            "1",
            "yes",
            "y",
        }

    if isinstance(value, (int, float)):
        return value != 0

    return False


# ============================================================
# IRCTC POST HELPER
# ============================================================

async def irctc_post(
    url: str,
    payload: dict[str, Any],
) -> dict[str, Any]:

    try:

        return await irctc_browser.post(
            url=url,
            payload=payload,
        )

    except HTTPException:
        raise

    except Exception as exc:

        print(
            "CHART IRCTC BROWSER REQUEST ERROR:",
            repr(exc),
        )

        print(
            "CHART IRCTC URL:",
            url,
        )

        print(
            "CHART IRCTC PAYLOAD:",
            payload,
        )

        raise HTTPException(
            status_code=502,
            detail=(
                "IRCTC browser request failed: "
                f"{str(exc)}"
            ),
        )


# ============================================================
# BERTH STATUS CALCULATOR
# ============================================================

def calculate_berth_status(
    segments: list[dict[str, Any]],
) -> dict[str, Any]:

    occupied_segments: list[
        dict[str, Any]
    ] = []

    vacant_segments: list[
        dict[str, Any]
    ] = []

    for segment in segments:

        occupancy = normalize_bool(
            segment.get("occupancy")
        )

        normalized_segment = {

            "split_no": segment.get(
                "split_no"
            ),

            "from": clean_optional(
                segment.get("from")
            ),

            "to": clean_optional(
                segment.get("to")
            ),

            "quota": clean_optional(
                segment.get("quota")
            ),
        }

        if occupancy:

            occupied_segments.append(
                normalized_segment
            )

        else:

            vacant_segments.append(
                normalized_segment
            )

    total_segments = len(
        segments
    )

    occupied_count = len(
        occupied_segments
    )

    vacant_count = len(
        vacant_segments
    )

    fully_occupied = (
        total_segments > 0
        and occupied_count
        == total_segments
    )

    fully_vacant = (
        total_segments > 0
        and vacant_count
        == total_segments
    )

    partially_occupied = (
        total_segments > 0
        and occupied_count > 0
        and vacant_count > 0
    )

    return {

        "occupied_segments":
            occupied_segments,

        "vacant_segments":
            vacant_segments,

        "total_segments":
            total_segments,

        "occupied_segment_count":
            occupied_count,

        "vacant_segment_count":
            vacant_count,

        "fully_occupied":
            fully_occupied,

        "partially_occupied":
            partially_occupied,

        "fully_vacant":
            fully_vacant,
    }


# ============================================================
# NORMALIZE COACH BERTH
# ============================================================

def normalize_berth(
    berth: dict[str, Any],
) -> dict[str, Any]:

    raw_segments = berth.get(
        "bsd"
    )

    if not isinstance(
        raw_segments,
        list,
    ):

        raw_segments = []

    normalized_segments: list[
        dict[str, Any]
    ] = []

    for segment in raw_segments:

        if not isinstance(
            segment,
            dict,
        ):
            continue

        normalized_segments.append(
            {

                "split_no":
                    segment.get(
                        "splitNo"
                    ),

                "from":
                    clean_optional(
                        segment.get(
                            "from"
                        )
                    ),

                "to":
                    clean_optional(
                        segment.get(
                            "to"
                        )
                    ),

                "quota":
                    clean_optional(
                        segment.get(
                            "quota"
                        )
                    ),

                "occupancy":
                    normalize_bool(
                        segment.get(
                            "occupancy"
                        )
                    ),
            }
        )

    computed_status = (
        calculate_berth_status(
            normalized_segments
        )
    )

    return {

        "berth_no":
            berth.get(
                "berthNo"
            ),

        "berth_code":
            clean_optional(
                berth.get(
                    "berthCode"
                )
            ),

        "cabin_coupe":
            clean_optional(
                berth.get(
                    "cabinCoupe"
                )
            ),

        "cabin_coupe_name_no":
            clean_optional(
                berth.get(
                    "cabinCoupeNameNo"
                )
            ),

        "from":
            clean_optional(
                berth.get(
                    "from"
                )
            ),

        "to":
            clean_optional(
                berth.get(
                    "to"
                )
            ),

        "quota_count_station":
            clean_optional(
                berth.get(
                    "quotaCntStn"
                )
            ),

        "enable":
            normalize_bool(
                berth.get(
                    "enable"
                )
            ),

        "segments":
            normalized_segments,

        "occupied_segments":
            computed_status[
                "occupied_segments"
            ],

        "vacant_segments":
            computed_status[
                "vacant_segments"
            ],

        "total_segments":
            computed_status[
                "total_segments"
            ],

        "occupied_segment_count":
            computed_status[
                "occupied_segment_count"
            ],

        "vacant_segment_count":
            computed_status[
                "vacant_segment_count"
            ],

        "fully_occupied":
            computed_status[
                "fully_occupied"
            ],

        "partially_occupied":
            computed_status[
                "partially_occupied"
            ],

        "fully_vacant":
            computed_status[
                "fully_vacant"
            ],
    }


# ============================================================
# TRAIN COMPOSITION
# ============================================================

@router.post("/train")
async def get_train_chart(
    request: ChartTrainRequest,
):

    train_number = (
        normalize_train_number(
            request.train_number
        )
    )

    boarding_station = (
        normalize_station(
            request.boarding_station
        )
    )

    journey_date = (
        request.journey_date.isoformat()
    )

    payload = {

        "trainNo":
            train_number,

        "jDate":
            journey_date,

        "boardingStation":
            boarding_station,
    }

    response_data = await irctc_post(
        TRAIN_COMPOSITION_URL,
        payload,
    )

    raw_coaches = (
        response_data.get(
            "cdd"
        )
    )

    if not isinstance(
        raw_coaches,
        list,
    ):

        raw_coaches = []

    coaches: list[
        dict[str, Any]
    ] = []

    for coach in raw_coaches:

        if not isinstance(
            coach,
            dict,
        ):
            continue

        coaches.append(
            {

                "coach_name":
                    clean_optional(
                        coach.get(
                            "coachName"
                        )
                    ),

                "class_code":
                    clean_optional(
                        coach.get(
                            "classCode"
                        )
                    ),

                "position_from_engine":
                    coach.get(
                        "positionFromEngine"
                    ),

                "vacant_berths":
                    coach.get(
                        "vacantBerths"
                    ),
            }
        )

    chart_status_raw = (
        response_data.get(
            "chartStatusResponseDto"
        )
    )

    chart_status = None

    if isinstance(
        chart_status_raw,
        dict,
    ):

        chart_status = {

            "messageIndex":
                chart_status_raw.get(
                    "messageIndex"
                ),

            "chartOneFlag":
                chart_status_raw.get(
                    "chartOneFlag"
                ),

            "chartTwoFlag":
                chart_status_raw.get(
                    "chartTwoFlag"
                ),

            "trainStartDate":
                clean_optional(
                    chart_status_raw.get(
                        "trainStartDate"
                    )
                ),

            "remoteStationCode":
                clean_optional(
                    chart_status_raw.get(
                        "remoteStationCode"
                    )
                ),

            "messageType":
                clean_optional(
                    chart_status_raw.get(
                        "messageType"
                    )
                ),
        }

    return {

        "success": True,

        "data": {

            "train_number":
                clean_optional(
                    response_data.get(
                        "trainNo"
                    )
                ) or train_number,

            "train_name":
                clean_optional(
                    response_data.get(
                        "trainName"
                    )
                ),

            "from":
                clean_optional(
                    response_data.get(
                        "from"
                    )
                ),

            "to":
                clean_optional(
                    response_data.get(
                        "to"
                    )
                ),

            "train_start_date":
                clean_optional(
                    response_data.get(
                        "trainStartDate"
                    )
                ),

            "remote_location_chart_date":
                clean_optional(
                    response_data.get(
                        "remoteLocationChartDate"
                    )
                ),

            "remote":
                clean_optional(
                    response_data.get(
                        "remote"
                    )
                ),

            "next_remote":
                clean_optional(
                    response_data.get(
                        "nextRemote"
                    )
                ),

            "available_remote_for_booking":
                clean_optional(
                    response_data.get(
                        "avlRemoteForBooking"
                    )
                ),

            "destination_station":
                clean_optional(
                    response_data.get(
                        "destinationStation"
                    )
                ),

            "chart_one_date":
                clean_optional(
                    response_data.get(
                        "chartOneDate"
                    )
                ),

            "chart_two_date":
                clean_optional(
                    response_data.get(
                        "chartTwoDate"
                    )
                ),

            "chart_status":
                chart_status,

            "error":
                clean_optional(
                    response_data.get(
                        "error"
                    )
                ),

            "coaches":
                coaches,

            "source":
                "IRCTC Online Charts",
        },
    }


# ============================================================
# COACH COMPOSITION / BERTH DETAILS
# ============================================================

@router.post("/coach")
async def get_coach_chart(
    request: ChartCoachRequest,
):

    train_number = (
        normalize_train_number(
            request.train_number
        )
    )

    boarding_station = (
        normalize_station(
            request.boarding_station
        )
    )

    remote_station = (
        normalize_station(
            request.remote_station
        )
    )

    train_source_station = (
        normalize_station(
            request.train_source_station
        )
    )

    travel_class = (
        normalize_class(
            request.travel_class
        )
    )

    coach = (
        normalize_coach(
            request.coach
        )
    )

    journey_date = (
        request.journey_date.isoformat()
    )

    payload = {

        "trainNo":
            train_number,

        "boardingStation":
            boarding_station,

        "remoteStation":
            remote_station,

        "trainSourceStation":
            train_source_station,

        "cls":
            travel_class,

        "coach":
            coach,

        "jDate":
            journey_date,
    }

    response_data = await irctc_post(
        COACH_COMPOSITION_URL,
        payload,
    )

    raw_berths = (
        response_data.get(
            "bdd"
        )
    )

    if not isinstance(
        raw_berths,
        list,
    ):

        raw_berths = []

    berths: list[
        dict[str, Any]
    ] = []

    for berth in raw_berths:

        if not isinstance(
            berth,
            dict,
        ):
            continue

        berths.append(
            normalize_berth(
                berth
            )
        )

    # ========================================================
    # SUMMARY
    # ========================================================

    fully_occupied_count = 0

    partially_occupied_count = 0

    fully_vacant_count = 0

    for berth in berths:

        if berth[
            "fully_occupied"
        ]:

            fully_occupied_count += 1

        elif berth[
            "partially_occupied"
        ]:

            partially_occupied_count += 1

        elif berth[
            "fully_vacant"
        ]:

            fully_vacant_count += 1

    return {

        "success": True,

        "data": {

            "train_number":
                train_number,

            "journey_date":
                journey_date,

            "boarding_station":
                boarding_station,

            "remote_station":
                remote_station,

            "train_source_station":
                train_source_station,

            "class_code":
                travel_class,

            "coach":
                clean_optional(
                    response_data.get(
                        "coachName"
                    )
                ) or coach,

            "error":
                clean_optional(
                    response_data.get(
                        "error"
                    )
                ),

            "berth_count":
                len(berths),

            "berth_summary": {

                "fully_occupied":
                    fully_occupied_count,

                "partially_occupied":
                    partially_occupied_count,

                "fully_vacant":
                    fully_vacant_count,
            },

            "berths":
                berths,

            "source":
                "IRCTC Online Charts",
        },
    }


# ============================================================
# SHUTDOWN
# ============================================================

async def close_irctc_browser():

    await irctc_browser.close()
