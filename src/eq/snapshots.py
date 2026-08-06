"""Snapshot selection. Two mechanisms, opposite requirements, kept apart on purpose.

This project selects a catalogue snapshot in two situations that look similar
and must never be confused:

    newest_snapshot()    the most recent snapshot, whatever it is.
                         Used by ingest and by the revision diff, where the
                         question is "what does the catalogue look like now".

    snapshot_for_date()  the snapshot for one EXACT date, or an error.
                         Used by the T+45 evaluation freeze, where the question
                         is "what did the catalogue look like on this specific
                         day", and any other day's answer is wrong.

They live in one module so the contrast is visible.

WHAT THEY SHARE AND WHAT THEY DO NOT. Both call `dated_snapshots` to enumerate,
so there is exactly one definition of what counts as a dated snapshot and one
place that globs or parses a filename. What they do not share is any SELECTION
logic: deciding which snapshot to return is written separately in each, because
reusing or lightly adapting the newest-snapshot rule for the by-date case is the
shortcut that reads as fine in review and quietly produces a T+45 score computed
against the wrong day's catalogue.

That is the line worth holding. One definition of the data, two independent
contracts over it.

WHY snapshot_for_date REFUSES TO SUBSTITUTE. D7.2 requires the evaluation freeze
to fail loudly when the exact dated snapshot is missing. Falling back to the
nearest available one would make "frozen at T+45" mean something different for
that window than for every other window, silently and case by case, which is
exactly the property the freeze exists to eliminate. A missing snapshot is a gap
to report, not a hole to paper over.

WHY THE FILENAME PATTERN IS LOAD BEARING. Both selectors match date-shaped names
only. Continuous integration once wrote `catalogue-ci.parquet`, and because "c"
sorts after "2" a lexical maximum over `catalogue-*.parquet` returns that file
in preference to every real dated catalogue. That was verified against DuckDB
rather than reasoned about, and it is recorded in D4b. The pattern here is the
fix, and it must not be loosened.
"""

from __future__ import annotations

import re
from datetime import date
from pathlib import Path

from eq import paths

# Date-shaped only. Never widen this to catalogue-*.parquet; see D4b.
DATED_GLOB = "catalogue-????-??-??.parquet"
DATED_NAME = re.compile(r"^catalogue-(\d{4})-(\d{2})-(\d{2})\.parquet$")


class NoSnapshotsError(FileNotFoundError):
    """Raised when a directory holds no date-shaped snapshot at all."""


class SnapshotNotFoundForDateError(FileNotFoundError):
    """Raised when the snapshot for one specific required date is absent.

    A distinct type from NoSnapshotsError on purpose: the evaluation freeze
    needs to distinguish "nothing has ever been ingested" from "the day I must
    score against is missing", because only the second is a gap in an otherwise
    running record.
    """


def dated_snapshots(directory: Path | None = None) -> list[tuple[date, Path]]:
    """Every date-shaped snapshot, oldest first, as (date, path) pairs.

    This is the single definition of what counts as a dated snapshot, and it is
    public so that callers needing to ENUMERATE rather than SELECT have one to
    use. eq.freeze needs exactly that: it must decide whether the directory
    holds anything at all, to tell a broken pipeline apart from one missing
    day, and neither selector answers that question.

    Without this, a caller wanting that answer would write its own glob and its
    own filename pattern, leaving two definitions that must agree. This project
    has already had two mechanisms drift apart twice, so the enumeration is
    exposed rather than left private and reached into.

    Note this returns snapshots for INSPECTION. Choosing one to use goes
    through newest_snapshot or snapshot_for_date, which carry the contracts.
    Both of those call this function rather than repeating its glob or its
    filename pattern, so there is one definition here and not three that
    happen to agree.
    """
    directory = paths.SNAPSHOT_DIR if directory is None else Path(directory)
    found: list[tuple[date, Path]] = []
    for path in directory.glob(DATED_GLOB):
        match = DATED_NAME.match(path.name)
        if match is None:
            continue
        year, month, day = (int(part) for part in match.groups())
        found.append((date(year, month, day), path))
    return sorted(found)


def has_any_dated_snapshot(directory: Path | None = None) -> bool:
    """Whether any date-shaped snapshot exists at all.

    The question that separates a systemic failure, nothing has ever been
    ingested, from a local one, a particular day did not land. D7.2 requires
    those to produce different outcomes.
    """
    return bool(dated_snapshots(directory))




def newest_snapshot(directory: Path | None = None) -> Path:
    """The most recent dated snapshot.

    For ingest and the revision diff, where the question is what the catalogue
    looks like now. Do NOT use this for the evaluation freeze: see
    snapshot_for_date.

    Sorted by the date parsed out of the filename rather than by the filename
    itself, so a non-date-shaped file cannot win on lexical ordering.
    """
    found = dated_snapshots(directory)
    if not found:
        raise NoSnapshotsError(
            f"no date-shaped snapshot in "
            f"{paths.SNAPSHOT_DIR if directory is None else directory}"
        )
    return found[-1][1]


def snapshot_for_date(target: date, directory: Path | None = None) -> Path:
    """The snapshot for exactly `target`, or an error. Never a neighbour.

    For the T+45 evaluation freeze. This function has no fallback behaviour by
    design, per D7.2. If you find yourself wanting one, the answer is to report
    the window as SCORING FAILED, not to score it against a different day.
    """
    for snapshot_date, path in dated_snapshots(directory):
        if snapshot_date == target:
            return path

    available = [str(d) for d, _ in dated_snapshots(directory)]
    raise SnapshotNotFoundForDateError(
        f"no snapshot for {target}. Refusing to substitute a nearby date, "
        f"because that would make a frozen evaluation catalogue mean something "
        f"different for this window than for every other one. "
        f"Available: {available if available else 'none'}"
    )
