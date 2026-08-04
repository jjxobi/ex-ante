"""Pure parsing of GeoNet catalogue CSV into typed records.

No IO happens here. Keeping parsing separate from fetching means the awkward
cases (the antimeridian, missing magnitudes, assigned depths) are testable
without touching the network.
"""

from __future__ import annotations

import csv
import io
from datetime import datetime, timezone

COLUMNS = (
    "publicid",
    "origintime",
    "modificationtime",
    "longitude",
    "latitude",
    "magnitude",
    "depth",
    "magnitudetype",
    "depthtype",
    "evaluationstatus",
    "evaluationmode",
)

REQUIRED = ("publicid", "origintime", "longitude", "latitude", "magnitude", "depth")


def normalise_longitude(value: float) -> float:
    """Put longitude on the continuous [163.6, 183.0] convention.

    New Zealand seismicity crosses the antimeridian, so GeoNet reports some
    events with negative longitude. Wrapping would put a discontinuity through
    the Kermadec arc, so negative values are unwrapped by adding 360 instead.
    """
    return value + 360.0 if value < 0 else value


def _parse_timestamp(raw: str) -> datetime | None:
    if not raw:
        return None
    return datetime.fromisoformat(raw.replace("Z", "+00:00")).astimezone(timezone.utc)


def parse_catalogue_csv(text: str) -> list[dict]:
    """Convert Quake Search CSV text into typed records.

    Rows missing any required field are skipped rather than guessed at.
    """
    if not text.strip():
        return []

    records: list[dict] = []
    for row in csv.DictReader(io.StringIO(text)):
        if any(not row.get(field) for field in REQUIRED):
            continue
        records.append(
            {
                "publicid": row["publicid"],
                "origintime": _parse_timestamp(row["origintime"]),
                "modificationtime": _parse_timestamp(row.get("modificationtime", "")),
                "longitude": normalise_longitude(float(row["longitude"])),
                "latitude": float(row["latitude"]),
                "magnitude": float(row["magnitude"]),
                "depth": float(row["depth"]),
                "magnitudetype": row.get("magnitudetype", ""),
                "depthtype": row.get("depthtype", ""),
                "evaluationstatus": row.get("evaluationstatus", ""),
                "evaluationmode": row.get("evaluationmode", ""),
            }
        )
    return records
