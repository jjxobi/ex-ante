"""Catalogue ingest orchestration.

Two jobs live here. Delta ingest pulls a date range into parquet. The full
snapshot pulls the whole catalogue once a day and keeps it, which is the only
way to build the magnitude-revision-versus-time curve: GeoNet's per-event
history endpoint returns no revisions, so the curve can only be built forward
by snapshotting and diffing.

A failed or empty pull raises. It never writes, because a partial catalogue
silently corrupts every number downstream.
"""

from __future__ import annotations

from datetime import date
from pathlib import Path

from eq import geonet, parse, paths, storage

SNAPSHOT_START = date(2005, 1, 1)
SNAPSHOT_MIN_MAGNITUDE = 3.0


class EmptyCatalogueError(RuntimeError):
    """Raised when GeoNet returns a well formed response containing no events."""


def ingest_range(
    start: date,
    end: date,
    min_magnitude: float,
    destination: Path,
    *,
    fetch=geonet.fetch_csv,
) -> int:
    """Fetch one date range and write it to parquet. Returns records written."""
    url = geonet.build_url(min_magnitude, start, end)
    text = fetch(url)
    records = parse.parse_catalogue_csv(text)

    if not records:
        raise EmptyCatalogueError(f"no events returned for {start} to {end}")

    storage.write_parquet_atomic(records, Path(destination))
    return len(records)


def snapshot_full_catalogue(today: date, *, fetch=geonet.fetch_csv) -> Path:
    """Take the daily full-catalogue snapshot used to build the revision curve."""
    paths.SNAPSHOT_DIR.mkdir(parents=True, exist_ok=True)
    destination = paths.SNAPSHOT_DIR / f"catalogue-{today.isoformat()}.parquet"
    ingest_range(
        SNAPSHOT_START, today, SNAPSHOT_MIN_MAGNITUDE, destination, fetch=fetch
    )
    return destination
