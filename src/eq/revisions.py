"""Daily revision diffing.

GeoNet does not expose per-event revision history, so the only way to build a
magnitude-revision-versus-time curve is to snapshot the catalogue daily and diff
consecutive snapshots.

Full snapshots are far too large to commit daily, so they stay local and
ephemeral. The diff is what gets committed: it is the actual content of the
curve and costs a few kilobytes a day.
"""

from __future__ import annotations

from pathlib import Path

from eq import storage

TRACKED_FIELDS = (
    "magnitude",
    "depth",
    "longitude",
    "latitude",
    "evaluationstatus",
)


def _format(value) -> str:
    """Render a value for stable comparison and storage."""
    if isinstance(value, float):
        return f"{value:g}"
    return str(value)


def diff_catalogues(previous: list[dict], current: list[dict]) -> list[dict]:
    """Compare two catalogue states. One row per new, withdrawn or changed field."""
    before = {row["publicid"]: row for row in previous}
    after = {row["publicid"]: row for row in current}
    rows: list[dict] = []

    for publicid, row in after.items():
        if publicid not in before:
            rows.append(
                {
                    "publicid": publicid,
                    "change_kind": "new",
                    "field": "",
                    "old_value": "",
                    "new_value": "",
                }
            )
            continue

        for field in TRACKED_FIELDS:
            old = _format(before[publicid].get(field))
            new = _format(row.get(field))
            if old != new:
                rows.append(
                    {
                        "publicid": publicid,
                        "change_kind": "revised",
                        "field": field,
                        "old_value": old,
                        "new_value": new,
                    }
                )

    for publicid in before:
        if publicid not in after:
            rows.append(
                {
                    "publicid": publicid,
                    "change_kind": "withdrawn",
                    "field": "",
                    "old_value": "",
                    "new_value": "",
                }
            )

    return rows


def write_daily_diff(previous_path: Path, current_path: Path, destination: Path) -> int:
    """Diff two snapshot files and write the result. Returns rows written.

    Writes nothing when there are no changes, so a quiet day leaves no file
    rather than an empty one.
    """
    rows = diff_catalogues(
        storage.read_parquet(previous_path), storage.read_parquet(current_path)
    )
    if not rows:
        return 0
    storage.write_parquet_atomic(rows, Path(destination))
    return len(rows)
