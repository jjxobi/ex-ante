"""The two snapshot selectors, and the guard that stops them drifting together.

These are tested side by side on purpose. The whole risk is that someone later
notices the two functions look similar, reuses one for the other's job, and
produces a T+45 score computed against the wrong day's catalogue: a number that
is individually plausible and silently wrong, which is this project's recurring
failure shape.

The decisive test is that the by-date selector RAISES rather than substituting.
If it ever grows a fallback, that test fails.
"""

from __future__ import annotations

from datetime import date

import pytest

from eq import snapshots


def make(directory, *names):
    for name in names:
        (directory / name).write_bytes(b"")
    return directory


# --------------------------------------------------------------------------
# newest_snapshot: for ingest and the revision diff
# --------------------------------------------------------------------------

def test_newest_snapshot_picks_the_latest_date(tmp_path):
    make(
        tmp_path,
        "catalogue-2026-08-01.parquet",
        "catalogue-2026-08-03.parquet",
        "catalogue-2026-08-02.parquet",
    )
    assert snapshots.newest_snapshot(tmp_path).name == "catalogue-2026-08-03.parquet"


def test_newest_snapshot_ignores_a_non_dated_file(tmp_path):
    """The D4b trap, pinned.

    A lexical maximum over catalogue-*.parquet returns catalogue-ci.parquet,
    because "c" sorts after "2". That was verified against DuckDB and is why
    the pattern is date-shaped rather than a wildcard.
    """
    make(
        tmp_path,
        "catalogue-2026-08-03.parquet",
        "catalogue-ci.parquet",
        "catalogue-latest.parquet",
    )
    assert snapshots.newest_snapshot(tmp_path).name == "catalogue-2026-08-03.parquet"


def test_newest_snapshot_raises_when_there_are_none(tmp_path):
    with pytest.raises(snapshots.NoSnapshotsError):
        snapshots.newest_snapshot(tmp_path)


def test_newest_snapshot_sorts_by_parsed_date_not_by_filename(tmp_path):
    """Across a year boundary, filename order and date order agree, but the
    guarantee should come from parsing rather than coincidence.
    """
    make(tmp_path, "catalogue-2025-12-31.parquet", "catalogue-2026-01-01.parquet")
    assert snapshots.newest_snapshot(tmp_path).name == "catalogue-2026-01-01.parquet"


# --------------------------------------------------------------------------
# snapshot_for_date: for the T+45 evaluation freeze
# --------------------------------------------------------------------------

def test_snapshot_for_date_returns_the_exact_match(tmp_path):
    make(
        tmp_path,
        "catalogue-2026-08-01.parquet",
        "catalogue-2026-08-02.parquet",
        "catalogue-2026-08-03.parquet",
    )
    got = snapshots.snapshot_for_date(date(2026, 8, 2), tmp_path)
    assert got.name == "catalogue-2026-08-02.parquet"


def test_snapshot_for_date_raises_rather_than_substituting_a_neighbour(tmp_path):
    """THE test this module exists for.

    Snapshots exist on either side of the target and the target itself is
    missing. A fallback would silently score this window against a different
    day's catalogue, making "frozen at T+45" mean something different here than
    everywhere else. D7.2 requires a loud failure instead.
    """
    make(
        tmp_path,
        "catalogue-2026-08-01.parquet",
        "catalogue-2026-08-03.parquet",
    )
    with pytest.raises(snapshots.SnapshotNotFoundForDateError) as excinfo:
        snapshots.snapshot_for_date(date(2026, 8, 2), tmp_path)
    assert "2026-08-02" in str(excinfo.value)
    assert "substitute" in str(excinfo.value).lower()


def test_snapshot_for_date_does_not_fall_back_to_the_newest(tmp_path):
    """The specific wrong behaviour: reusing newest_snapshot for this job."""
    make(tmp_path, "catalogue-2026-08-05.parquet")
    with pytest.raises(snapshots.SnapshotNotFoundForDateError):
        snapshots.snapshot_for_date(date(2026, 8, 2), tmp_path)


def test_snapshot_for_date_ignores_a_non_dated_file(tmp_path):
    make(tmp_path, "catalogue-ci.parquet")
    with pytest.raises(snapshots.SnapshotNotFoundForDateError):
        snapshots.snapshot_for_date(date(2026, 8, 2), tmp_path)


# --------------------------------------------------------------------------
# The two must stay distinguishable
# --------------------------------------------------------------------------

def test_the_two_selectors_disagree_when_the_target_is_absent(tmp_path):
    """Same directory, same call site shape, deliberately different answers.

    newest_snapshot happily returns something. snapshot_for_date refuses. If a
    future refactor ever makes these agree, the freeze has silently acquired a
    fallback and this fails.
    """
    make(
        tmp_path,
        "catalogue-2026-08-01.parquet",
        "catalogue-2026-08-05.parquet",
    )
    assert snapshots.newest_snapshot(tmp_path).name == "catalogue-2026-08-05.parquet"
    with pytest.raises(snapshots.SnapshotNotFoundForDateError):
        snapshots.snapshot_for_date(date(2026, 8, 3), tmp_path)


def test_the_two_error_types_are_distinct(tmp_path):
    """An empty directory and a missing target date are different conditions.

    Only the second is a gap in an otherwise running record, so the evaluation
    freeze needs to tell them apart.
    """
    assert snapshots.SnapshotNotFoundForDateError is not snapshots.NoSnapshotsError
    with pytest.raises(snapshots.NoSnapshotsError):
        snapshots.newest_snapshot(tmp_path)
    with pytest.raises(snapshots.SnapshotNotFoundForDateError):
        snapshots.snapshot_for_date(date(2026, 8, 2), tmp_path)
