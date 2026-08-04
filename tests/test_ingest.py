from datetime import date

import pytest

from eq import geonet, ingest, storage


def test_ingest_range_writes_records(tmp_path, sample_csv):
    target = tmp_path / "out.parquet"
    written = ingest.ingest_range(
        date(2026, 1, 1), date(2026, 2, 1), 3.0, target, fetch=lambda url: sample_csv
    )
    assert written == 2
    assert len(storage.read_parquet(target)) == 2


def test_ingest_range_passes_correct_url(tmp_path, sample_csv):
    seen = {}

    def spy(url):
        seen["url"] = url
        return sample_csv

    ingest.ingest_range(
        date(2025, 1, 1), date(2026, 1, 1), 3.5, tmp_path / "o.parquet", fetch=spy
    )
    assert "minmag=3.5" in seen["url"]
    assert "startdate=2025-01-01T00:00:00" in seen["url"]
    assert geonet.BBOX in seen["url"]


def test_outage_raises_and_writes_nothing(tmp_path):
    target = tmp_path / "out.parquet"

    def failing(url):
        raise geonet.GeoNetError("simulated outage")

    with pytest.raises(geonet.GeoNetError):
        ingest.ingest_range(date(2026, 1, 1), date(2026, 2, 1), 3.0, target, fetch=failing)

    assert not target.exists()
    assert list(tmp_path.iterdir()) == []


def test_outage_leaves_previous_file_intact(tmp_path, sample_csv):
    target = tmp_path / "out.parquet"
    ingest.ingest_range(
        date(2026, 1, 1), date(2026, 2, 1), 3.0, target, fetch=lambda url: sample_csv
    )
    before = target.read_bytes()

    def failing(url):
        raise geonet.GeoNetError("simulated outage")

    with pytest.raises(geonet.GeoNetError):
        ingest.ingest_range(date(2026, 2, 1), date(2026, 3, 1), 3.0, target, fetch=failing)

    assert target.read_bytes() == before


def test_empty_response_raises_rather_than_writing_empty_file(tmp_path):
    target = tmp_path / "out.parquet"
    header_only = "publicid,origintime,modificationtime,longitude,latitude,magnitude,depth\n"
    with pytest.raises(ingest.EmptyCatalogueError):
        ingest.ingest_range(
            date(2026, 1, 1), date(2026, 2, 1), 3.0, target, fetch=lambda url: header_only
        )
    assert not target.exists()


def test_snapshot_writes_dated_filename(tmp_path, monkeypatch, sample_csv):
    monkeypatch.setattr(ingest.paths, "SNAPSHOT_DIR", tmp_path)
    path = ingest.snapshot_full_catalogue(date(2026, 8, 4), fetch=lambda url: sample_csv)
    assert path.name == "catalogue-2026-08-04.parquet"
    assert path.is_file()
