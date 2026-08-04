from datetime import date

import pytest

from eq import revisions, storage


def event(pid: str, magnitude: float = 3.5, depth: float = 12.0, status: str = "preliminary") -> dict:
    return {
        "publicid": pid,
        "origintime": "2026-01-01T00:00:00Z",
        "modificationtime": "2026-01-02T00:00:00Z",
        "longitude": 175.0,
        "latitude": -41.0,
        "magnitude": magnitude,
        "depth": depth,
        "magnitudetype": "MLv",
        "depthtype": "",
        "evaluationstatus": status,
        "evaluationmode": "manual",
    }


def test_no_changes_produces_no_rows():
    catalogue = [event("a"), event("b")]
    assert revisions.diff_catalogues(catalogue, catalogue) == []


def test_new_event_is_reported_as_new():
    rows = revisions.diff_catalogues([event("a")], [event("a"), event("b")])
    assert len(rows) == 1
    assert rows[0]["publicid"] == "b"
    assert rows[0]["change_kind"] == "new"


def test_magnitude_revision_is_reported():
    rows = revisions.diff_catalogues([event("a", magnitude=3.4)], [event("a", magnitude=3.6)])
    assert len(rows) == 1
    assert rows[0]["change_kind"] == "revised"
    assert rows[0]["field"] == "magnitude"
    assert rows[0]["old_value"] == "3.4"
    assert rows[0]["new_value"] == "3.6"


def test_multiple_field_changes_produce_one_row_each():
    rows = revisions.diff_catalogues(
        [event("a", magnitude=3.4, depth=12.0)],
        [event("a", magnitude=3.6, depth=33.0)],
    )
    assert {r["field"] for r in rows} == {"magnitude", "depth"}


def test_evaluation_status_change_is_tracked():
    rows = revisions.diff_catalogues(
        [event("a", status="preliminary")], [event("a", status="confirmed")]
    )
    assert rows[0]["field"] == "evaluationstatus"


def test_untracked_field_change_is_ignored():
    before = event("a")
    after = event("a")
    after["modificationtime"] = "2026-06-06T00:00:00Z"
    assert revisions.diff_catalogues([before], [after]) == []


def test_disappeared_event_is_reported():
    rows = revisions.diff_catalogues([event("a"), event("b")], [event("a")])
    assert len(rows) == 1
    assert rows[0]["publicid"] == "b"
    assert rows[0]["change_kind"] == "withdrawn"


def test_write_daily_diff_round_trips(tmp_path):
    previous = tmp_path / "prev.parquet"
    current = tmp_path / "curr.parquet"
    out = tmp_path / "diff.parquet"
    storage.write_parquet_atomic([event("a", magnitude=3.4)], previous)
    storage.write_parquet_atomic([event("a", magnitude=3.9)], current)

    written = revisions.write_daily_diff(previous, current, out)

    assert written == 1
    rows = storage.read_parquet(out)
    assert rows[0]["field"] == "magnitude"
    assert rows[0]["new_value"] == "3.9"


def test_write_daily_diff_with_no_changes_writes_nothing(tmp_path):
    previous = tmp_path / "prev.parquet"
    current = tmp_path / "curr.parquet"
    out = tmp_path / "diff.parquet"
    storage.write_parquet_atomic([event("a")], previous)
    storage.write_parquet_atomic([event("a")], current)

    written = revisions.write_daily_diff(previous, current, out)

    assert written == 0
    assert not out.exists()
