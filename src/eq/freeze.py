"""The T+45 evaluation freeze: decides what gets scored, and against what.

This is the component DECISIONS.md D7, D7.1 and D7.2 describe. It answers
three questions for every published forecast window:

    Is this window old enough to score yet?
    If so, what exact catalogue snapshot is its score computed against?
    If it cannot be scored, why not, and does that stop just this window or
    the whole run?

WHY T+45 IS MEASURED FROM WINDOW CLOSE, NOT WINDOW OPEN (D7.1). Revision lag
is measured from an event's own origin time, and an event can occur at any
point in a window, including its final second. Timing the freeze from window
open would give the last event of a weekly window only 38 days to settle,
which is inside the region where most records are still moving. Timing from
close guarantees every event in the window gets the full 45 days. For a
weekly window this is a 7 day difference, so it has to be computed
explicitly rather than assumed to not matter.

WHY THE SNAPSHOT IS SELECTED BY EXACT DATE, NEVER SUBSTITUTED (D7.1). This
module calls `eq.snapshots.snapshot_for_date`, never the sibling selector
`eq.snapshots` offers for picking whatever snapshot is most recent. The
whole point of freezing is that "scored at T+45" means the same thing for
every window; silently scoring one window against the nearest available day
instead of its own exact target date would make that claim false for that
one window without saying so. If the exact date is missing, that is reported,
not patched over.

WHY THE TWO SNAPSHOT ERRORS PRODUCE DIFFERENT OUTCOMES (D7.2). `eq.snapshots`
raises two distinct exception types for a reason, and this is the one place
that reason has to be honoured:

  * `snapshots.NoSnapshotsError` means the snapshot directory holds nothing
    dated at all: ingest never ran, or the directory or volume is wrong.
    That is a systemic failure with nothing to do with any one window, so it
    is left to propagate straight out of `freeze_window` and `freeze_all`,
    halting the whole run. Nothing gets marked SCORING FAILED, because
    marking fifty windows failed would bury the one real cause.
  * `snapshots.SnapshotNotFoundForDateError` means the record is otherwise
    running and one specific day did not land. That is local to the one
    window whose target date it is, so it is caught here and turned into a
    SCORING_FAILED result for that window only; every other window is
    unaffected.

  A subtlety worth stating plainly: `snapshot_for_date` itself only ever
  raises `SnapshotNotFoundForDateError`, whether the directory is merely
  missing one date or holds nothing at all (see its own docstring and
  `tests/test_snapshots.py`). Telling the two situations apart is therefore
  this module's job, not `eq.snapshots`'s. It is done with `_require_any_dated_snapshot`
  below, which calls `eq.snapshots.has_any_dated_snapshot` rather than
  duplicating its glob or its date-shaped filename pattern, and which never
  reaches for the recency selector: that function's job is picking a
  snapshot for use, a different and forbidden use here, not checking
  whether one exists.

THE FOUR WINDOW STATES (D7.2). PUBLISHED_NOT_YET_SCOREABLE, SCORED,
SCORING_FAILED, NEVER_PUBLISHED. Every window this module classifies gets
exactly one of these four, returned as an explicit value on
`FrozenEvaluation.state`. None of them is spelled as `None` or inferred from
a missing field: a scoreboard reading this output must never have to guess
which of "too young to score", "we tried and could not" and "never
forecast at all" a blank cell means, because that guess is exactly what a
run of scheduler failures could hide behind.

THE FROZEN CATALOGUE ITSELF. `freeze_window` writes the events that fell
inside the window (filtered by time only, nothing else) to a committed
parquet file under `paths.EVALUATION_CATALOGUE_DIR`, one file per window.
D7's reproducibility claim rests on scoring always reading that committed
file, never a live API: `score.score` is then called against exactly those
committed bytes, so re-running the freeze later, or reading the committed
file directly, reproduces the same score of record. D4b records that a
withdrawn event does not get retroactively removed from an already-frozen
catalogue: this module does not special case that, by design.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from enum import Enum
from pathlib import Path

from eq import expander, paths, score, snapshots, storage

# Frozen by D7: the score of record is computed 45 days after window close.
T_PLUS_DAYS = 45

# Frozen by D7: a provisional score, explicitly labelled unstable, is
# published at 7 days after window close.
PROVISIONAL_T_PLUS_DAYS = 7

PROVISIONAL_LABEL = "PROVISIONAL_T7_UNSTABLE"


class WindowState(str, Enum):
    """The four states a window can be in, per D7.2. See the module
    docstring: every window gets exactly one of these, never inferred from a
    blank field.
    """

    PUBLISHED_NOT_YET_SCOREABLE = "PUBLISHED_NOT_YET_SCOREABLE"
    SCORED = "SCORED"
    SCORING_FAILED = "SCORING_FAILED"
    NEVER_PUBLISHED = "NEVER_PUBLISHED"


@dataclass(frozen=True)
class FrozenEvaluation:
    """The outcome of attempting to freeze and score one window.

    `score` is populated only in state SCORED, and is the score of record:
    D7 says that score is not recomputed once produced. `reason` explains a
    SCORING_FAILED or NEVER_PUBLISHED state in words, so a reader never has
    to reverse engineer why from a missing field. `catalogue_path` is the
    committed, window-filtered parquet file this evaluation was scored
    against; it is None whenever no score was produced.
    """

    window_start: datetime | date
    window_end: datetime | date
    state: WindowState
    target_snapshot_date: date
    snapshot_path: Path | None = None
    catalogue_path: Path | None = None
    n_events: int | None = None
    score: "score.ScoreResult | None" = None
    reason: str | None = None


@dataclass(frozen=True)
class WindowSpec:
    """One window to attempt to freeze, as input to `freeze_all`.

    `forecast` is None exactly when no forecast was ever published for this
    window: that is Rule 1's NEVER_PUBLISHED case, not an error to raise.
    """

    window_start: datetime | date
    window_end: datetime | date
    forecast: "expander.DenseForecast | None" = None
    stratum: str | None = None
    label: str | None = None


@dataclass(frozen=True)
class FreezeRunResult:
    """The outcome of `freeze_all`: one `FrozenEvaluation` per window given.

    Only ever returned in full. If `snapshots.NoSnapshotsError` is raised
    partway through a run, `freeze_all` does not catch it and this type is
    never constructed for that run at all: see the module docstring's
    branch table.
    """

    evaluations: list[FrozenEvaluation]

    def by_state(self, state: WindowState) -> list[FrozenEvaluation]:
        return [e for e in self.evaluations if e.state is state]

    def state_counts(self) -> dict[WindowState, int]:
        counts = {state: 0 for state in WindowState}
        for evaluation in self.evaluations:
            counts[evaluation.state] += 1
        return counts


@dataclass(frozen=True)
class ProvisionalScore:
    """A T+7 score, per D7. Not the score of record.

    `label` is always `PROVISIONAL_T7_UNSTABLE`: nothing downstream can carry
    this figure without also carrying the word that says it is expected to
    move. T+7 sits well inside the region where D7's measured revision-lag
    histogram still has mass; the wall it eventually hits does not arrive
    until 32 to 45 days.
    """

    window_start: datetime | date
    window_end: datetime | date
    label: str
    snapshot_date: date
    score: "score.ScoreResult"


@dataclass(frozen=True)
class Drift:
    """How far a provisional T+7 read moved by the time the T+45 score of
    record landed, per D7's "along with the drift between the two". Reported
    explicitly rather than left for a caller to diff the two ScoreResults by
    hand.
    """

    provisional_n_events: int
    final_n_events: int
    n_events_difference: int
    provisional_n_statistic: float
    final_n_statistic: float


# --------------------------------------------------------------------------
# Small helpers, duplicated in miniature from eq.score / eq.anchor rather
# than imported, matching this codebase's own convention (see eq.score's
# _as_utc_datetime docstring): this module has no other reason to depend on
# those modules' internals.
# --------------------------------------------------------------------------

def _as_date(value: date | datetime) -> date:
    if isinstance(value, datetime):
        if value.tzinfo is None:
            raise ValueError(
                f"{value!r} has no timezone; window boundaries are UTC per D12 "
                f"and must say so explicitly"
            )
        return value.astimezone(timezone.utc).date()
    if isinstance(value, date):
        return value
    raise TypeError(f"expected a date or datetime, got {type(value)!r}")


def _as_utc_datetime(value: date | datetime) -> datetime:
    if isinstance(value, datetime):
        if value.tzinfo is None:
            raise ValueError(
                f"{value!r} has no timezone; window boundaries are UTC per D12 "
                f"and must say so explicitly"
            )
        return value.astimezone(timezone.utc)
    return datetime(value.year, value.month, value.day, tzinfo=timezone.utc)


# --------------------------------------------------------------------------
# Target dates
# --------------------------------------------------------------------------

def target_snapshot_date(window_end: date | datetime) -> date:
    """The exact date the T+45 evaluation snapshot must carry, per D7.1.

    Window CLOSE plus 45 days. Never window open: see the module docstring.
    Deliberately trivial so the one fact that matters (close, not open) is
    not buried inside a larger computation.
    """
    return _as_date(window_end) + timedelta(days=T_PLUS_DAYS)


def provisional_snapshot_date(window_end: date | datetime) -> date:
    """The exact date the T+7 provisional snapshot must carry, per D7."""
    return _as_date(window_end) + timedelta(days=PROVISIONAL_T_PLUS_DAYS)


# --------------------------------------------------------------------------
# Window state
# --------------------------------------------------------------------------

def window_state(
    window_end: date | datetime,
    *,
    forecast_published: bool,
    now: date | datetime | None = None,
    scoring_succeeded: bool | None = None,
) -> WindowState:
    """Classify a window into exactly one of D7.2's four states.

    `forecast_published` decides NEVER_PUBLISHED outright: Rule 1 does not
    care whether a window's T+45 date has arrived, only whether a forecast
    was ever committed for it. Once a forecast exists, the window is
    PUBLISHED_NOT_YET_SCOREABLE until `now` reaches `target_snapshot_date`.
    From that point on the window has a real answer to "did it score", and
    `scoring_succeeded` must be supplied to say what that answer was: a
    caller asking this function to classify a window old enough to have been
    attempted, without saying whether the attempt worked, is asking an
    unanswerable question, so this raises rather than guessing SCORED or
    guessing SCORING_FAILED.
    """
    if not forecast_published:
        return WindowState.NEVER_PUBLISHED

    reference = _as_date(now) if now is not None else datetime.now(timezone.utc).date()
    target = target_snapshot_date(window_end)
    if reference < target:
        return WindowState.PUBLISHED_NOT_YET_SCOREABLE

    if scoring_succeeded is None:
        raise ValueError(
            f"window_end {window_end} closed and its T+45 date {target} has "
            f"passed as of {reference}: scoring_succeeded must be True or "
            f"False, not None, because whether this window scored is now a "
            f"real question rather than one that has not come up yet"
        )
    return WindowState.SCORED if scoring_succeeded else WindowState.SCORING_FAILED


# --------------------------------------------------------------------------
# The systemic-versus-local snapshot check
# --------------------------------------------------------------------------

def _require_any_dated_snapshot(directory: Path) -> None:
    """Raise NoSnapshotsError if `directory` holds nothing date-shaped at all.

    `snapshots.snapshot_for_date` cannot draw this distinction itself: it
    raises the same `SnapshotNotFoundForDateError` whether the directory is
    merely missing one date or holds nothing whatsoever, because by-exact-date
    lookup has no reason to care which. Only this module needs the
    distinction, per D7.2's branch table, so it is drawn here.

    This reuses `eq.snapshots`'s own directory listing (`dated_snapshots`)
    rather than re-deriving the glob and date-shaped filename pattern D4b
    made load bearing, and it deliberately avoids the recency selector `eq.snapshots`
    also offers: that one exists to pick the most recent snapshot for use, a
    job with nothing to do with checking whether any snapshot exists.
    """
    if not snapshots.has_any_dated_snapshot(directory):
        raise snapshots.NoSnapshotsError(
            f"no date-shaped snapshot in {directory}. This is systemic: "
            f"ingest has not run, the directory is wrong, or the volume "
            f"backing it is missing. Per D7.2 this halts the entire freeze "
            f"run rather than marking any individual window SCORING FAILED, "
            f"because the fault belongs to none of them."
        )


# --------------------------------------------------------------------------
# Writing the frozen catalogue
# --------------------------------------------------------------------------

def _frozen_catalogue_filename(window_start: date | datetime, window_end: date | datetime) -> str:
    return (
        f"catalogue-{_as_date(window_start).isoformat()}"
        f"-to-{_as_date(window_end).isoformat()}.parquet"
    )


def _write_frozen_catalogue(
    events: list[dict],
    window_start: date | datetime,
    window_end: date | datetime,
    output_dir: Path | None,
) -> Path:
    directory = paths.EVALUATION_CATALOGUE_DIR if output_dir is None else Path(output_dir)
    destination = directory / _frozen_catalogue_filename(window_start, window_end)
    return storage.write_parquet_atomic(events, destination)


def _events_in_window(
    all_events: list[dict], window_start: date | datetime, window_end: date | datetime
) -> list[dict]:
    start_dt = _as_utc_datetime(window_start)
    end_dt = _as_utc_datetime(window_end)
    return [e for e in all_events if start_dt <= e["origintime"] < end_dt]


# --------------------------------------------------------------------------
# freeze_window / freeze_all
# --------------------------------------------------------------------------

def freeze_window(
    window_end: date | datetime,
    *,
    window_start: date | datetime,
    forecast: "expander.DenseForecast | None",
    stratum: str | None = None,
    snapshot_dir: Path | None = None,
    output_dir: Path | None = None,
    now: date | datetime | None = None,
) -> FrozenEvaluation:
    """Freeze and, if the time has come, score one window.

    Raises `snapshots.NoSnapshotsError` (uncaught, propagating straight out)
    if this window is old enough to score and the snapshot directory holds
    nothing dated at all: that is systemic, per D7.2, and is not turned into
    a per-window result. A missing snapshot for this window's own exact
    target date, with other snapshots present, is local and is instead
    returned as a SCORING_FAILED `FrozenEvaluation`.

    The final state (SCORED or SCORING_FAILED, once this window is old
    enough to have an answer) is always produced by calling `window_state`
    with the actual outcome of the attempt, so that function stays the
    single source of truth for what those two states mean; this function
    does not separately decide "did it score", only "how did the attempt
    go".
    """
    target = target_snapshot_date(window_end)
    forecast_published = forecast is not None
    reference = _as_date(now) if now is not None else datetime.now(timezone.utc).date()

    if not forecast_published:
        return FrozenEvaluation(
            window_start=window_start,
            window_end=window_end,
            state=window_state(window_end, forecast_published=False, now=reference),
            target_snapshot_date=target,
            reason="no forecast was ever published for this window (Rule 1)",
        )

    if reference < target:
        return FrozenEvaluation(
            window_start=window_start,
            window_end=window_end,
            state=WindowState.PUBLISHED_NOT_YET_SCOREABLE,
            target_snapshot_date=target,
            reason=(
                f"window closes {_as_date(window_end)}; the T+45 date "
                f"{target} has not arrived yet (now is {reference})"
            ),
        )

    directory = paths.SNAPSHOT_DIR if snapshot_dir is None else Path(snapshot_dir)
    # Systemic check first: propagates uncaught if nothing dated exists here
    # at all, halting the run per D7.2. Only reached once this window is
    # actually due to score, so a young window never triggers it.
    _require_any_dated_snapshot(directory)

    try:
        snapshot_path = snapshots.snapshot_for_date(target, directory)
    except snapshots.SnapshotNotFoundForDateError as exc:
        return FrozenEvaluation(
            window_start=window_start,
            window_end=window_end,
            state=window_state(
                window_end, forecast_published=True, now=reference, scoring_succeeded=False
            ),
            target_snapshot_date=target,
            reason=str(exc),
        )

    all_events = storage.read_parquet(snapshot_path)
    windowed = _events_in_window(all_events, window_start, window_end)

    catalogue_path = None
    if windowed:
        catalogue_path = _write_frozen_catalogue(windowed, window_start, window_end, output_dir)

    result = score.score(forecast, windowed, window_start, window_end, stratum=stratum)

    return FrozenEvaluation(
        window_start=window_start,
        window_end=window_end,
        state=window_state(
            window_end, forecast_published=True, now=reference, scoring_succeeded=True
        ),
        target_snapshot_date=target,
        snapshot_path=snapshot_path,
        catalogue_path=catalogue_path,
        n_events=len(windowed),
        score=result,
    )


def freeze_all(
    windows: list[WindowSpec],
    *,
    snapshot_dir: Path | None = None,
    output_dir: Path | None = None,
    now: date | datetime | None = None,
) -> FreezeRunResult:
    """Freeze every window in `windows`, independently.

    A missing target date for one window (`SnapshotNotFoundForDateError`)
    only ever affects that window; every other window in `windows` is
    attempted and scored normally. An empty snapshot directory
    (`NoSnapshotsError`) is not caught here: it propagates straight out of
    this function, so no `FreezeRunResult` is returned at all for that run,
    per D7.2's requirement to halt loudly rather than mark windows failed one
    by one.
    """
    evaluations: list[FrozenEvaluation] = []
    for window in windows:
        evaluation = freeze_window(
            window.window_end,
            window_start=window.window_start,
            forecast=window.forecast,
            stratum=window.stratum,
            snapshot_dir=snapshot_dir,
            output_dir=output_dir,
            now=now,
        )
        evaluations.append(evaluation)
    return FreezeRunResult(evaluations=evaluations)


# --------------------------------------------------------------------------
# Provisional (T+7) scoring, and the drift between provisional and final
# --------------------------------------------------------------------------

def provisional_score(
    window_end: date | datetime,
    *,
    window_start: date | datetime,
    forecast: "expander.DenseForecast",
    stratum: str | None = None,
    snapshot_dir: Path | None = None,
) -> ProvisionalScore | None:
    """A best-effort early read at T+7, or None if that day has not landed.

    Unlike the T+45 freeze, a missing T+7 snapshot is not reported as a
    failure of any kind: D7.2 describes the provisional score as something
    that "may be shown", not something owed. Its absence is quiet; a caller
    simply gets None and can try again once ingest has caught up.
    """
    target = provisional_snapshot_date(window_end)
    directory = paths.SNAPSHOT_DIR if snapshot_dir is None else Path(snapshot_dir)

    try:
        snapshot_path = snapshots.snapshot_for_date(target, directory)
    except snapshots.SnapshotNotFoundForDateError:
        return None

    all_events = storage.read_parquet(snapshot_path)
    windowed = _events_in_window(all_events, window_start, window_end)
    result = score.score(forecast, windowed, window_start, window_end, stratum=stratum)

    return ProvisionalScore(
        window_start=window_start,
        window_end=window_end,
        label=PROVISIONAL_LABEL,
        snapshot_date=target,
        score=result,
    )


def drift(provisional: ProvisionalScore, final: FrozenEvaluation) -> Drift:
    """How far the T+7 provisional read moved by the time T+45 landed.

    Raises ValueError if `final` was never actually scored: there is nothing
    to compare a provisional read against in that case, and returning a
    Drift of zeroes would misrepresent a window that has not been scored yet
    as one that scored identically both times.
    """
    if final.score is None:
        raise ValueError(
            f"final evaluation is in state {final.state}, not SCORED: there "
            f"is no score of record yet to compare the provisional read "
            f"against"
        )
    return Drift(
        provisional_n_events=provisional.score.n_events_used,
        final_n_events=final.score.n_events_used,
        n_events_difference=final.score.n_events_used - provisional.score.n_events_used,
        provisional_n_statistic=provisional.score.n_test.observed_statistic,
        final_n_statistic=final.score.n_test.observed_statistic,
    )
