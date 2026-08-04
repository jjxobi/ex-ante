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


def test_chunk_ranges_covers_whole_span_without_gaps():
    chunks = ingest._chunk_ranges(date(2005, 1, 1), date(2026, 8, 5), 5)

    # First chunk should start at start
    assert chunks[0][0] == date(2005, 1, 1)

    # Last chunk should end at end
    assert chunks[-1][1] == date(2026, 8, 5)

    # Consecutive ranges should share boundaries
    for i in range(len(chunks) - 1):
        assert chunks[i][1] == chunks[i + 1][0]

    # No range should exceed the year span
    for chunk_start, chunk_end in chunks:
        year_diff = chunk_end.year - chunk_start.year
        assert year_diff <= 5


def test_snapshot_issues_multiple_fetches(tmp_path, monkeypatch):
    monkeypatch.setattr(ingest.paths, "SNAPSHOT_DIR", tmp_path)
    seen_urls = []

    def spy_fetch(url):
        seen_urls.append(url)
        return "publicid,origintime,modificationtime,longitude,latitude,magnitude,depth\n"

    # For a 2005-2026 span with 5-year chunks, should see multiple fetches
    try:
        ingest.snapshot_full_catalogue(date(2026, 8, 5), fetch=spy_fetch)
    except ingest.EmptyCatalogueError:
        # Expected, since all our fake responses are empty
        pass

    assert len(seen_urls) > 1


def test_snapshot_deduplicates_across_chunk_boundaries(tmp_path, monkeypatch):
    monkeypatch.setattr(ingest.paths, "SNAPSHOT_DIR", tmp_path)

    # Create two CSV responses with the same publicid but different modificationtime
    earlier_csv = "publicid,origintime,modificationtime,longitude,latitude,magnitude,depth\nid1,2020-01-01T00:00:00Z,2020-01-02T00:00:00Z,175.0,-41.0,5.0,10.0\n"
    later_csv = "publicid,origintime,modificationtime,longitude,latitude,magnitude,depth\nid1,2020-01-01T00:00:00Z,2020-01-03T00:00:00Z,175.0,-41.0,5.0,10.0\n"

    call_count = [0]

    def chunked_fetch(url):
        call_count[0] += 1
        if call_count[0] == 1:
            return earlier_csv
        else:
            return later_csv

    # Patch _chunk_ranges to return exactly 2 chunks so we can control the fetch responses
    original_chunk_ranges = ingest._chunk_ranges
    def two_chunks(start, end, years):
        return [(date(2005, 1, 1), date(2015, 1, 1)), (date(2015, 1, 1), date(2026, 8, 5))]

    monkeypatch.setattr(ingest, "_chunk_ranges", two_chunks)

    path = ingest.snapshot_full_catalogue(date(2026, 8, 5), fetch=chunked_fetch)

    # Read the written file and verify only one record with the later modificationtime
    records = storage.read_parquet(path)
    assert len(records) == 1
    assert records[0]["publicid"] == "id1"
    # The modificationtime should be the later one
    assert records[0]["modificationtime"].isoformat() == "2020-01-03T00:00:00+00:00"


def test_snapshot_failure_midway_writes_nothing(tmp_path, monkeypatch):
    monkeypatch.setattr(ingest.paths, "SNAPSHOT_DIR", tmp_path)

    success_csv = "publicid,origintime,modificationtime,longitude,latitude,magnitude,depth\nid1,2020-01-01T00:00:00Z,2020-01-02T00:00:00Z,175.0,-41.0,5.0,10.0\n"

    call_count = [0]

    def failing_fetch(url):
        call_count[0] += 1
        if call_count[0] == 1:
            return success_csv
        else:
            raise geonet.GeoNetError("simulated outage")

    # Patch _chunk_ranges to return exactly 2 chunks
    def two_chunks(start, end, years):
        return [(date(2005, 1, 1), date(2015, 1, 1)), (date(2015, 1, 1), date(2026, 8, 5))]

    monkeypatch.setattr(ingest, "_chunk_ranges", two_chunks)

    with pytest.raises(geonet.GeoNetError):
        ingest.snapshot_full_catalogue(date(2026, 8, 5), fetch=failing_fetch)

    # Verify no snapshot file was created
    snapshots = list(ingest.paths.SNAPSHOT_DIR.glob("*.parquet"))
    assert len(snapshots) == 0
