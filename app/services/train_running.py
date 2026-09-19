from __future__ import annotations

import re
import html as html_lib
import time
from datetime import datetime
from typing import Any

import httpx
from bs4 import BeautifulSoup
from bs4.element import Tag


NTES_BASE_URL = "https://enquiry.indianrail.gov.in"
NTES_HOME_URL = f"{NTES_BASE_URL}/mntes/"
NTES_CSRF_URL = f"{NTES_BASE_URL}/mntes/GetCSRFToken"
NTES_RUNNING_URL = (
    f"{NTES_BASE_URL}/mntes/tr"
    "?opt=TrainRunning&subOpt=FindRunningInstance"
)
NTES_FULL_RUNNING_URL = (
    f"{NTES_BASE_URL}/mntes/tr"
    "?opt=TrainRunning&subOpt=fullR"
)

SESSION_TTL_SECONDS = 30 * 60
CSRF_TTL_SECONDS = 30 * 60


class NTESRunningError(Exception):
    """Raised when NTES train running status cannot be fetched."""


def timestamp_ms() -> int:
    return int(time.time() * 1000)


def clean_text(value: str | None) -> str:
    if not value:
        return ""
    return re.sub(r"\s+", " ", value).strip()


def get_classes(element: Tag) -> list[str]:
    value = element.get("class")

    if value is None:
        return []

    if isinstance(value, str):
        return value.split()

    try:
        return [str(item) for item in value]
    except TypeError:
        return []


def has_class(element: Tag, class_name: str) -> bool:
    return class_name in get_classes(element)


def ntes_headers() -> dict[str, str]:
    return {
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/153.0.0.0 Safari/537.36"
        ),
        "Accept": (
            "text/html,application/xhtml+xml,application/xml;"
            "q=0.9,image/avif,image/webp,*/*;q=0.8"
        ),
        "Accept-Language": "en-US,en;q=0.9",
        "Cache-Control": "no-cache",
        "Pragma": "no-cache",
        "Referer": NTES_HOME_URL,
        "Origin": NTES_BASE_URL,
    }


async def initialize_ntes_session(client: httpx.AsyncClient) -> None:
    try:
        response = await client.get(
            NTES_HOME_URL,
            headers=ntes_headers(),
            timeout=60.0,
        )

        print("NTES INIT STATUS:", response.status_code)
        print("NTES INIT URL:", response.url)
        print("NTES INIT LENGTH:", len(response.text))

        response.raise_for_status()

    except Exception as exc:
        print("NTES INIT ERROR:", repr(exc))

        raise NTESRunningError(
            "Unable to initialize NTES session."
        ) from exc


async def get_csrf_token(client: httpx.AsyncClient) -> tuple[str, str]:
    try:
        response = await client.get(
            f"{NTES_CSRF_URL}?t={timestamp_ms()}",
            headers=ntes_headers(),
            timeout=20.0,
        )
        response.raise_for_status()
    except httpx.HTTPError as exc:
        raise NTESRunningError(
            "Unable to fetch NTES CSRF token."
        ) from exc

    soup = BeautifulSoup(response.text, "html.parser")

    csrf_input = soup.find(
        "input",
        attrs={"type": "hidden"},
    )

    if isinstance(csrf_input, Tag):
        name = csrf_input.get("name")
        value = csrf_input.get("value")

        if name and value:
            return str(name), str(value)

    match = re.search(
        r'name=["\']([^"\']+)["\']\s+value=["\']([^"\']+)["\']',
        response.text,
        re.IGNORECASE,
    )

    if match:
        return match.group(1), match.group(2)

    raise NTESRunningError(
        "NTES CSRF token was not found."
    )


def validate_train_number(train_number: str) -> str:
    value = str(train_number).strip()

    if not re.fullmatch(r"\d{5}", value):
        raise NTESRunningError(
            "Train number must be exactly 5 digits."
        )

    if value == "00000":
        raise NTESRunningError(
            "Invalid train number."
        )

    return value


def normalize_journey_date(journey_date: str) -> str:
    value = str(journey_date).strip()

    formats = (
        "%d-%b-%Y",
        "%d-%B-%Y",
        "%d/%m/%Y",
        "%d-%m-%Y",
        "%Y-%m-%d",
    )

    for fmt in formats:
        try:
            parsed = datetime.strptime(value, fmt)
            return parsed.strftime("%d-%b-%Y")
        except ValueError:
            continue

    raise NTESRunningError(
        "Invalid journey date. Use DD-Mon-YYYY."
    )


def parse_ntes_date(value: str) -> str | None:
    value = clean_text(value)

    formats = (
        "%d-%b-%Y",
        "%d-%B-%Y",
        "%d-%b-%Y",
    )

    for fmt in formats:
        try:
            return datetime.strptime(value, fmt).strftime(
                "%d-%b-%Y"
            )
        except ValueError:
            continue

    return None


def extract_active_pane(
    soup: BeautifulSoup,
    journey_date: str,
) -> Tag | None:
    normalized_date = normalize_journey_date(journey_date)

    pane_id = "train" + normalized_date.lower()

    pane = soup.find(
        "div",
        id=pane_id,
    )

    if isinstance(pane, Tag):
        return pane

    # Fallback: find pane containing the requested Start Date
    for candidate in soup.find_all(
        "div",
        class_="tab-pane",
    ):
        if not isinstance(candidate, Tag):
            continue

        start_text = clean_text(
            candidate.get_text(
                " ",
                strip=True,
            )
        )

        if (
            normalized_date.lower() in start_text.lower()
            and "start date" in start_text.lower()
        ):
            return candidate

    return None


def extract_train_name(
    soup: BeautifulSoup,
    train_number: str,
) -> str:
    pattern = re.compile(
        rf"^\s*{re.escape(train_number)}\s+(.+?)\s*$",
        re.IGNORECASE,
    )

    for heading in soup.find_all(["h3", "h4", "h5"]):
        text = clean_text(heading.get_text(" ", strip=True))

        match = pattern.match(text)

        if match:
            name = clean_text(match.group(1))

            if name:
                return name

    for heading in soup.find_all(["h3", "h4", "h5"]):
        text = clean_text(
            heading.get_text(" ", strip=True)
        )

        if not text:
            continue

        if text.lower().startswith("start date"):
            continue

        if text.lower().startswith(
            "yet to start from its source"
        ):
            continue

        if " - " in text and len(text) > 5:
            return text

    return ""


def extract_active_journey_date(
    pane: Tag | None,
    requested_date: str,
) -> str:
    if pane is not None:
        start_date = pane.find(
            string=re.compile(
                r"Start Date\s*:",
                re.IGNORECASE,
            )
        )

        if start_date:
            text = clean_text(
                start_date.parent.get_text(
                    " ",
                    strip=True,
                )
                if isinstance(start_date.parent, Tag)
                else str(start_date)
            )

            match = re.search(
                r"Start Date\s*:\s*"
                r"(\d{1,2}-[A-Za-z]{3}-\d{4})",
                text,
                re.IGNORECASE,
            )

            if match:
                parsed = parse_ntes_date(
                    match.group(1)
                )

                if parsed:
                    return parsed

        pane_id = pane.get("id")

        if isinstance(pane_id, str):
            match = re.search(
                r"train(\d{1,2}-[A-Za-z]{3}-\d{4})",
                pane_id,
                re.IGNORECASE,
            )

            if match:
                parsed = parse_ntes_date(
                    match.group(1)
                )

                if parsed:
                    return parsed

    return normalize_journey_date(requested_date)


def extract_page_status(
    pane: Tag | None,
) -> str | None:
    if pane is None:
        return None

    # Page-level status is inside h6 on NTES.
    for element in pane.find_all("h6"):
        text = clean_text(
            element.get_text(
                " ",
                strip=True,
            )
        )

        if text:
            return text

    return None


def extract_station_code(
    station_block: Tag,
) -> str | None:
    code_element = station_block.find(
        lambda tag: (
            isinstance(tag, Tag)
            and tag.name == "b"
            and bool(
                re.search(
                    r"\b[A-Z0-9]{2,6}\b",
                    clean_text(
                        tag.get_text(
                            " ",
                            strip=True,
                        )
                    ),
                )
            )
        )
    )

    if not isinstance(code_element, Tag):
        return None

    text = clean_text(
        code_element.get_text(
            " ",
            strip=True,
        )
    )

    platform_match = re.search(
        r"\b([A-Z0-9]{2,6})\b\s+PF\s+",
        text,
        re.IGNORECASE,
    )

    if platform_match:
        return platform_match.group(1).upper()

    match = re.search(
        r"\b([A-Z0-9]{2,6})\b",
        text,
    )

    if match:
        return match.group(1).upper()

    return None


def extract_platform(
    station_block: Tag,
) -> str | None:
    text = clean_text(
        station_block.get_text(
            " ",
            strip=True,
        )
    )

    match = re.search(
        r"\bPF\s+([A-Za-z0-9]+)\*?",
        text,
        re.IGNORECASE,
    )

    if not match:
        return None

    return match.group(1)


def extract_station_identity(
    row: Tag,
) -> tuple[str | None, str | None, str | None]:
    """Extract station name, station code and platform.

    NTES uses two different row types:

    * stopRow    -> normal stopping station with platform/code
    * nonStopRow -> a non-stopping station block. Its markup does
                    not contain the normal platform/time structure.

    A nonStopRow is itself the station block, so we parse its bold
    ``STATION - CODE`` value directly and never inspect its nested
    layout divs as independent stations.
    """

    station_name: str | None = None
    station_code: str | None = None
    platform: str | None = None

    classes = get_classes(row)

    # ------------------------------------------------------------
    # NON-STOPPING STATION
    # ------------------------------------------------------------
    # Example NTES markup:
    #   <div class="... nonStopRow ...">
    #       ...
    #       <b>KADIPUR - KDQ</b>
    #       ...
    #       <b>17</b> KMs
    #       ...
    #       <b>Non-Stopping</b>
    #   </div>
    #
    # The outer nonStopRow is the actual station record.
    if "nonStopRow" in classes:
        for bold in row.find_all("b"):
            if not isinstance(bold, Tag):
                continue

            text = clean_text(
                bold.get_text(
                    " ",
                    strip=True,
                )
            )

            if not text:
                continue

            match = re.fullmatch(
                r"(.+?)\s+-\s+([A-Z0-9]{2,6})",
                text,
                re.IGNORECASE,
            )

            if not match:
                continue

            station_name = clean_text(
                match.group(1)
            )
            station_code = match.group(2).upper()
            break

        return (
            station_name,
            station_code,
            None,
        )

    # ------------------------------------------------------------
    # NORMAL STOPPING STATION
    # ------------------------------------------------------------
    # Keep the original NTES stopRow logic separate from the
    # non-stop logic. This prevents values such as "3 Min" from
    # being mistaken for a station name.
    for container in row.find_all("div"):
        if not isinstance(container, Tag):
            continue

        container_classes = get_classes(container)

        style = str(
            container.get("style") or ""
        ).lower().replace(" ", "")

        if (
            "w3-container" not in container_classes
            or "display:flex" not in style
        ):
            continue

        text = clean_text(
            container.get_text(
                " ",
                strip=True,
            )
        )

        if "PF" not in text.upper():
            continue

        # -------------------------
        # Station name
        # -------------------------
        for span in container.find_all("span"):
            if not isinstance(span, Tag):
                continue

            font = span.find("font")

            if not isinstance(font, Tag):
                continue

            bold = font.find("b")

            if not isinstance(bold, Tag):
                continue

            text = clean_text(
                bold.get_text(
                    " ",
                    strip=True,
                )
            )

            if text:
                station_name = text
                break

        # -------------------------
        # Station code + platform
        # -------------------------
        for bold in container.find_all("b"):
            if not isinstance(bold, Tag):
                continue

            text = clean_text(
                bold.get_text(
                    " ",
                    strip=True,
                )
            )

            match = re.search(
                r"\b([A-Z0-9]{2,6})\b"
                r"\s+PF\s+([A-Za-z0-9]+)\*?",
                text,
                re.IGNORECASE,
            )

            if match:
                station_code = match.group(1).upper()
                platform = match.group(2)
                break

        if station_name or station_code:
            break

    return (
        station_name,
        station_code,
        platform,
    )

def extract_distance_km(row: Tag) -> int | float | None:
    """
    Extract station distance from NTES row.

    Examples:
        <b>17</b> KMs
        <b>26</b> KMs
        <b>73</b> KMs
    """
    text = row.get_text(" ", strip=True)

    match = re.search(
        r"\b(\d+(?:\.\d+)?)\s*KMs?\b",
        text,
        re.IGNORECASE,
    )

    if not match:
        return None

    value = float(match.group(1))

    if value.is_integer():
        return int(value)

    return value


TIME_PATTERN = re.compile(
    r"\b\d{1,2}:\d{2}"
    r"(?:\s+\d{1,2}-[A-Za-z]{3})?\*?\b"
)


def extract_time_values(
    row: Tag,
) -> list[str]:
    values: list[str] = []

    timing_containers: list[Tag] = []

    for container in row.find_all("div"):
        if not isinstance(container, Tag):
            continue

        classes = get_classes(container)

        if "w3-container" not in classes:
            continue

        style = str(
            container.get("style") or ""
        ).lower()

        if "width:100px" in style:
            text = clean_text(
                container.get_text(
                    " ",
                    strip=True,
                )
            )

            if TIME_PATTERN.search(text):
                timing_containers.append(
                    container
                )

    for container in timing_containers:
        matches = TIME_PATTERN.findall(
            container.get_text(
                " ",
                strip=True,
            )
        )

        for match in matches:
            value = clean_text(match)

            if value and value not in values:
                values.append(value)

    return values


def normalize_time_value(
    value: str | None,
) -> str | None:
    if not value:
        return None

    value = clean_text(value)
    value = value.rstrip("*").strip()

    return value or None


def extract_times(
    container: Tag | None,
) -> tuple[str | None, str | None]:
    if container is None:
        return None, None

    values: list[str] = []
    actual_value: str | None = None

    # NTES time format:
    # 22:35 14-Sep
    time_pattern = re.compile(
        r"\b\d{1,2}:\d{2}\s+\d{1,2}-[A-Za-z]{3}\b"
    )

    # First collect all time values from this column.
    for text_node in container.stripped_strings:
        text = clean_text(text_node)

        if not text:
            continue

        match = time_pattern.search(text)

        if match:
            value = match.group(0)

            if value not in values:
                values.append(value)



    scheduled: str | None = (
        values[0]
        if values
        else None
    )

    if len(values) >= 2:
        actual_value = values[1]

    # If NTES explicitly marks a dynamic value with '*',
    # prefer that value.
    for element in container.find_all(
        ["b", "span", "font"]
    ):
        text = clean_text(
            element.get_text(
                " ",
                strip=True,
            )
        )

        if "*" not in text:
            continue

        match = time_pattern.search(text)

        if match:
            actual_value = match.group(0)
            break

    actual: str | None = actual_value

    # On-time rows generally contain only one time.
    if actual is None:
        actual = scheduled

    return scheduled, actual

def extract_arrival_departure(
    row: Tag,
) -> tuple[str | None, str | None, str | None, str | None]:
    """
    Extract arrival/departure scheduled and actual times
    from a normal NTES stopping row.

    Non-stopping rows do not have running-time columns, so all
    four time values are intentionally returned as None.
    """

    if has_class(row, "nonStopRow"):
        return None, None, None, None

    time_pattern = re.compile(
        r"\b\d{1,2}:\d{2}\s+\d{1,2}-[A-Za-z]{3}\b"
    )

    timing_columns: list[Tag] = []

    # NTES timing columns have width:100px and text-align:right
    for div in row.find_all("div"):
        if not isinstance(div, Tag):
            continue

        style = str(
            div.get("style") or ""
        ).lower().replace(" ", "")

        if (
            "width:100px" in style
            and "text-align:right" in style
        ):
            timing_columns.append(div)

    if not timing_columns:
        return None, None, None, None

    arrival_column = timing_columns[0]

    departure_column: Tag | None = None

    if len(timing_columns) >= 2:
        departure_column = timing_columns[-1]

    def extract_times(
        container: Tag | None,
    ) -> tuple[str | None, str | None]:

        if container is None:
            return None, None

        values: list[str] = []
        actual_value: str | None = None

        # Collect all NTES time values from this column.
        for text_node in container.stripped_strings:
            text = clean_text(text_node)

            if not text:
                continue

            match = time_pattern.search(text)

            if match:
                value = match.group(0)

                if value not in values:
                    values.append(value)

        # First value = scheduled time.
        scheduled: str | None = (
            values[0]
            if values
            else None
        )

        
        if len(values) >= 2:
            actual_value = values[1]

        # On-time rows can explicitly mark actual with '*'.
        # Prefer the '*' value when NTES provides it.
        for element in container.find_all(
            ["b", "span", "font"]
        ):
            text = clean_text(
                element.get_text(
                    " ",
                    strip=True,
                )
            )

            if "*" not in text:
                continue

            match = time_pattern.search(text)

            if match:
                actual_value = match.group(0)
                break

        # If there is only one time, it is both
        # scheduled and actual.
        actual: str | None = actual_value

        if actual is None:
            actual = scheduled

        return scheduled, actual

    (
        arrival_scheduled,
        arrival_actual,
    ) = extract_times(arrival_column)

    (
        departure_scheduled,
        departure_actual,
    ) = extract_times(departure_column)

    return (
        arrival_scheduled,
        arrival_actual,
        departure_scheduled,
        departure_actual,
    )

def extract_status_and_delay(
    row: Tag,
) -> tuple[str | None, int | None]:
    # A non-stop row is a real station record, but NTES does not
    # provide arrival/departure/delay values for it.
    if has_class(row, "nonStopRow"):
        return "Non-Stopping", None

    text = clean_text(
        row.get_text(
            " ",
            strip=True,
        )
    )

    status: str | None = None
    delay_minutes: int | None = None

    on_time_match = re.search(
        r"\bOn\s+Time\b",
        text,
        re.IGNORECASE,
    )

    if on_time_match:
        status = "On Time"
        return status, None

    delay_match = re.search(
        r"\b(\d+)\s*Min(?:ute)?s?\b",
        text,
        re.IGNORECASE,
    )

    if delay_match:
        delay_minutes = int(
            delay_match.group(1)
        )
        status = f"{delay_minutes} Min"

        return status, delay_minutes

    late_match = re.search(
        r"\b(\d+)\s*Min(?:ute)?s?\s*Late\b",
        text,
        re.IGNORECASE,
    )

    if late_match:
        delay_minutes = int(
            late_match.group(1)
        )
        status = f"{delay_minutes} Min Late"

        return status, delay_minutes

    status_patterns = (
        "Yet to arrive",
        "Arrived",
        "Departed",
        "Cancelled",
        "Skipped",
        "Rescheduled",
    )

    lowered = text.lower()

    for candidate in status_patterns:
        if candidate.lower() in lowered:
            return candidate, delay_minutes

    return None, delay_minutes


def is_current_station_row(row: Tag) -> bool:
    """
    Detect whether an NTES station row is marked as the
    current station.

    NTES uses the `currentStation` class on the active
    station row.
    """

    if has_class(row, "currentStation"):
        return True

    # Some NTES pages may apply the marker to a nested element.
    for element in row.find_all(True):
        if not isinstance(element, Tag):
            continue

        if has_class(element, "currentStation"):
            return True

    return False


def station_row_candidates(
    pane: Tag,
) -> list[Tag]:
    """Return NTES stopping and non-stopping station rows in DOM order."""

    rows: list[Tag] = []

    for row in pane.find_all("div"):
        if not isinstance(row, Tag):
            continue

        classes = get_classes(row)

        # Only the actual row containers are candidates.
        # Nested layout divs do not carry either class.
        if (
            "stopRow" in classes
            or "nonStopRow" in classes
        ):
            rows.append(row)

    return rows



def extract_coach_position(
    soup: BeautifulSoup,
    requested_date: str | None = None,
) -> list[dict[str, Any]]:
    """
    Extract Coach Position directly from NTES cpos modal.

    NTES structure:
        <div id="cpos1BCY15-Sep-2026">
            <table>
                <thead>
                    <tr>
                        <th>Position</th>
                        <th>Coach Type</th>
                        <th>Coach ID</th>
                    </tr>
                </thead>
                <tbody>
                    <tr>
                        <td>1</td>
                        <td>LOCOMOTIVE</td>
                        <td>ENGINE</td>
                    </tr>
                    ...
                </tbody>
            </table>
        </div>
    """

    # print("\n" + "-" * 80)
    # print("[NTES DEBUG] COACH POSITION EXTRACTOR")

    if not requested_date:
        requested_date = ""

    requested_date = str(requested_date).strip()

    # print(
        # f"[NTES DEBUG] requested_date={requested_date}"
    # )

    # ------------------------------------------------------------------
    # STEP 1: Find all cpos elements
    # ------------------------------------------------------------------

    cpos_elements = []

    for element in soup.find_all(
        lambda tag: (
            tag.has_attr("id")
            and str(tag.get("id", "")).lower().startswith("cpos")
        )
    ):
        cpos_elements.append(element)

    # print(
        # "[NTES DEBUG] total cpos elements="
        # f"{len(cpos_elements)}"
    # )

    if not cpos_elements:
        # print("[NTES DEBUG] NO cpos elements found")
        return []

    # ------------------------------------------------------------------
    # STEP 2: Filter cpos by requested date
    # ------------------------------------------------------------------

    requested_cpos = []

    if requested_date:
        for element in cpos_elements:
            element_id = str(
                element.get("id", "")
            )

            if requested_date in element_id:
                requested_cpos.append(element)

    # print(
        # "[NTES DEBUG] requested-date cpos="
        # f"{len(requested_cpos)} for {requested_date}"
    # )

    # If date filtering somehow fails, don't immediately give up.
    # cpos elements themselves are already highly specific.
    candidates = requested_cpos or cpos_elements

    # ------------------------------------------------------------------
    # STEP 3: Parse one Coach Position table
    # ------------------------------------------------------------------

    def parse_coach_table(table: Tag) -> list[dict[str, Any]]:
        """
        Parse the Coach Position table from the actual NTES HTTP HTML.

        IMPORTANT:
        In the HTTP response, NTES may put the ENTIRE coach-position
        sequence inside ONE <td>/<tr>, for example:

            ENG ENG 0 SLRD SLRD 1 GEN GEN 2 ... SL S1 5 ...

        So parsing individual <td> elements is not sufficient.

        The actual repeating format is:

            COACH_TYPE  COACH_ID  POSITION

        Example:

            ENG  ENG  0
            SLRD SLRD 1
            SL   S1   5
            2A   A1   10
            3A   B1   11
        """

        rows = table.find_all("tr")

        if not rows:
            return []

        result: list[dict[str, Any]] = []

        # print(
            # "[NTES DEBUG] parsing coach table "
            # f"rows={len(rows)}"
        # )

        # ==============================================================
        # STEP 1: Extract text from every row
        # ==============================================================

        for row_index, row in enumerate(rows):

            row_text = clean_text(
                row.get_text(
                    " ",
                    strip=True,
                )
            )

            if not row_text:
                continue

            # print(
                # "[NTES DEBUG] "
                # f"row={row_index} "
                # f"raw_text={row_text[:1000]}"
            # )

            # ==========================================================
            # STEP 2:
            #
            # NTES sometimes gives us:
            #
            # ENG ENG 0 SLRD SLRD 1 GEN GEN 2 ...
            #
            # as ONE text block.
            #
            # Extract every:
            #
            # TYPE ID POSITION
            #
            # triple.
            #
            # The regex intentionally stops naturally when it reaches
            # "Divyangjan Coach at Position".
            # ==========================================================

            coach_pattern = re.compile(
                r"\b"
                r"([A-Za-z0-9][A-Za-z0-9_-]*)"
                r"\s+"
                r"([A-Za-z0-9][A-Za-z0-9_-]*)"
                r"\s+"
                r"(\d+)"
                r"\b"
            )

            matches = list(
                coach_pattern.finditer(
                    row_text
                )
            )

            # print(
                # "[NTES DEBUG] coach triple matches="
                # f"{len(matches)}"
            # )

            for match in matches:

                coach_type = clean_text(
                    match.group(1)
                )

                coach_id = clean_text(
                    match.group(2)
                )

                position_text = clean_text(
                    match.group(3)
                )

                if not coach_type:
                    continue

                if not coach_id:
                    continue

                if not position_text:
                    continue

                try:
                    position = int(
                        position_text
                    )
                except ValueError:
                    continue

                result.append(
                    {
                        "position": position,
                        "coach_type": coach_type,
                        "coach_id": coach_id,
                    }
                )

        # ==============================================================
        # STEP 3: Remove duplicates
        # ==============================================================

        if not result:
            return []

        unique_coaches: list[
            dict[str, Any]
        ] = []

        seen_positions: set[int] = set()

        for coach in result:

            position = coach.get(
                "position"
            )

            if not isinstance(
                position,
                int,
            ):
                continue

            if position in seen_positions:
                continue

            seen_positions.add(
                position
            )

            unique_coaches.append(
                coach
            )

        # ==============================================================
        # STEP 4: Sort by coach position
        # ==============================================================

        unique_coaches.sort(
            key=lambda item: item[
                "position"
            ]
        )

        # print(
            # "[NTES DEBUG] parsed coach count="
            # f"{len(unique_coaches)}"
        # )

        return unique_coaches

    # ------------------------------------------------------------------
    # STEP 4: Search requested-date cpos
    # ------------------------------------------------------------------

    for cpos in candidates:

        cpos_id = str(
            cpos.get("id", "")
        )

        tables = cpos.find_all("table")

        # print(
            # "[NTES DEBUG] checking "
            # f"cpos={cpos_id} "
            # f"tables={len(tables)}"
        # )

        if not tables:
            continue

        # ==============================================================
        # NTES Coach Position modal normally contains one table.
        # Try every table in case NTES changes its HTML structure.
        # ==============================================================

        for table_index, table in enumerate(
            tables
        ):

            coaches = parse_coach_table(
                table
            )

            if coaches:

                # print(
                    # "[NTES DEBUG] COACH TABLE SUCCESS "
                    # f"cpos={cpos_id} "
                    # f"table_index={table_index} "
                    # f"count={len(coaches)}"
                # )

                for coach in coaches:

                    # print(
                        # "[NTES DEBUG] "
                        # f"position={coach.get('position')} "
                        # f"type={coach.get('coach_type')} "
                        # f"id={coach.get('coach_id')}"
                    # )

                    pass

                return coaches

            # ----------------------------------------------------------
            # Debug only when parsing failed.
            # ----------------------------------------------------------

            if table_index == 0:

                # print(
                    # "[NTES DEBUG] FIRST COACH TABLE "
                    # "DID NOT PARSE"
                # )

                raw_rows = table.find_all(
                    "tr"
                )

                # print(
                    # "[NTES DEBUG] raw row count="
                    # f"{len(raw_rows)}"
                # )

                for row_index, row in enumerate(
                    raw_rows[:10]
                ):

                    pass

                    # print(
                        # "[NTES DEBUG] ROW "
                        # f"{row_index}: "
                        # f"{repr(row.get_text(' | ', strip=True))}"
                    # )

    # ------------------------------------------------------------------
    # STEP 5: Last fallback - search all cpos
    # ------------------------------------------------------------------

    if candidates != cpos_elements:

        # print(
            # "[NTES DEBUG] requested-date search failed, "
            # "trying all cpos elements"
        # )

        for cpos in cpos_elements:

            cpos_id = str(
                cpos.get("id", "")
            )

            tables = cpos.find_all(
                "table"
            )

            if not tables:
                continue

            for table_index, table in enumerate(
                tables
            ):

                coaches = parse_coach_table(
                    table
                )

                if coaches:

                    # print(
                        # "[NTES DEBUG] FALLBACK COACH "
                        # "TABLE SUCCESS "
                        # f"cpos={cpos_id} "
                        # f"table_index={table_index} "
                        # f"count={len(coaches)}"
                    # )

                    for coach in coaches:
                        pass

                    return coaches

    # ------------------------------------------------------------------
    # Nothing found
    # ------------------------------------------------------------------

    # print(
        # "[NTES DEBUG] NO VALID COACH TABLE FOUND"
    # )

    return []


def parse_running_html(
    html: str,
    train_number: str,
    requested_date: str,
) -> dict[str, Any]:
    soup = BeautifulSoup(
        html,
        "html.parser",
    )

    active_pane = extract_active_pane( soup, requested_date)
    
  

    if active_pane is None:
        raise NTESRunningError(
            "NTES active journey pane was not found."
        )

    journey_date = extract_active_journey_date(
        active_pane,
        requested_date,
    )

    train_name = extract_train_name(
        soup,
        train_number,
    )

    page_status = extract_page_status(
        active_pane
    )

    coach_position = extract_coach_position(
        soup,
        requested_date,
    )

    rows = station_row_candidates(
        active_pane
    )

    stations: list[dict[str, Any]] = []

    for sequence, row in enumerate(
        rows,
        start=1,
    ):
        (
            station_name,
            station_code,
            platform,
        ) = extract_station_identity(row)

        if not station_name:
            continue

        (
            arrival_time,
            arrival_actual,
            departure_time,
            departure_actual,
        ) = (
            extract_arrival_departure(row)
        )

        status, delay_minutes = (
            extract_status_and_delay(row)
        )

        current = is_current_station_row(
            row
        )

        scheduled_time = (
            arrival_time
            or departure_time
        )

        actual_time = (
            departure_actual
            or arrival_actual
        )

        distance_km = extract_distance_km(row)

        if current:
            current_station = {
                "station_name": station_name,
                "station_code": station_code,
                "platform": platform,
                "sequence": sequence,
            }

        stations.append(
            {
                "station_name": station_name,
                "station_code": station_code,
                "platform": platform,
                "arrival_time": arrival_time,
                "departure_time": departure_time,
                "scheduled_time": scheduled_time,
                "actual_time": actual_time,
                "delay_minutes": delay_minutes,
                "status": status,
                "sequence": sequence,
                "is_current": current,
                "distance_km": distance_km,
            }
        )

    current_station = next(
        (
            station
            for station in stations
            if station["is_current"]
        ),
        None,
    )

    return {
        "train_number": train_number,
        "journey_date": journey_date,
        "train_name": train_name or None,
        "status": page_status,
        "current_station": current_station,
        "stations": stations,
        "coach_position": coach_position,
        "source": "NTES",
        "fetched_at": int(time.time()),
    }


async def fetch_running_status(
    train_number: str,
    journey_date: str,
) -> dict[str, Any]:
    train_number = validate_train_number(
        train_number
    )

    journey_date = normalize_journey_date(
        journey_date
    )

    # print("\n" + "=" * 80)
    # print("[NTES DEBUG] START TRAIN RUNNING")
    # print(
        # f"[NTES DEBUG] train_number={train_number}"
    # )
    # print(
        # f"[NTES DEBUG] journey_date={journey_date}"
    # )
    # print("=" * 80)

    async with httpx.AsyncClient(
        follow_redirects=True,
        headers=ntes_headers(),
    ) as client:

        # -------------------------------------------------
        # 1. INITIALIZE NTES SESSION
        # -------------------------------------------------
        # print(
            # "[NTES DEBUG] Initializing NTES session..."
        # )

        await initialize_ntes_session(client)

        # print(
            # "[NTES DEBUG] NTES session initialized."
        # )

        # -------------------------------------------------
        # 2. GET CSRF FOR FindRunningInstance
        # -------------------------------------------------
        csrf_name, csrf_value = (
            await get_csrf_token(client)
        )

        # print(
            # "[NTES DEBUG] CSRF received:"
        # )
        # print(
            # f"[NTES DEBUG] csrf_name={csrf_name}"
        # )
        # print(
            # "[NTES DEBUG] csrf_value="
            # + str(csrf_value)[:20]
            # + "..."
        # )

        form_data = {
            "lan": "en",
            "jDate": journey_date,
            "trainNo": train_number,
            csrf_name: csrf_value,
        }

        # -------------------------------------------------
        # 3. FindRunningInstance
        # -------------------------------------------------
        # print("\n" + "-" * 80)
        # print(
            # "[NTES DEBUG] STEP 1: FindRunningInstance"
        # )
        # print(
            # f"[NTES DEBUG] URL={NTES_RUNNING_URL}"
        # )
        # print(
            # f"[NTES DEBUG] form_data="
            # f"{ {k: v for k, v in form_data.items() if k != csrf_name} }"
        # )

        try:
            response = await client.post(
                NTES_RUNNING_URL,
                data=form_data,
                headers={
                    **ntes_headers(),
                    "Content-Type": (
                        "application/x-www-form-urlencoded"
                    ),
                },
                timeout=30.0,
            )

            response.raise_for_status()

        except httpx.HTTPError as exc:
            # print(
                # "[NTES DEBUG] FindRunningInstance FAILED"
            # )
            # print(
                # f"[NTES DEBUG] ERROR={exc}"
            # )
            raise NTESRunningError(
                "NTES train running request failed."
            ) from exc

        # print(
            # "[NTES DEBUG] FindRunningInstance SUCCESS"
        # )
        # print(
            # f"[NTES DEBUG] status_code={response.status_code}"
        # )
        # print(
            # f"[NTES DEBUG] final_url={response.url}"
        # )
        # print(
            # f"[NTES DEBUG] response_length="
            # f"{len(response.text)}"
        # )

        # -------------------------------------------------
        # 4. CHECK WHETHER COACH POSITION ALREADY EXISTS
        # -------------------------------------------------
        running_html = response.text

        # print(
            # "[NTES DEBUG] FindRunningInstance contains "
            # f"'Coach Position'="
            # f"{'Coach Position' in running_html}"
        # )

        # print(
            # "[NTES DEBUG] FindRunningInstance contains "
            # f"'cpos'="
            # f"{'cpos' in running_html.lower()}"
        # )

        # Existing running parser
        result = parse_running_html(
            running_html,
            train_number,
            journey_date,
        )

        # print(
            # "[NTES DEBUG] Running status parsed."
        # )
        # print(
            # f"[NTES DEBUG] stations="
            # f"{len(result.get('stations', []))}"
        # )

        # -------------------------------------------------
        # 5. FINAL COACH POSITION RESULT
        # -------------------------------------------------
        # Coach Position is already embedded in FindRunningInstance.
        # Do NOT call fullR here: fullR does not contain coach modals and
        # would incorrectly overwrite a valid result with an empty list.
        coach_position = result.get("coach_position")
        if not isinstance(coach_position, list):
            coach_position = []
            result["coach_position"] = coach_position

        # print("\n" + "-" * 80)
        # print("[NTES DEBUG] FINAL COACH POSITION")
        # print(f"[NTES DEBUG] coach_position_count={len(coach_position)}")
        for coach in coach_position:
            # print(
                # "[NTES DEBUG] coach="
                # f"position={coach.get('position')} "
                # f"type={coach.get('coach_type')} "
                # f"id={coach.get('coach_id')}"
            # )
            pass

        # print("\n" + "=" * 80)
        # print("[NTES DEBUG] COMPLETE")
        # print("=" * 80 + "\n")

        return result

