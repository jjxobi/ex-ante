"""HTTP access to the GeoNet Quake Search catalogue export.

Quake Search resets connections under sustained querying, so every request goes
through exponential backoff. A failed pull raises rather than returning partial
data, because a partial catalogue silently corrupts everything downstream.
"""

from __future__ import annotations

import time
from datetime import date

import requests

BBOX = "163.60840,-49.18170,182.98828,-32.28713"
BASE_URL = "https://quakesearch.geonet.org.nz/csv"
TIMEOUT_SECONDS = 600
INITIAL_BACKOFF_SECONDS = 5


class GeoNetError(RuntimeError):
    """Raised when the catalogue cannot be retrieved."""


def build_url(min_magnitude: float, start: date, end: date) -> str:
    """Build a Quake Search CSV export URL for a half-open date interval."""
    return (
        f"{BASE_URL}?bbox={BBOX}"
        f"&minmag={min_magnitude}"
        f"&startdate={start.isoformat()}T00:00:00"
        f"&enddate={end.isoformat()}T00:00:00"
    )


def fetch_csv(
    url: str,
    *,
    session=None,
    max_attempts: int = 6,
    sleep=time.sleep,
) -> str:
    """Fetch CSV text, retrying with exponential backoff.

    Raises GeoNetError if every attempt fails. Never returns a partial body.
    """
    client = session if session is not None else requests.Session()
    last_error: Exception | None = None

    for attempt in range(max_attempts):
        try:
            response = client.get(url, timeout=TIMEOUT_SECONDS)
            response.raise_for_status()
            return response.text
        except Exception as error:
            last_error = error
            if attempt == max_attempts - 1:
                break
            sleep(INITIAL_BACKOFF_SECONDS * 2**attempt)

    raise GeoNetError(f"failed to fetch {url} after {max_attempts} attempts: {last_error}")
