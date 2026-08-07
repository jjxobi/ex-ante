"""Renders the published record into static JSON a site can read.

Every rule this module enforces is a display obligation from DECISIONS.md,
not a computation of its own: this module classifies nothing, scores
nothing, and decides no forecast's fate. It reads what `eq.freeze` already
decided (D7.2's four window states) and what `eq.score` already flagged
(D7.1a's empty-window exemption), and it refuses to let either fact get
lost between "decided in Python" and "displayed on a page."

WHY THE FOUR STATES ARE STRUCTURALLY DISTINCT HERE, NOT JUST DIFFERENT
STRING VALUES. D7.2 exists because collapsing "not old enough yet", "we
tried and could not", and "we never forecast this at all" into one blank
cell would let a run of scheduler failures read as an uneventful stretch. It
would be possible to satisfy that sentence with four strings that differ
only cosmetically while carrying the same shape underneath, so `render_window`
below branches on `eq.freeze.WindowState` explicitly, one arm per state, and
only the SCORED arm ever includes a `tests` block. A reader (or a future
change to this file) cannot accidentally leak score-shaped data into a
window that was never scored, because there is no code path that would let
that happen.

WHY D7.1a'S LABEL IS APPLIED HERE RATHER THAN TRUSTED FROM UPSTREAM.
`eq.score.ConsistencyTestResult.applicable` already carries the right
answer; this module's only job is to never read `quantile` when it is
False, and to say why in words a reader does not have to already know
DECISIONS.md to understand: "no seismicity in window", not a blank field
and not a failing score. D14 states this as an explicit obligation because
roughly 30 percent of daily windows are empty, so this is not a rare edge
case a reader would forgive being wrong.

WHY A SKILL NUMBER CANNOT APPEAR WITHOUT ITS CAVEAT, STRUCTURALLY. D10 is
explicit: "Information gain must never be reported without naming the
benchmark's known weakness." `skill_block` is the only function in this
module that produces a `{"information_gain": ...}`-shaped value, and it
always returns the caveat in the same dict, under the same key, in the same
call. There is no `render_window` code path that writes an information gain
number any other way, so the caveat cannot be dropped by a future edit that
forgets to carry it along by hand; forgetting would have to mean deleting
the whole function's use, which removes the number too.
"""

from __future__ import annotations

import json
import os
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

from eq import expander, freeze, paths, publish, region, score

# D10: the caveat that must travel with any skill score reported against the
# baseline. Quoted, not paraphrased, so this file and DECISIONS.md cannot
# quietly drift apart in what the caveat actually claims.
BASELINE_WEAKNESS_CAVEAT = (
    "This skill score is measured against a benchmark with a known, "
    "documented deficiency: the baseline's spatial consistency test is "
    "rejected on 46 percent of scored weekly windows against 5 percent "
    "expected (DECISIONS.md D13.4b), and its smoothing bandwidth sits at "
    "the feasibility boundary of the 0.1 degree grid (D13.4a). A positive "
    "score means the forecast beat a benchmark already known to be "
    "measurably deficient, not that the forecast is good in an absolute "
    "sense."
)

NO_SEISMICITY_LABEL = "no seismicity in window"

STATE_LABELS = {
    freeze.WindowState.PUBLISHED_NOT_YET_SCOREABLE: "published, not yet scoreable",
    freeze.WindowState.SCORED: "scored",
    freeze.WindowState.SCORING_FAILED: "scoring failed",
    freeze.WindowState.NEVER_PUBLISHED: "never published",
}

HORIZON_LENGTH = {
    "daily": timedelta(days=1),
    "weekly": timedelta(days=7),
}


def _iso(value: date | datetime) -> str:
    if isinstance(value, datetime):
        if value.tzinfo is None:
            raise ValueError(f"{value!r} has no timezone; window boundaries are UTC per D12")
        return value.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    return datetime(value.year, value.month, value.day, tzinfo=timezone.utc).strftime(
        "%Y-%m-%dT%H:%M:%SZ"
    )


def _as_utc_datetime(value: date | datetime) -> datetime:
    if isinstance(value, datetime):
        if value.tzinfo is None:
            raise ValueError(f"{value!r} has no timezone; window boundaries are UTC per D12")
        return value.astimezone(timezone.utc)
    return datetime(value.year, value.month, value.day, tzinfo=timezone.utc)


# --------------------------------------------------------------------------
# One consistency test, D7.1a applied
# --------------------------------------------------------------------------

def render_test(test: "score.ConsistencyTestResult") -> dict:
    """One N/S/M/L test, as JSON. `status` is always one of "APPLICABLE" or
    "NOT_APPLICABLE"; a reader who only wants the label never has to also
    parse the quantile to know whether it means anything, because when it is
    NOT_APPLICABLE `quantile` is always null here, never pyCSEP's real but
    meaningless 1.0 (D7.1a).
    """
    if not test.applicable:
        return {
            "name": test.name,
            "status": "NOT_APPLICABLE",
            "label": NO_SEISMICITY_LABEL,
            "quantile": None,
            "observed_statistic": test.observed_statistic,
        }
    return {
        "name": test.name,
        "status": "APPLICABLE",
        "label": None,
        "quantile": test.quantile,
        "observed_statistic": test.observed_statistic,
    }


# --------------------------------------------------------------------------
# Skill against the baseline, caveat structurally attached
# --------------------------------------------------------------------------

def skill_block(information_gain: float) -> dict:
    """An information-gain figure, always paired with D10's baseline-weakness
    caveat in the same object. See the module docstring: this is the one and
    only place this project writes an information gain number, specifically
    so the caveat cannot be separated from it by a future edit.
    """
    return {
        "information_gain": information_gain,
        "baseline_caveat": BASELINE_WEAKNESS_CAVEAT,
    }


# --------------------------------------------------------------------------
# One window
# --------------------------------------------------------------------------

def render_window(
    evaluation: "freeze.FrozenEvaluation",
    *,
    model: str,
    horizon: str,
    stratum: str,
    manifest: dict | None = None,
    information_gain: float | None = None,
) -> dict:
    """One scoreboard cell, in exactly one of D7.2's four states.

    `manifest` is the published forecast's own manifest (from
    `eq.anchor.manifest_for` via `eq.publish.publish_forecast`), included
    whenever a forecast exists so a reader can see the D10 provenance
    (file hash, commit, CI anchor) beside the score. It is omitted entirely,
    never present-but-null, on a NEVER_PUBLISHED window, because there is no
    manifest to show: nothing was ever published.

    `information_gain`, when given, is wrapped through `skill_block` so its
    D10 caveat travels with it. Only meaningful, and only ever passed by a
    caller, on a SCORED window: a skill score against a window that was
    never scored, or not yet scoreable, does not exist to report.
    """
    base = {
        "model": model,
        "horizon": horizon,
        "stratum": stratum,
        "window_start_utc": _iso(evaluation.window_start),
        "window_end_utc": _iso(evaluation.window_end),
        "state": evaluation.state.value,
        "state_label": STATE_LABELS[evaluation.state],
    }

    if evaluation.state is freeze.WindowState.NEVER_PUBLISHED:
        # Rule 1 / D11: a permanent gap. No manifest, no tests, no score:
        # there is nothing here because nothing was ever published, and this
        # branch is the only place that fact is allowed to mean "gap" rather
        # than "not old enough yet" or "we tried and failed".
        base["reason"] = evaluation.reason or "no forecast was ever published for this window"
        return base

    if manifest is not None:
        # published_at and the lead it implies are carried here because they are
        # the project's central claim, and a payload that omits them forces any
        # reader of the record to go and reconstruct the one number that makes
        # the rest of it mean anything. Derived once, here, rather than by every
        # consumer: a page that computed its own lead time could disagree with
        # the manifest it is displaying.
        published_at = manifest["published_at_utc"]
        lead = _as_utc_datetime(evaluation.window_start) - _as_utc_datetime(
            datetime.fromisoformat(published_at.replace("Z", "+00:00"))
        )
        base["manifest"] = {
            "sha256": manifest["sha256"],
            "commit_sha": manifest["anchors"]["commit"]["sha"],
            "ci_anchor": manifest["anchors"]["ci_run"],
            "published_at_utc": published_at,
            "lead_hours": round(lead.total_seconds() / 3600, 1),
        }

    if evaluation.state is freeze.WindowState.PUBLISHED_NOT_YET_SCOREABLE:
        base["reason"] = evaluation.reason
        return base

    if evaluation.state is freeze.WindowState.SCORING_FAILED:
        base["reason"] = evaluation.reason
        return base

    # SCORED. The only state that ever carries a tests block, per the module
    # docstring: no other branch above reaches this far.
    result = evaluation.score
    base["n_events"] = evaluation.n_events
    base["tests"] = {
        "N": render_test(result.n_test),
        "S": render_test(result.s_test),
        "M": render_test(result.m_test),
        "L": render_test(result.l_test),
    }
    if information_gain is not None:
        base["skill"] = skill_block(information_gain)
    return base


# --------------------------------------------------------------------------
# Discovering what has been published, for gap detection
# --------------------------------------------------------------------------

def discover_manifests(
    model: str, horizon: str, stratum: str, *, forecasts_dir: Path | None = None
) -> dict[datetime, dict]:
    """Every published (model, horizon, stratum) manifest on disk, keyed by
    window_start. Used to tell "published" from "gap" when reconstructing a
    scoreboard across a date range: a window with no entry here is either in
    the future (not due yet) or a NEVER_PUBLISHED gap, decided by
    `expected_window_starts` and `freeze.freeze_window` respectively, not by
    this function.
    """
    base = paths.FORECASTS_DIR if forecasts_dir is None else Path(forecasts_dir)
    directory = base / model / horizon / stratum
    out: dict[datetime, dict] = {}
    if not directory.exists():
        return out
    for manifest_file in sorted(directory.glob("*.manifest.json")):
        manifest = json.loads(manifest_file.read_text(encoding="utf-8"))
        start = _as_utc_datetime(datetime.fromisoformat(
            manifest["window_start_utc"].replace("Z", "+00:00")
        ))
        out[start] = manifest
    return out


def earliest_published_window_start(
    model: str, horizon: str, stratum: str, *, forecasts_dir: Path | None = None
) -> datetime | None:
    """The earliest window_start ever published for this (model, horizon,
    stratum), or None if nothing has been published yet.

    This is what lets a caller (see `eq.cli`'s `render` command) render a
    full scoreboard without having to hand-configure a record-start date
    anywhere: the record's own first forecast defines where it starts. Before
    that first forecast exists there is nothing to render and nothing to
    default to, so None is returned rather than some placeholder date that
    would fabricate history the record does not have.
    """
    published = discover_manifests(model, horizon, stratum, forecasts_dir=forecasts_dir)
    if not published:
        return None
    return min(published)


def expected_window_starts(
    horizon: str, record_start: date | datetime, now: date | datetime
) -> list[datetime]:
    """Every window start of this horizon from `record_start` up to and
    including the last one at or before `now`.

    A window whose start is still in the future is not due yet and is not
    part of this list: it will show up on a future render, correctly, once
    its own start has arrived. This is what keeps a NEVER_PUBLISHED
    classification honest per D11: only a window that should already have a
    forecast can be a gap.
    """
    if horizon not in HORIZON_LENGTH:
        raise ValueError(f"horizon must be one of {tuple(HORIZON_LENGTH)}, got {horizon!r}")
    step = HORIZON_LENGTH[horizon]
    start = _as_utc_datetime(record_start)
    reference = _as_utc_datetime(now)
    starts = []
    while start <= reference:
        starts.append(start)
        start += step
    return starts


# --------------------------------------------------------------------------
# A full scoreboard for one (model, horizon, stratum)
# --------------------------------------------------------------------------

def build_scoreboard(
    model: str,
    horizon: str,
    stratum: str,
    *,
    record_start: date | datetime,
    now: date | datetime | None = None,
    forecasts_dir: Path | None = None,
    snapshot_dir: Path | None = None,
    evaluation_output_dir: Path | None = None,
) -> list[dict]:
    """Every window of this (model, horizon, stratum) from `record_start` to
    `now`, each rendered through `render_window`.

    A window with a manifest is loaded, expanded, and handed to
    `eq.freeze.freeze_window` to determine its real state (PUBLISHED_NOT_YET_
    SCOREABLE, SCORED, or SCORING_FAILED). A window with none is handed
    `forecast=None`, which `freeze_window` already turns into NEVER_PUBLISHED
    without this function having to special-case it: Rule 1's gap and D7.2's
    freeze share one code path here rather than two that could drift apart.

    `eq.freeze.freeze_window`'s own NoSnapshotsError (a systemic, empty
    evaluation-snapshot directory) is deliberately not caught here: per
    D7.2's branch table that halts the whole run rather than being absorbed
    into any one window's cell.
    """
    reference = datetime.now(timezone.utc) if now is None else _as_utc_datetime(now)
    published = discover_manifests(model, horizon, stratum, forecasts_dir=forecasts_dir)
    grid_hash = region.grid_hash()

    windows: list[dict] = []
    for window_start in expected_window_starts(horizon, record_start, reference):
        window_end = window_start + HORIZON_LENGTH[horizon]
        manifest = published.get(window_start)

        if manifest is None:
            evaluation = freeze.freeze_window(
                window_end,
                window_start=window_start,
                forecast=None,
                stratum=stratum,
                snapshot_dir=snapshot_dir,
                output_dir=evaluation_output_dir,
                now=reference,
            )
        else:
            base = paths.FORECASTS_DIR if forecasts_dir is None else Path(forecasts_dir)
            a_forecast_path = base / model / horizon / stratum / f"{window_start.date().isoformat()}.json"
            separable = publish.load_forecast(a_forecast_path)
            dense = expander.expand(separable, expected_grid_hash=grid_hash)
            evaluation = freeze.freeze_window(
                window_end,
                window_start=window_start,
                forecast=dense,
                stratum=stratum,
                snapshot_dir=snapshot_dir,
                output_dir=evaluation_output_dir,
                now=reference,
            )

        windows.append(
            render_window(evaluation, model=model, horizon=horizon, stratum=stratum, manifest=manifest)
        )

    return windows


# --------------------------------------------------------------------------
# The site payload and its write
# --------------------------------------------------------------------------

def render_site(scoreboards: dict[str, list[dict]], *, now: date | datetime | None = None) -> dict:
    """Wrap one or more named scoreboards (for example
    "baseline/daily/shallow") with the generation timestamp a reader needs
    to know how fresh the page is.
    """
    reference = datetime.now(timezone.utc) if now is None else _as_utc_datetime(now)
    return {
        "generated_at_utc": _iso(reference),
        "scoreboards": scoreboards,
    }


def write_site(payload: dict, destination: Path) -> Path:
    """Write the rendered site JSON atomically, temp-then-replace, matching
    `eq.anchor.write_manifest`'s own pattern: a crashed or failed write never
    leaves a partial page where a reader might load it.
    """
    destination = Path(destination)
    destination.parent.mkdir(parents=True, exist_ok=True)
    temp_path = destination.with_name(destination.name + ".tmp")
    try:
        temp_path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        os.replace(temp_path, destination)
    finally:
        if temp_path.exists():
            temp_path.unlink()
    return destination
