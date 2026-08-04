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
SNAPSHOT_CHUNK_YEARS = 5
"""Quake Search rejects oversized result sets with HTTP 400, measured at over 33,000 rows,
so the range is chunked to stay well under that."""


class EmptyCatalogueError(RuntimeError):
    """Raised when GeoNet returns a well formed response containing no events."""


def _chunk_ranges(start: date, end: date, years: int) -> list[tuple[date, date]]:
    """Split [start, end] into consecutive half-open ranges of at most `years` years.

    Consecutive ranges share a boundary so no time is skipped. The final range
    ends exactly at `end`.
    """
    chunks: list[tuple[date, date]] = []
    current = start
    while current < end:
        try:
            nxt = current.replace(year=current.year + years)
        except ValueError:
            nxt = current.replace(year=current.year + years, day=28)
        chunk_end = min(nxt, end)
        chunks.append((current, chunk_end))
        current = chunk_end
    return chunks


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
    """Take the daily full-catalogue snapshot used to build the revision curve.

    Fetches in chunks to avoid hitting HTTP 400 on oversized result sets, then
    deduplicates by publicid (keeping the latest modificationtime) and writes
    atomically.
    """
    paths.SNAPSHOT_DIR.mkdir(parents=True, exist_ok=True)
    destination = paths.SNAPSHOT_DIR / f"catalogue-{today.isoformat()}.parquet"

    # Fetch all chunks and accumulate records
    accumulated: dict[str, dict] = {}
    chunks = _chunk_ranges(SNAPSHOT_START, today, SNAPSHOT_CHUNK_YEARS)

    for chunk_start, chunk_end in chunks:
        url = geonet.build_url(SNAPSHOT_MIN_MAGNITUDE, chunk_start, chunk_end)
        text = fetch(url)
        records = parse.parse_catalogue_csv(text)

        # A well formed HTTP 200 with a header-only body is a known Quake Search
        # failure mode under load. Left unchecked, that chunk's events vanish
        # silently and the remaining chunks write a file indistinguishable from
        # a complete one. Raise immediately rather than only checking the
        # aggregate at the end.
        if not records:
            raise EmptyCatalogueError(f"no events returned for chunk {chunk_start} to {chunk_end}")

        # Accumulate and deduplicate by publicid, keeping latest modificationtime
        for record in records:
            pid = record["publicid"]
            if pid not in accumulated:
                accumulated[pid] = record
            else:
                # Keep the record with the later modificationtime
                existing_mod = accumulated[pid]["modificationtime"]
                new_mod = record["modificationtime"]
                # Compare, handling None values (treat None as oldest)
                if new_mod is not None and (existing_mod is None or new_mod > existing_mod):
                    accumulated[pid] = record

    # Convert back to list
    all_records = list(accumulated.values())

    if not all_records:
        raise EmptyCatalogueError(f"no events returned for {SNAPSHOT_START} to {today}")

    storage.write_parquet_atomic(all_records, Path(destination))
    return destination
