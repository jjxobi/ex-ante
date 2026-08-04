from datetime import datetime, timezone

import pytest

from eq import storage


def make_records(n: int = 2) -> list[dict]:
    return [
        {
            "publicid": f"id{i}",
            "origintime": datetime(2026, 1, 1, tzinfo=timezone.utc),
            "modificationtime": datetime(2026, 2, 1, tzinfo=timezone.utc),
            "longitude": 175.0 + i,
            "latitude": -41.0,
            "magnitude": 3.5,
            "depth": 12.0,
            "magnitudetype": "MLv",
            "depthtype": "operator assigned",
            "evaluationstatus": "confirmed",
            "evaluationmode": "manual",
        }
        for i in range(n)
    ]


def test_write_then_read_round_trips(tmp_path):
    target = tmp_path / "events.parquet"
    storage.write_parquet_atomic(make_records(3), target)
    loaded = storage.read_parquet(target)
    assert len(loaded) == 3
    assert loaded[0]["publicid"] == "id0"
    assert loaded[0]["depthtype"] == "operator assigned"


def test_write_leaves_no_temp_files(tmp_path):
    target = tmp_path / "events.parquet"
    storage.write_parquet_atomic(make_records(), target)
    assert [p.name for p in tmp_path.iterdir()] == ["events.parquet"]


def test_failed_write_does_not_replace_existing_file(tmp_path, monkeypatch):
    target = tmp_path / "events.parquet"
    storage.write_parquet_atomic(make_records(1), target)
    original = target.read_bytes()

    def boom(*args, **kwargs):
        raise OSError("disk full")

    monkeypatch.setattr(storage.pq, "write_table", boom)
    with pytest.raises(OSError):
        storage.write_parquet_atomic(make_records(5), target)

    assert target.read_bytes() == original
    assert [p.name for p in tmp_path.iterdir()] == ["events.parquet"]


def test_write_rejects_empty_records(tmp_path):
    with pytest.raises(ValueError):
        storage.write_parquet_atomic([], tmp_path / "events.parquet")
