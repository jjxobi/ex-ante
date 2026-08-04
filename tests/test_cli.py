from datetime import date

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
    assert cli.main(["nonsense"]) != 0
