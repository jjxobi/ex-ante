from datetime import date

import pytest

from eq import cli


def test_snapshot_command_invokes_ingest(monkeypatch, tmp_path):
    called = {}

    def fake_snapshot(today, **kwargs):
        called["today"] = today
        return tmp_path / "catalogue.parquet"

    monkeypatch.setattr(cli.ingest, "snapshot_full_catalogue", fake_snapshot)
    exit_code = cli.main(["snapshot", "--date", "2026-08-04"])
    assert exit_code == 0
    assert called["today"] == date(2026, 8, 4)


def test_range_command_invokes_ingest(monkeypatch, tmp_path):
    called = {}

    def fake_range(start, end, min_magnitude, destination, **kwargs):
        called.update(start=start, end=end, mag=min_magnitude, dest=destination)
        return 7

    monkeypatch.setattr(cli.ingest, "ingest_range", fake_range)
    exit_code = cli.main(
        [
            "range",
            "--start", "2025-01-01",
            "--end", "2026-01-01",
            "--min-magnitude", "3.0",
            "--out", str(tmp_path / "x.parquet"),
        ]
    )
    assert exit_code == 0
    assert called["start"] == date(2025, 1, 1)
    assert called["mag"] == 3.0


def test_unknown_command_returns_error():
    with pytest.raises(SystemExit) as excinfo:
        cli.main(["nonsense"])
    assert excinfo.value.code != 0


def test_diff_command_invokes_write_daily_diff_with_two_newest_files(monkeypatch, tmp_path):
    monkeypatch.setattr(cli.paths, "SNAPSHOT_DIR", tmp_path)
    monkeypatch.setattr(cli.paths, "DATA_DIR", tmp_path)
    for name in (
        "catalogue-2026-08-01.parquet",
        "catalogue-2026-08-02.parquet",
        "catalogue-2026-08-03.parquet",
    ):
        (tmp_path / name).write_bytes(b"")

    called = {}

    def fake_write_daily_diff(previous, current, destination, observed_at):
        called.update(previous=previous, current=current, destination=destination, observed_at=observed_at)
        return 5

    monkeypatch.setattr(cli.revisions, "write_daily_diff", fake_write_daily_diff)
    exit_code = cli.main(["diff", "--date", "2026-08-03"])

    assert exit_code == 0
    assert called["previous"].name == "catalogue-2026-08-02.parquet"
    assert called["current"].name == "catalogue-2026-08-03.parquet"
    assert called["observed_at"] == date(2026, 8, 3)


def test_diff_command_with_fewer_than_two_snapshots_returns_zero(monkeypatch, tmp_path):
    monkeypatch.setattr(cli.paths, "SNAPSHOT_DIR", tmp_path)
    (tmp_path / "catalogue-2026-08-03.parquet").write_bytes(b"")

    called = {}

    def fake_write_daily_diff(*args, **kwargs):
        called["invoked"] = True
        return 0

    monkeypatch.setattr(cli.revisions, "write_daily_diff", fake_write_daily_diff)
    exit_code = cli.main(["diff", "--date", "2026-08-03"])

    assert exit_code == 0
    assert "invoked" not in called
