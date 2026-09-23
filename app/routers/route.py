import re
from typing import Any

import httpx
from bs4 import BeautifulSoup
from fastapi import APIRouter, HTTPException


router = APIRouter(
    prefix="/train",
    tags=["Train Route"],
)

NTES_QUERY_URL = "https://enquiry.indianrail.gov.in/mntes/q"
NTES_REFERER = "https://enquiry.indianrail.gov.in/mntes/"
NTES_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/153.0.0.0 Safari/537.36"
)

STATION_CODE_RE = re.compile(r"^[A-Z0-9]{2,6}$")
MARKER_WORDS = {
    "SRC",
    "DST",
    "SOURCE",
    "DESTINATION",
    "ORIGIN",
}


def _clean_text(value: str | None) -> str:
    if not value:
        return ""
    return " ".join(value.split())


def _looks_like_station_code(value: str) -> bool:
    value = _clean_text(value).upper()
    if not STATION_CODE_RE.fullmatch(value):
        return False
    if value in MARKER_WORDS:
        return False
    return True


def _extract_station_from_stop_row(row: Any, sequence: int) -> dict[str, Any] | None:
    # NTES renders the stopping station as two adjacent <b> values:
    #   <b>JAYNAGAR</b>
    #   <b>JYG <span>PF 1</span></b>
    # Keep the parser tied to the actual .stopRow structure and ignore
    # .nonStopRow completely.
    bold_values = [_clean_text(tag.get_text(" ", strip=True)) for tag in row.find_all("b")]

    code_index = None
    code = None
    for index, value in enumerate(bold_values):
        tokens = value.upper().split()
        for token in tokens:
            if _looks_like_station_code(token):
                code_index = index
                code = token
                break
        if code:
            break

    if not code:
        return None

    # Usually the preceding bold value is the station name.
    name = ""
    for index in range((code_index or 0) - 1, -1, -1):
        candidate = bold_values[index]
        if candidate and candidate.upper() not in MARKER_WORDS and not _looks_like_station_code(candidate):
            # Ignore obvious numeric/coach-position fragments.
            if not candidate.isdigit():
                name = candidate
                break

    # Fallback: derive station name from the first meaningful bold value.
    if not name:
        for candidate in bold_values:
            upper = candidate.upper()
            if candidate and upper not in MARKER_WORDS and not candidate.isdigit() and not _looks_like_station_code(candidate):
                name = candidate
                break

    row_text = _clean_text(row.get_text(" ", strip=True))
    marker = None
    upper_row_text = row_text.upper()
    if re.search(r"\bSRC\b", upper_row_text):
        marker = "SRC"
    elif re.search(r"\bDST\b", upper_row_text):
        marker = "DST"

    # Pull useful schedule values when present. These are deliberately
    # best-effort because the NTES HTML layout can vary by train/date.
    distance = None
    distance_match = re.search(r"\b([0-9]+)\s*KMs?\b", row_text, re.IGNORECASE)
    if distance_match:
        distance = int(distance_match.group(1))

    return {
        "sequence": sequence,
        "code": code,
        "name": name or None,
        "marker": marker,
        "distance": distance,
    }


def _extract_station_from_non_stop_row(row: Any, sequence: int) -> dict[str, Any] | None:
    # NTES non-stopping rows use markup such as:
    #   <b>KORAHIA - KRHA</b>
    #   <b>7</b> KMs
    # They are included for route inspection but must remain distinguishable
    # from actual stopping stations.
    bold_values = [_clean_text(tag.get_text(" ", strip=True)) for tag in row.find_all("b")]
    if not bold_values:
        return None

    station_text = next((v for v in bold_values if " - " in v), "")
    if not station_text:
        return None

    name, code = [part.strip() for part in station_text.rsplit(" - ", 1)]
    if not name or not _looks_like_station_code(code):
        return None

    row_text = _clean_text(row.get_text(" ", strip=True))
    distance = None
    distance_match = re.search(r"\b([0-9]+)\s*KMs?\b", row_text, re.IGNORECASE)
    if distance_match:
        distance = int(distance_match.group(1))

    return {
        "sequence": sequence,
        "code": code,
        "name": name,
        "marker": None,
        "distance": distance,
        "station_type": "non_stopping",
        "stops": False,
    }


def parse_ntes_route(html: str) -> list[dict[str, Any]]:
    soup = BeautifulSoup(html, "html.parser")
    stations: list[dict[str, Any]] = []

    # Preserve the exact DOM order because NTES places stopping and
    # non-stopping rows together along the train's route.
    route_rows = soup.select("div.stopRow, div.nonStopRow")

    for row in route_rows:
        classes = set(row.get("class") or [])
        sequence = len(stations) + 1

        if "stopRow" in classes:
            station = _extract_station_from_stop_row(row, sequence)
            if station:
                station["station_type"] = "stopping"
                station["stops"] = True
                stations.append(station)

        elif "nonStopRow" in classes:
            station = _extract_station_from_non_stop_row(row, sequence)
            if station:
                stations.append(station)

    return stations


@router.get("/route/{train_number}")
async def get_ntes_train_route(train_number: str):
    """Testing endpoint: return the ordered stopping-station route from NTES."""

    train_number = train_number.strip()
    if not train_number:
        raise HTTPException(status_code=400, detail="Train number is required")
    if not train_number.isdigit():
        raise HTTPException(status_code=400, detail="Train number must contain only digits")

    headers = {
        "User-Agent": NTES_USER_AGENT,
        "Referer": NTES_REFERER,
        "X-Requested-With": "XMLHttpRequest",
        "Accept": "text/html, */*;q=0.01",
        "Content-Type": "application/x-www-form-urlencoded; charset=UTF-8",
    }

    try:
        async with httpx.AsyncClient(
            timeout=30.0,
            follow_redirects=True,
            headers=headers,
        ) as client:
            response = await client.post(
                NTES_QUERY_URL,
                data={
                    "trainNo": train_number,
                    "opt": "TrainSchedule",
                    "subOpt": "show",
                },
            )

            print("NTES STATUS:", response.status_code)
            print("NTES FINAL URL:", response.url)
            print("NTES RESPONSE LENGTH:", len(response.text))
            print(response.text[:3000])
            
    except httpx.HTTPError as exc:
        raise HTTPException(
            status_code=502,
            detail=f"NTES route request failed: {exc.__class__.__name__}",
        ) from exc

    if response.status_code != 200:
        raise HTTPException(
            status_code=502,
            detail=f"NTES returned HTTP {response.status_code}",
        )

    stations = parse_ntes_route(response.text)

    if not stations:
        raise HTTPException(
            status_code=404,
            detail="No stopping stations found in NTES TrainSchedule response",
        )

    return {
        "train_number": train_number,
        "source": "NTES TrainSchedule",
        "total_stations": len(stations),
        "stopping_stations": sum(1 for s in stations if s.get("stops")),
        "non_stopping_stations": sum(1 for s in stations if not s.get("stops")),
        "stations": stations,
    }
