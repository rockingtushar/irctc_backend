import asyncio
import json
from pathlib import Path

from sqlalchemy import delete
from app.database import AsyncSessionLocal
from app.models import Station


# ============================================================
# FILE LOCATION
# ============================================================

BASE_DIR = Path(__file__).resolve().parent.parent

STATIONS_JSON = BASE_DIR / "data" / "stations.json"


# ============================================================
# LOAD STATIONS.JSON
# ============================================================

def load_stations() -> list[dict[str, str]]:

    with open(
        STATIONS_JSON,
        "r",
        encoding="utf-8",
    ) as file:
        raw_data = json.load(file)

    stations: list[dict[str, str]] = []

    # --------------------------------------------------------
    # Keep track of station codes already seen.
    # --------------------------------------------------------

    seen_codes: set[str] = set()

    duplicate_count = 0

    for item in raw_data:

        if not isinstance(item, str):
            continue

        item = item.strip()

        if not item:
            continue

        # Expected format:
        #
        # BABRALA - BBA
        # SONDHA ROAD - SCN
        #
        if " - " not in item:

            # print(
            #     f"Skipping invalid station format: {item}"
            # )

            continue

        # ----------------------------------------------------
        # rsplit is important because station name itself
        # may theoretically contain a hyphen.
        # ----------------------------------------------------

        station_name, station_code = item.rsplit(
            " - ",
            1,
        )

        station_name = station_name.strip()
        station_code = station_code.strip().upper()

        if not station_name or not station_code:
            continue

        # ----------------------------------------------------
        # DUPLICATE CODE CHECK
        # ----------------------------------------------------

        if station_code in seen_codes:

            duplicate_count += 1

            # print(
            #     f"Skipping duplicate station code: "
            #     f"{station_code} -> {station_name}"
            # )

            continue

        seen_codes.add(station_code)

        stations.append(
            {
                "name": station_name,
                "code": station_code,
            }
        )

    # print(
    #     f"Duplicate station codes skipped: "
    #     f"{duplicate_count}"
    # )

    return stations


# ============================================================
# IMPORT INTO DATABASE
# ============================================================

async def import_stations() -> None:

    stations = load_stations()

    # print(
    #     f"Unique stations ready for import: "
    #     f"{len(stations)}"
    # )

    if not stations:

        # print(
        #     "No stations found. Nothing to import."
        # )

        return

    async with AsyncSessionLocal() as db:

        try:

            # ------------------------------------------------
            # DELETE OLD STATION ROWS
            # ------------------------------------------------

            await db.execute(
                delete(Station)
            )

            # ------------------------------------------------
            # CREATE NEW STATION OBJECTS
            # ------------------------------------------------

            station_objects = [
                Station(
                    station_code=station["code"],
                    station_name=station["name"],
                    region_code=None,
                )
                for station in stations
            ]

            # ------------------------------------------------
            # BULK INSERT
            # ------------------------------------------------

            db.add_all(station_objects)

            await db.commit()

            # print(
            #     f"Successfully imported "
            #     f"{len(station_objects)} stations."
            # )

        except Exception:

            # ------------------------------------------------
            # IMPORTANT:
            # If anything goes wrong, rollback so we don't
            # leave the transaction in a failed state.
            # ------------------------------------------------

            await db.rollback()

            raise


# ============================================================
# ENTRY POINT
# ============================================================

if __name__ == "__main__":

    asyncio.run(
        import_stations()
    )