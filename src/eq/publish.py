"""Writes a forecast and its manifest, before the window it describes opens.

This module is Rule 1 (design spec section 5, DECISIONS.md D11) made into
code: "the publish step refuses to write a forecast whose window has already
started." Everything else here exists in service of that one refusal.

WHY THE BOUNDARY IS THE WINDOW START, NOT THE RUN TIME. D10 sets publication
lead time at T minus 2 hours, but a scheduler is not a clock. GitHub Actions
cron is documented to run several minutes late routinely, and a public
project cannot promise a satellite-grade schedule. If the refusal boundary
were "the run happened at T-2h exactly", ordinary jitter would turn a
harmless five-minute delay into a missed window. Anchoring the refusal to the
window's own start instead means a run at T-2h and a run at T-90m both
succeed, and only a run that has slid all the way past T itself is refused.
That is what `publish_forecast` checks, and nothing else: it does not
enforce, or even record disapprovingly, how close to T-2h a call actually
landed.

WHY THIS IS A HARD RAISE, NOT A LOGGED WARNING. D11 states the reason
directly: "a forecast committed after the window it describes is
indistinguishable from fraud to a reader, regardless of intent." A warning
can be missed. A raised exception cannot be silently swallowed by a workflow
step that keeps going, because the file this function would have written
never gets written, so there is nothing downstream to accidentally commit.

WHAT GETS WRITTEN. Two files per forecast: the separable forecast itself (one
rate per cell plus the fitted Gutenberg-Richter b, per D9) as JSON, and a
manifest built by `eq.anchor.manifest_for`, which already carries the file's
own SHA-256 and both D10 provenance anchors (the commit, and the GitHub
Actions run ID or its explicit, distinguishable absence). This module adds
three fields anchor.py has no way to know: which model produced the
forecast, what version of that model, and the input catalogue's own hash, so
a manifest fully answers "what code, on what data, produced this."

WHY JSON, NOT PARQUET, FOR THE FORECAST FILE ITSELF. D9 sizes a separable
forecast at about 8,200 rates: a few hundred KB of JSON at most, small enough
that human-readability at a `git show` outweighs a columnar format's benefit,
which matters more for the near-million-row catalogue and evaluation files
`eq.storage` handles. `eq.storage.write_parquet_atomic` also requires a
non-empty list of row dicts, which does not fit a forecast's shape (one rate
per cell keyed by cell id, plus a single scalar b): forcing that into rows
would need its own reader here regardless, so a small bespoke JSON writer,
temp-then-replace in the same style as `eq.anchor.write_manifest`, is the
plainer choice.

WHAT THIS MODULE DOES NOT DO. It does not fit a model: it is handed an
already-fitted separable forecast (the dict `eq.baseline.forecast` or
`eq.adaptive.forecast` returns) and an already-computed input catalogue hash.
Publication and fitting are different failure domains: a bad fit is a model
problem, a late publish is a scheduling problem, and conflating them would
make a Rule 1 refusal look like it came from the model, or a fitting failure
look like a backfill risk.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

from eq import adaptive, anchor, baseline, expander, paths, region

# D6: the two horizons this project publishes and scores.
HORIZONS = ("daily", "weekly")
HORIZON_LENGTH = {
    "daily": timedelta(days=1),
    "weekly": timedelta(days=7),
}

# D3 / D13.4b: the two depth strata every model registers on.
STRATA = ("shallow", "deep")

# D13.4b: the baseline and its registered, separately scored successor.
# Kept as a mapping of model name to (fit, forecast, frozen_grid_hash) rather
# than importing eq.baseline / eq.adaptive ad hoc at each call site, so
# publish_cycle has one place that knows what "the two models" are.
_MODEL_FUNCS = {
    "baseline": (baseline.fit, baseline.forecast, baseline.FROZEN_GRID_HASH),
    "adaptive": (adaptive.fit, adaptive.forecast, adaptive.FROZEN_GRID_HASH),
}
MODELS = tuple(_MODEL_FUNCS)

# D10's "publication lead time... T minus 2 hours". Not enforced as a hard
# boundary by publish_forecast itself (see the module docstring): it is the
# target next_daily_window / next_weekly_window aim for, and Rule 1's own
# check cares only that publication happens before T, not how long before.
PUBLICATION_LEAD = timedelta(hours=2)

# A version tag per registered model, distinct from both the git commit (which
# identifies the code that ran) and the input catalogue hash (which identifies
# the data it ran on). Bumped only if a model's fitting procedure changes in a
# way that should be visible to a reader comparing forecasts across time.
MODEL_VERSIONS = {
    "baseline": "baseline-v1",
    "adaptive": "adaptive-v1",
}


class WindowAlreadyStartedError(RuntimeError):
    """Rule 1 (D11). Raised when asked to publish a forecast whose window has
    already started. See the module docstring for why this is a hard raise
    and why the boundary is the window start rather than the run time.
    """


@dataclass(frozen=True)
class PublishedForecast:
    """What publishing one forecast produced: both file paths and the
    manifest actually written, so a caller never has to re-derive a path or
    re-read a file to know what just happened.
    """

    model: str
    horizon: str
    stratum: str
    window_start: datetime
    window_end: datetime
    forecast_path: Path
    manifest_path: Path
    manifest: dict


@dataclass(frozen=True)
class PublicationCycle:
    """The outcome of `publish_cycle`: every forecast published this cycle,
    plus the input catalogue hash they all share.
    """

    published: list[PublishedForecast]
    input_catalogue_hash: str


# --------------------------------------------------------------------------
# Small helpers, duplicated in miniature rather than imported, matching this
# codebase's own convention (see eq.score._as_utc_datetime's docstring).
# --------------------------------------------------------------------------

def _as_utc_datetime(value: date | datetime) -> datetime:
    if isinstance(value, datetime):
        if value.tzinfo is None:
            raise ValueError(
                f"{value!r} has no timezone; window boundaries are UTC per D12 "
                f"and must say so explicitly"
            )
        return value.astimezone(timezone.utc)
    return datetime(value.year, value.month, value.day, tzinfo=timezone.utc)


def _iso(value: datetime) -> str:
    return value.strftime("%Y-%m-%dT%H:%M:%SZ")


def _check_model(model: str) -> None:
    if model not in MODELS:
        raise ValueError(f"model must be one of {MODELS}, got {model!r}")


def _check_horizon(horizon: str) -> None:
    if horizon not in HORIZONS:
        raise ValueError(f"horizon must be one of {HORIZONS}, got {horizon!r}")


def _check_stratum(stratum: str) -> None:
    if stratum not in STRATA:
        raise ValueError(f"stratum must be one of {STRATA}, got {stratum!r}")


# --------------------------------------------------------------------------
# Where a forecast and its manifest live
# --------------------------------------------------------------------------

def forecast_path(
    model: str,
    horizon: str,
    stratum: str,
    window_start: date | datetime,
    *,
    base_dir: Path | None = None,
) -> Path:
    """Every window boundary in this project starts at UTC midnight (D12), for
    both horizons, so the window's own date uniquely names its file: no two
    windows of the same (model, horizon, stratum) ever share a start date.
    """
    base = paths.FORECASTS_DIR if base_dir is None else Path(base_dir)
    start_date = _as_utc_datetime(window_start).date().isoformat()
    return base / model / horizon / stratum / f"{start_date}.json"


def manifest_path_for(a_forecast_path: Path) -> Path:
    """The manifest that belongs beside a given forecast file."""
    path = Path(a_forecast_path)
    return path.with_name(path.stem + ".manifest.json")


# --------------------------------------------------------------------------
# The refusal: Rule 1 itself
# --------------------------------------------------------------------------

def publish_forecast(
    *,
    model: str,
    horizon: str,
    stratum: str,
    window_start: date | datetime,
    window_end: date | datetime,
    separable: dict,
    input_catalogue_hash: str,
    model_version: str | None = None,
    now: date | datetime | None = None,
    output_dir: Path | None = None,
) -> PublishedForecast:
    """Write one forecast and its manifest, refusing outright if the window
    it describes has already started.

    `separable` is the dict `eq.baseline.forecast` or `eq.adaptive.forecast`
    returns: grid_hash, cell_ids, b, rates. Its grid_hash is checked against
    the frozen grid before anything is written, the same guard every reader
    of a separable forecast in this project applies.

    Raises `WindowAlreadyStartedError` (Rule 1, D11) if `now` is at or past
    `window_start`. `now` defaults to the real current time; tests pass it
    explicitly so the refusal boundary can be exercised without waiting for
    a clock. Raises before anything is written to disk: a refused publish
    leaves no trace to accidentally commit.
    """
    _check_model(model)
    _check_horizon(horizon)
    _check_stratum(stratum)

    start_dt = _as_utc_datetime(window_start)
    end_dt = _as_utc_datetime(window_end)
    if end_dt <= start_dt:
        raise ValueError(f"window_end {end_dt} must be after window_start {start_dt}")

    reference = datetime.now(timezone.utc) if now is None else _as_utc_datetime(now)

    # Rule 1. The boundary is the window START (D12's half-open [start, end)
    # convention: an instant equal to window_start already belongs to the
    # window), never the run time. See the module docstring for why this is
    # the only check this function performs about timing.
    if reference >= start_dt:
        raise WindowAlreadyStartedError(
            f"refusing to publish {model}/{horizon}/{stratum} for window "
            f"[{_iso(start_dt)}, {_iso(end_dt)}): the window has already "
            f"started (now={_iso(reference)}). Per DECISIONS.md D11, a "
            f"forecast committed after the window it describes is "
            f"indistinguishable from fraud to a reader, regardless of "
            f"intent, so this window is permanently unforecast rather than "
            f"backfilled."
        )

    region.assert_grid_hash(separable["grid_hash"])

    dest = forecast_path(model, horizon, stratum, start_dt, base_dir=output_dir)
    payload = {
        "model": model,
        "horizon": horizon,
        "stratum": stratum,
        "window_start_utc": _iso(start_dt),
        "window_end_utc": _iso(end_dt),
        "grid_hash": separable["grid_hash"],
        "b": separable["b"],
        "cell_ids": list(separable["cell_ids"]),
        # JSON object keys are always strings; load_forecast converts them
        # back to ints on the way in, once, in the one place that matters.
        "rates": {str(cell_id): rate for cell_id, rate in separable["rates"].items()},
    }
    dest.parent.mkdir(parents=True, exist_ok=True)
    temp_path = dest.with_name(dest.name + ".tmp")
    try:
        temp_path.write_text(
            json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8"
        )
        os.replace(temp_path, dest)
    finally:
        if temp_path.exists():
            temp_path.unlink()

    manifest = anchor.manifest_for(
        dest, window_start=start_dt, window_end=end_dt, model=model, stratum=stratum
    )
    manifest["horizon"] = horizon
    manifest["model_version"] = model_version or MODEL_VERSIONS.get(model, model)
    manifest["input_catalogue_hash"] = input_catalogue_hash
    manifest["grid_hash"] = separable["grid_hash"]

    manifest_dest = manifest_path_for(dest)
    anchor.write_manifest(manifest, manifest_dest)

    return PublishedForecast(
        model=model,
        horizon=horizon,
        stratum=stratum,
        window_start=start_dt,
        window_end=end_dt,
        forecast_path=dest,
        manifest_path=manifest_dest,
        manifest=manifest,
    )


def load_forecast(path: Path) -> dict:
    """Read a published forecast file back into the separable dict shape
    `eq.expander.expand` consumes: grid_hash, cell_ids, b, rates, the last
    with cell ids restored to ints (JSON only has string object keys).
    """
    payload = json.loads(Path(path).read_text(encoding="utf-8"))
    return {
        "grid_hash": payload["grid_hash"],
        "cell_ids": list(payload["cell_ids"]),
        "b": payload["b"],
        "rates": {int(cell_id): rate for cell_id, rate in payload["rates"].items()},
    }


# --------------------------------------------------------------------------
# Which window is next, per horizon
# --------------------------------------------------------------------------

def next_daily_window(now: date | datetime) -> tuple[datetime, datetime]:
    """Tomorrow's UTC midnight to midnight window, the daily window a run at
    roughly T-2h (D10) is publishing ahead of. Deliberately insensitive to
    what time of day `now` carries: a run any time today is publishing
    tomorrow's window, so ordinary scheduler jitter never changes which
    window is targeted, only how comfortably ahead of it the run landed.
    """
    reference = _as_utc_datetime(now)
    start_of_today = datetime(reference.year, reference.month, reference.day, tzinfo=timezone.utc)
    start = start_of_today + timedelta(days=1)
    return start, start + HORIZON_LENGTH["daily"]


def next_weekly_window(now: date | datetime) -> tuple[datetime, datetime]:
    """The next Monday-to-Monday UTC window strictly after today, per D12's
    weekly boundary. Always the upcoming Monday, never today even if today
    happens to be one: a weekly forecast is always published ahead of the
    window it describes, exactly like the daily one.
    """
    reference = _as_utc_datetime(now)
    start_of_today = datetime(reference.year, reference.month, reference.day, tzinfo=timezone.utc)
    days_until_monday = (7 - start_of_today.weekday()) % 7
    if days_until_monday == 0:
        days_until_monday = 7
    start = start_of_today + timedelta(days=days_until_monday)
    return start, start + HORIZON_LENGTH["weekly"]


NEXT_WINDOW = {
    "daily": next_daily_window,
    "weekly": next_weekly_window,
}


# --------------------------------------------------------------------------
# A full publication cycle: both models, both horizons, both strata
# --------------------------------------------------------------------------

def publish_cycle(
    events: list[dict],
    *,
    input_catalogue_path: Path,
    now: date | datetime | None = None,
    output_dir: Path | None = None,
) -> PublicationCycle:
    """Fit both registered models on both strata and publish the next daily
    and weekly window for each, per the "WHAT PUBLISHES" matrix: 2 models x
    2 horizons x 2 strata = 8 forecasts.

    `events` is the full loaded catalogue (`eq.storage.read_parquet` output)
    fitting is done against; `input_catalogue_path` is the committed snapshot
    file it came from, hashed once here and recorded identically on all 8
    manifests, since a single cycle fits every model on the same input.

    Each of the 8 calls goes through `publish_forecast`, so Rule 1 applies
    independently to every one: a call whose target window has already
    started (for example a weekly window on a day the scheduler was down for
    a week) raises exactly as a standalone call would, and this function does
    not catch that to keep the other 7 going, because a caller silently
    losing one of eight without noticing is its own kind of unreliable
    record. Callers that want partial-cycle resilience call
    `publish_forecast` directly per window instead of this convenience
    wrapper.
    """
    input_hash = anchor.sha256_file(Path(input_catalogue_path))
    reference = datetime.now(timezone.utc) if now is None else _as_utc_datetime(now)

    published: list[PublishedForecast] = []
    for model in MODELS:
        fit_fn, forecast_fn, _frozen_grid_hash = _MODEL_FUNCS[model]
        for stratum in STRATA:
            fitted = fit_fn(events, stratum)
            for horizon in HORIZONS:
                window_start, window_end = NEXT_WINDOW[horizon](reference)
                separable = forecast_fn(fitted, window_start, window_end)
                result = publish_forecast(
                    model=model,
                    horizon=horizon,
                    stratum=stratum,
                    window_start=window_start,
                    window_end=window_end,
                    separable=separable,
                    input_catalogue_hash=input_hash,
                    model_version=MODEL_VERSIONS[model],
                    now=reference,
                    output_dir=output_dir,
                )
                published.append(result)

    return PublicationCycle(published=published, input_catalogue_hash=input_hash)
