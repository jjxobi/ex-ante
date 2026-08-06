"""Notices when the daily scheduler has stopped, before a reader has to.

Design spec section 4.7: "Fails if the newest forecast is more than 48 hours
old, opening or updating a GitHub issue." The risk this guards against is
specific and stated there too: GitHub disables a scheduled workflow after
about 60 days of repository inactivity, and the failure mode is silent, which
is fatal for a project whose entire premise is uninterrupted daily operation.
A record that quietly stops accumulating is worse than one that visibly
fails, because nothing about a static site says "this stopped updating
11 months ago" unless something is watching for it.

WHY 48 HOURS, NOT 24. The daily cron publishes ahead of tomorrow's window
(D10, T-2h), so under normal operation the newest forecast is never much more
than a day old. 48 hours gives one full missed run of slack before this
raises an alarm, rather than firing on the first ordinary hiccup (a delayed
run, a transient GitHub Actions outage) the same day it happens.

WHAT THIS MODULE CHECKS. The newest `published_at_utc` across every manifest
this project has ever written, found by walking `eq.paths.FORECASTS_DIR`
rather than trusting any single model, horizon or stratum's own directory:
a scheduler that silently stopped touching one of the eight forecast types
while the other seven kept running is still a partial failure worth
surfacing, so the newest manifest across all of them is what is checked, not
the newest for some assumed-representative one.

WHAT THIS MODULE DOES NOT DO. It does not call the GitHub API itself, open an
issue, or know anything about `gh`. `HealthCheck` carries everything a
workflow step needs to do that (a title, a body, and the machine-readable
fields both are built from) so the GitHub-specific part stays in the
workflow YAML, where GITHUB_TOKEN and the `gh` CLI already live, rather than
being reimplemented against the API in Python.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path

from eq import paths

# Design spec 4.7 / this module's docstring above: the alarm threshold.
STALE_AFTER = timedelta(hours=48)

# A stable marker embedded in every issue this module's output produces, so a
# workflow step can find and update its own previously opened issue (by
# searching for this string) instead of opening a new one on every failing
# run.
ISSUE_MARKER = "<!-- eq-health-check -->"


@dataclass(frozen=True)
class HealthCheck:
    """The outcome of one health check.

    `healthy` is the single field a workflow step needs to decide whether to
    fail the job. Everything else is detail carried along so that decision
    is auditable rather than a bare boolean a reader has to trust.
    """

    healthy: bool
    checked_at: datetime
    newest_published_at: datetime | None
    age: timedelta | None
    newest_forecast: dict | None
    reason: str

    def age_hours(self) -> float | None:
        return None if self.age is None else self.age.total_seconds() / 3600.0


def _iso(value: datetime) -> str:
    return value.strftime("%Y-%m-%dT%H:%M:%SZ")


def _parse_utc(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(timezone.utc)


def _iter_manifests(directory: Path):
    """Every manifest ever published, in no particular order.

    Walks the directory tree rather than assuming a fixed model/horizon/
    stratum layout, so a health check does not silently miss a forecast type
    that was added, renamed, or misconfigured after this function was
    written.
    """
    if not directory.exists():
        return
    for path in directory.rglob("*.manifest.json"):
        yield path


def newest_publication(directory: Path | None = None) -> tuple[datetime, dict, Path] | None:
    """The most recently published forecast's (published_at, manifest, path),
    across every model, horizon and stratum, or None if nothing has ever been
    published.
    """
    forecasts_dir = paths.FORECASTS_DIR if directory is None else Path(directory)
    best: tuple[datetime, dict, Path] | None = None
    for manifest_path in _iter_manifests(forecasts_dir):
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        published_at = _parse_utc(manifest["published_at_utc"])
        if best is None or published_at > best[0]:
            best = (published_at, manifest, manifest_path)
    return best


def check_health(
    *,
    directory: Path | None = None,
    now: datetime | None = None,
    stale_after: timedelta = STALE_AFTER,
) -> HealthCheck:
    """Fails (returns `healthy=False`) if the newest published forecast is
    older than `stale_after`, or if nothing has ever been published at all.

    An empty record is treated as unhealthy rather than vacuously healthy:
    "no forecasts exist yet" and "forecasts exist but stopped 90 days ago"
    are both states nothing is currently being published, which is exactly
    what this check exists to catch. The one case this deliberately does not
    cover is a brand new repository before its first-ever scheduled run;
    the health workflow is not expected to run until after that run has
    happened at least once.
    """
    reference = datetime.now(timezone.utc) if now is None else now.astimezone(timezone.utc)
    found = newest_publication(directory)

    if found is None:
        forecasts_dir = paths.FORECASTS_DIR if directory is None else Path(directory)
        return HealthCheck(
            healthy=False,
            checked_at=reference,
            newest_published_at=None,
            age=None,
            newest_forecast=None,
            reason=(
                f"no forecast manifests found under {forecasts_dir}; nothing has "
                f"ever been published"
            ),
        )

    published_at, manifest, manifest_path = found
    age = reference - published_at
    healthy = age <= stale_after

    if healthy:
        reason = (
            f"newest forecast ({manifest.get('model')}/{manifest.get('horizon')}/"
            f"{manifest.get('stratum')}) published {age.total_seconds() / 3600.0:.1f}h "
            f"ago, within the {stale_after.total_seconds() / 3600.0:.0f}h threshold"
        )
    else:
        reason = (
            f"newest forecast ({manifest.get('model')}/{manifest.get('horizon')}/"
            f"{manifest.get('stratum')}, {manifest_path.name}) was published at "
            f"{_iso(published_at)}, {age.total_seconds() / 3600.0:.1f}h ago, "
            f"exceeding the {stale_after.total_seconds() / 3600.0:.0f}h threshold. "
            f"The scheduler may have stopped running."
        )

    return HealthCheck(
        healthy=healthy,
        checked_at=reference,
        newest_published_at=published_at,
        age=age,
        newest_forecast=manifest,
        reason=reason,
    )


# --------------------------------------------------------------------------
# GitHub issue text. Deliberately plain functions returning strings, so a
# workflow step, or a test, can inspect exactly what would be posted without
# either of them calling the GitHub API.
# --------------------------------------------------------------------------

def issue_title(check: HealthCheck) -> str:
    return "Forecast pipeline stalled: newest forecast is stale"


def issue_body(check: HealthCheck) -> str:
    lines = [
        ISSUE_MARKER,
        "The scheduled forecast pipeline health check failed.",
        "",
        f"Checked at: {_iso(check.checked_at)}",
    ]
    if check.newest_published_at is None:
        lines.append("No forecast has ever been published.")
    else:
        lines.append(f"Newest forecast published at: {_iso(check.newest_published_at)}")
        lines.append(f"Age: {check.age_hours():.1f} hours (threshold: {STALE_AFTER.total_seconds() / 3600.0:.0f} hours)")
        if check.newest_forecast is not None:
            lines.append(
                f"Model/horizon/stratum: {check.newest_forecast.get('model')}/"
                f"{check.newest_forecast.get('horizon')}/{check.newest_forecast.get('stratum')}"
            )
    lines.append("")
    lines.append(check.reason)
    lines.append("")
    lines.append(
        "GitHub Actions disables a scheduled workflow after roughly 60 days of "
        "repository inactivity, which is a silent failure mode. Check whether "
        "the `forecast` workflow is still enabled and running, per DECISIONS.md "
        "D10/D11 and the design spec section 4.7."
    )
    return "\n".join(lines)


def to_json(check: HealthCheck) -> dict:
    """A machine-readable summary, for a workflow step to branch on (for
    example whether to open or update an issue) without re-deriving anything
    from the dataclass by hand. Carries the full `issue_title` and
    `issue_body` text too, so the health workflow's shell steps never have to
    reimplement the wording in bash: they read it back out of this JSON.
    """
    return {
        "healthy": check.healthy,
        "checked_at_utc": _iso(check.checked_at),
        "newest_published_at_utc": (
            _iso(check.newest_published_at) if check.newest_published_at else None
        ),
        "age_hours": check.age_hours(),
        "reason": check.reason,
        "issue_title": issue_title(check),
        "issue_body": issue_body(check),
        "issue_marker": ISSUE_MARKER,
    }
