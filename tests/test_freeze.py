"""Tests for the T+45 evaluation freeze (eq.freeze).

Hermetic by construction: every snapshot directory a test needs is built in
tmp_path, never read from data/snapshots. data/ is gitignored and absent on a
fresh clone, and this project has already shipped a CI break from a test that
quietly depended on it (see D4b and test_score.py's own note on the same
trap). Real catalogue content still appears here: the fixture
tests/fixtures/catalogue-fit-window.parquet is committed and used, in the
same style test_score.py and test_baseline.py already establish, as the
source of "real" events written into synthetic, tmp_path snapshot files.

Fitting a baseline is expensive, so the fitted forecast used throughout is a
module scoped fixture, fit once.
"""

from __future__ import annotations

import inspect
import pathlib
from datetime import date, datetime, timedelta, timezone

import pytest

from eq import baseline, expander, freeze, score, snapshots, storage

FIXTURE = pathlib.Path(__file__).parent / "fixtures" / "catalogue-fit-window.parquet"

# The same real week test_score.py's end-to-end test already exercises: 47
# events, both in and out of the collection region, both strata. Chosen there
# because it is far enough before the fixture's own coverage ends that
# GeoNet's review queue had time to settle most of it; reused here for the
# same reason, and because its target dates are already known and checked
# below.
WINDOW_START = datetime(2026, 7, 20, tzinfo=timezone.utc)
WINDOW_END = datetime(2026, 7, 27, tzinfo=timezone.utc)

# window_end + 45 days. Deliberately NOT window_start + 45 days
# (2026-09-03): see test_target_snapshot_date_would_be_wrong_from_window_open.
TARGET_DATE = date(2026, 9, 10)

# window_end + 7 days, for the provisional-score tests.
PROVISIONAL_DATE = date(2026, 8, 3)

# Comfortably past every target date used in this file, so "the window is
# old enough to score" never has to be recomputed by hand in each test.
FAR_FUTURE = date(2026, 12, 1)

# Comfortably before every target date used in this file.
FAR_PAST = date(2026, 8, 1)


def make_dated_snapshots(directory: pathlib.Path, *dates_and_events) -> None:
    """Write synthetic, hermetic snapshot files, one per (date, events) pair.

    Mirrors test_snapshots.py's own `make` helper in spirit: build exactly
    the directory state a test needs, nothing borrowed from data/.
    """
    for snapshot_date, events in dates_and_events:
        name = f"catalogue-{snapshot_date.isoformat()}.parquet"
        storage.write_parquet_atomic(events, directory / name)


@pytest.fixture(scope="module")
def fit_events() -> list[dict]:
    return storage.read_parquet(FIXTURE)


@pytest.fixture(scope="module")
def fitted_shallow(fit_events) -> baseline.FittedBaseline:
    return baseline.fit(fit_events, "shallow")


@pytest.fixture(scope="module")
def dense_shallow_week(fitted_shallow) -> expander.DenseForecast:
    separable = baseline.forecast(fitted_shallow, WINDOW_START, WINDOW_END)
    return expander.expand(separable, expected_grid_hash=baseline.FROZEN_GRID_HASH)


# ==========================================================================
# Criterion 1: T+45 runs from window close, not window open
# ==========================================================================

def test_target_snapshot_date_is_window_close_plus_45_days():
    assert freeze.target_snapshot_date(WINDOW_END) == TARGET_DATE


def test_target_snapshot_date_would_be_wrong_from_window_open():
    """THE test named in the brief. For this weekly window, close and open
    are 7 days apart, so computing T+45 from window_start gives a different,
    wrong date. Pinning the correct value here means this test starts
    failing the moment target_snapshot_date is switched to measure from
    window_start instead of window_end.
    """
    from_close = freeze.target_snapshot_date(WINDOW_END)
    from_open = freeze.target_snapshot_date(WINDOW_START)
    assert from_close == TARGET_DATE
    assert from_open != from_close
    assert from_close - from_open == timedelta(days=7)


# ==========================================================================
# Criterion 2: snapshot_for_date is used, newest_snapshot never is
# ==========================================================================

def test_module_source_never_mentions_newest_snapshot():
    """Grepping the module as a real, self-checking assertion rather than a
    one-off manual check: this fails the moment anyone adds a call to
    snapshots.newest_snapshot anywhere in eq.freeze.
    """
    source = inspect.getsource(freeze)
    assert "newest_snapshot" not in source


def test_freeze_window_locates_the_snapshot_by_exact_date(tmp_path, dense_shallow_week, fit_events):
    make_dated_snapshots(tmp_path, (TARGET_DATE, fit_events))
    evaluation = freeze.freeze_window(
        WINDOW_END,
        window_start=WINDOW_START,
        forecast=dense_shallow_week,
        stratum="shallow",
        snapshot_dir=tmp_path,
        output_dir=tmp_path / "eval",
        now=FAR_FUTURE,
    )
    assert evaluation.snapshot_path == tmp_path / f"catalogue-{TARGET_DATE.isoformat()}.parquet"


def test_freeze_window_fails_when_exact_date_missing_even_with_a_neighbour_present(
    tmp_path, dense_shallow_week, fit_events
):
    """A neighbouring date exists on either side of the target; the target
    itself does not. A fallback to the nearest date would silently score
    this window against the wrong day; D7.1 requires a loud failure instead.
    """
    make_dated_snapshots(
        tmp_path,
        (TARGET_DATE - timedelta(days=1), fit_events),
        (TARGET_DATE + timedelta(days=1), fit_events),
    )
    evaluation = freeze.freeze_window(
        WINDOW_END,
        window_start=WINDOW_START,
        forecast=dense_shallow_week,
        stratum="shallow",
        snapshot_dir=tmp_path,
        now=FAR_FUTURE,
    )
    assert evaluation.state is freeze.WindowState.SCORING_FAILED
    assert str(TARGET_DATE) in evaluation.reason


# ==========================================================================
# Criterion 3: one missing target date fails only that window
# ==========================================================================

def test_one_missing_target_date_fails_only_that_window(tmp_path, dense_shallow_week, fit_events):
    window_a_start, window_a_end = WINDOW_START, WINDOW_END  # target 2026-09-10
    window_b_start = WINDOW_START + timedelta(days=7)
    window_b_end = WINDOW_END + timedelta(days=7)  # target 2026-09-17, deliberately absent
    window_c_start = WINDOW_START + timedelta(days=14)
    window_c_end = WINDOW_END + timedelta(days=14)  # target 2026-09-24

    make_dated_snapshots(
        tmp_path,
        (freeze.target_snapshot_date(window_a_end), fit_events),
        (freeze.target_snapshot_date(window_c_end), fit_events),
    )

    windows = [
        freeze.WindowSpec(window_a_start, window_a_end, dense_shallow_week, "shallow", "a"),
        freeze.WindowSpec(window_b_start, window_b_end, dense_shallow_week, "shallow", "b"),
        freeze.WindowSpec(window_c_start, window_c_end, dense_shallow_week, "shallow", "c"),
    ]
    result = freeze.freeze_all(
        windows, snapshot_dir=tmp_path, output_dir=tmp_path / "eval", now=FAR_FUTURE
    )

    states = {w.label: e.state for w, e in zip(windows, result.evaluations)}
    print(f"\nper-window states: {states}")
    assert states == {
        "a": freeze.WindowState.SCORED,
        "b": freeze.WindowState.SCORING_FAILED,
        "c": freeze.WindowState.SCORED,
    }
    # The two scored windows actually carry a score of record.
    assert result.evaluations[0].score is not None
    assert result.evaluations[2].score is not None
    # The failed one carries a reason and no score.
    assert result.evaluations[1].score is None
    assert result.evaluations[1].reason is not None


# ==========================================================================
# Criterion 4 and the BRANCH criterion: NoSnapshotsError halts the whole run;
# SnapshotNotFoundForDateError only ever fails its own window.
# ==========================================================================

def test_empty_snapshot_directory_halts_the_whole_run_and_raises(tmp_path, dense_shallow_week):
    empty_dir = tmp_path / "empty_snapshots"
    empty_dir.mkdir()
    windows = [
        freeze.WindowSpec(WINDOW_START, WINDOW_END, dense_shallow_week, "shallow"),
        freeze.WindowSpec(
            WINDOW_START + timedelta(days=7), WINDOW_END + timedelta(days=7),
            dense_shallow_week, "shallow",
        ),
    ]
    with pytest.raises(snapshots.NoSnapshotsError):
        freeze.freeze_all(
            windows, snapshot_dir=empty_dir, output_dir=tmp_path / "eval", now=FAR_FUTURE
        )


def test_empty_snapshot_directory_never_produces_a_scoring_failed_result(tmp_path, dense_shallow_week):
    """The exact failure this branch exists to prevent: an empty directory
    quietly turning into fifty SCORING FAILED rows instead of one loud halt.
    freeze_all must not catch NoSnapshotsError and repackage it as a state.
    """
    empty_dir = tmp_path / "empty_snapshots"
    empty_dir.mkdir()
    windows = [freeze.WindowSpec(WINDOW_START, WINDOW_END, dense_shallow_week, "shallow")]
    try:
        freeze.freeze_all(windows, snapshot_dir=empty_dir, now=FAR_FUTURE)
        raised = False
    except snapshots.NoSnapshotsError:
        raised = True
    assert raised, "an empty directory must halt the run by raising, not by returning a result"


def test_missing_single_date_vs_empty_directory_are_different_branches(
    tmp_path, dense_shallow_week, fit_events
):
    """THE branch test. Same call shape (freeze_window on the same window),
    two different snapshot directory states, and D7.2 requires two different
    outcomes: a directory that has other snapshots but not this window's
    exact date must return SCORING_FAILED and must NOT raise; a directory
    with nothing dated at all must raise NoSnapshotsError and must NOT return
    a SCORING_FAILED result. Collapsing these into one behaviour (catching
    both, or raising both, or returning SCORING_FAILED for both) is exactly
    the defect D7.2's branch table exists to prevent.
    """
    # Branch 1: SnapshotNotFoundForDateError, local, one bad day.
    non_empty = tmp_path / "non_empty"
    non_empty.mkdir()
    make_dated_snapshots(non_empty, (date(2020, 1, 1), fit_events))
    evaluation = freeze.freeze_window(
        WINDOW_END,
        window_start=WINDOW_START,
        forecast=dense_shallow_week,
        stratum="shallow",
        snapshot_dir=non_empty,
        now=FAR_FUTURE,
    )
    assert evaluation.state is freeze.WindowState.SCORING_FAILED

    # Branch 2: NoSnapshotsError, systemic, nothing dated anywhere.
    empty = tmp_path / "truly_empty"
    empty.mkdir()
    with pytest.raises(snapshots.NoSnapshotsError):
        freeze.freeze_window(
            WINDOW_END,
            window_start=WINDOW_START,
            forecast=dense_shallow_week,
            stratum="shallow",
            snapshot_dir=empty,
            now=FAR_FUTURE,
        )


# ==========================================================================
# Criterion 5: all four states are reachable and distinguishable
# ==========================================================================

def test_never_published_state_via_window_state():
    assert (
        freeze.window_state(WINDOW_END, forecast_published=False)
        is freeze.WindowState.NEVER_PUBLISHED
    )


def test_published_not_yet_scoreable_state_via_window_state():
    assert (
        freeze.window_state(WINDOW_END, forecast_published=True, now=FAR_PAST)
        is freeze.WindowState.PUBLISHED_NOT_YET_SCOREABLE
    )


def test_scored_state_via_window_state():
    assert (
        freeze.window_state(
            WINDOW_END, forecast_published=True, now=FAR_FUTURE, scoring_succeeded=True
        )
        is freeze.WindowState.SCORED
    )


def test_scoring_failed_state_via_window_state():
    assert (
        freeze.window_state(
            WINDOW_END, forecast_published=True, now=FAR_FUTURE, scoring_succeeded=False
        )
        is freeze.WindowState.SCORING_FAILED
    )


def test_window_state_refuses_to_guess_once_t45_has_passed():
    with pytest.raises(ValueError):
        freeze.window_state(WINDOW_END, forecast_published=True, now=FAR_FUTURE)


def test_never_published_state_via_freeze_window(tmp_path):
    evaluation = freeze.freeze_window(
        WINDOW_END, window_start=WINDOW_START, forecast=None, snapshot_dir=tmp_path
    )
    assert evaluation.state is freeze.WindowState.NEVER_PUBLISHED


def test_published_not_yet_scoreable_state_via_freeze_window(tmp_path, dense_shallow_week):
    evaluation = freeze.freeze_window(
        WINDOW_END,
        window_start=WINDOW_START,
        forecast=dense_shallow_week,
        stratum="shallow",
        snapshot_dir=tmp_path,
        now=FAR_PAST,
    )
    assert evaluation.state is freeze.WindowState.PUBLISHED_NOT_YET_SCOREABLE


def test_scored_state_via_freeze_window(tmp_path, dense_shallow_week, fit_events):
    make_dated_snapshots(tmp_path, (TARGET_DATE, fit_events))
    evaluation = freeze.freeze_window(
        WINDOW_END,
        window_start=WINDOW_START,
        forecast=dense_shallow_week,
        stratum="shallow",
        snapshot_dir=tmp_path,
        output_dir=tmp_path / "eval",
        now=FAR_FUTURE,
    )
    assert evaluation.state is freeze.WindowState.SCORED
    assert evaluation.score is not None


def test_scoring_failed_state_via_freeze_window(tmp_path, dense_shallow_week, fit_events):
    make_dated_snapshots(tmp_path, (date(2020, 1, 1), fit_events))
    evaluation = freeze.freeze_window(
        WINDOW_END,
        window_start=WINDOW_START,
        forecast=dense_shallow_week,
        stratum="shallow",
        snapshot_dir=tmp_path,
        now=FAR_FUTURE,
    )
    assert evaluation.state is freeze.WindowState.SCORING_FAILED


def test_all_four_states_are_distinct_values():
    states = {
        freeze.WindowState.PUBLISHED_NOT_YET_SCOREABLE,
        freeze.WindowState.SCORED,
        freeze.WindowState.SCORING_FAILED,
        freeze.WindowState.NEVER_PUBLISHED,
    }
    assert len(states) == 4


# ==========================================================================
# Criterion 6: the frozen catalogue is written, window-only, and small
# ==========================================================================

def test_frozen_catalogue_holds_only_the_window_events_and_is_small(
    tmp_path, dense_shallow_week, fit_events
):
    make_dated_snapshots(tmp_path, (TARGET_DATE, fit_events))
    out_dir = tmp_path / "eval"
    evaluation = freeze.freeze_window(
        WINDOW_END,
        window_start=WINDOW_START,
        forecast=dense_shallow_week,
        stratum="shallow",
        snapshot_dir=tmp_path,
        output_dir=out_dir,
        now=FAR_FUTURE,
    )
    assert evaluation.catalogue_path is not None
    assert evaluation.catalogue_path.exists()

    written = storage.read_parquet(evaluation.catalogue_path)
    size_bytes = evaluation.catalogue_path.stat().st_size
    source_size_bytes = FIXTURE.stat().st_size

    print(
        f"\nfrozen catalogue: {len(written)} rows, {size_bytes} bytes "
        f"(source snapshot: {len(fit_events)} rows, {source_size_bytes} bytes)"
    )

    assert len(written) == evaluation.n_events
    assert len(written) < len(fit_events)
    assert all(WINDOW_START <= e["origintime"] < WINDOW_END for e in written)
    # An order of magnitude smaller than the snapshot it was cut from, not
    # merely "smaller by one row": this is a week's worth of events out of
    # several years'.
    assert size_bytes * 10 < source_size_bytes


# ==========================================================================
# Criterion 7: scoring the committed bytes twice is identical
# ==========================================================================

def test_scoring_the_committed_catalogue_twice_gives_identical_results(
    tmp_path, dense_shallow_week, fit_events
):
    make_dated_snapshots(tmp_path, (TARGET_DATE, fit_events))
    evaluation = freeze.freeze_window(
        WINDOW_END,
        window_start=WINDOW_START,
        forecast=dense_shallow_week,
        stratum="shallow",
        snapshot_dir=tmp_path,
        output_dir=tmp_path / "eval",
        now=FAR_FUTURE,
    )
    committed_events = storage.read_parquet(evaluation.catalogue_path)

    result_a = score.score(
        dense_shallow_week, committed_events, WINDOW_START, WINDOW_END, stratum="shallow"
    )
    result_b = score.score(
        dense_shallow_week, committed_events, WINDOW_START, WINDOW_END, stratum="shallow"
    )
    assert result_a == result_b
    # And identical to the score freeze_window itself already produced from
    # those same bytes.
    assert result_a == evaluation.score


# ==========================================================================
# Criterion 8: provisional T+7 score, labelled unstable, drift reportable
# ==========================================================================

def test_provisional_score_is_labelled_unstable(tmp_path, dense_shallow_week, fit_events):
    make_dated_snapshots(tmp_path, (PROVISIONAL_DATE, fit_events))
    provisional = freeze.provisional_score(
        WINDOW_END,
        window_start=WINDOW_START,
        forecast=dense_shallow_week,
        stratum="shallow",
        snapshot_dir=tmp_path,
    )
    assert provisional is not None
    assert provisional.snapshot_date == PROVISIONAL_DATE
    assert provisional.label == freeze.PROVISIONAL_LABEL
    assert "UNSTABLE" in provisional.label
    print(f"\nprovisional score label: {provisional.label}")


def test_provisional_score_is_none_when_t7_snapshot_has_not_landed(tmp_path, dense_shallow_week):
    provisional = freeze.provisional_score(
        WINDOW_END,
        window_start=WINDOW_START,
        forecast=dense_shallow_week,
        stratum="shallow",
        snapshot_dir=tmp_path,  # empty: T+7 has not landed
    )
    assert provisional is None


def test_drift_between_provisional_and_final_is_reportable(tmp_path, dense_shallow_week, fit_events):
    make_dated_snapshots(
        tmp_path,
        (PROVISIONAL_DATE, fit_events),
        (TARGET_DATE, fit_events),
    )
    provisional = freeze.provisional_score(
        WINDOW_END,
        window_start=WINDOW_START,
        forecast=dense_shallow_week,
        stratum="shallow",
        snapshot_dir=tmp_path,
    )
    final = freeze.freeze_window(
        WINDOW_END,
        window_start=WINDOW_START,
        forecast=dense_shallow_week,
        stratum="shallow",
        snapshot_dir=tmp_path,
        output_dir=tmp_path / "eval",
        now=FAR_FUTURE,
    )
    assert final.state is freeze.WindowState.SCORED

    observed_drift = freeze.drift(provisional, final)
    print(
        f"\ndrift: provisional {observed_drift.provisional_n_events} events, "
        f"final {observed_drift.final_n_events} events, "
        f"difference {observed_drift.n_events_difference}"
    )
    assert isinstance(observed_drift.n_events_difference, int)
    assert observed_drift.provisional_n_events == provisional.score.n_events_used
    assert observed_drift.final_n_events == final.score.n_events_used


def test_drift_refuses_to_compare_against_an_unscored_window(tmp_path, dense_shallow_week, fit_events):
    make_dated_snapshots(tmp_path, (PROVISIONAL_DATE, fit_events))
    provisional = freeze.provisional_score(
        WINDOW_END,
        window_start=WINDOW_START,
        forecast=dense_shallow_week,
        stratum="shallow",
        snapshot_dir=tmp_path,
    )
    not_yet_scoreable = freeze.freeze_window(
        WINDOW_END,
        window_start=WINDOW_START,
        forecast=dense_shallow_week,
        stratum="shallow",
        snapshot_dir=tmp_path,
        now=FAR_PAST,
    )
    assert not_yet_scoreable.state is freeze.WindowState.PUBLISHED_NOT_YET_SCOREABLE
    with pytest.raises(ValueError):
        freeze.drift(provisional, not_yet_scoreable)
