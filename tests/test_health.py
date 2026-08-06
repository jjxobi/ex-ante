"""Tests for eq.health: noticing a stalled scheduler before a reader does.

Hermetic and fast: this module never fits a model or reads real events, only
small synthetic manifest JSON files written directly under tmp_path, in the
shape eq.publish.publish_forecast actually produces (published_at_utc,
model, horizon, stratum), so a test never depends on data/ or on running the
real publish pipeline.
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from eq import health

NOW = datetime(2027, 1, 10, 12, 0, tzinfo=timezone.utc)


def _write_manifest(
    directory: Path,
    *,
    model: str,
    horizon: str,
    stratum: str,
    published_at: datetime,
    window_start: str = "2027-01-05",
) -> Path:
    target = directory / model / horizon / stratum
    target.mkdir(parents=True, exist_ok=True)
    manifest_path = target / f"{window_start}.manifest.json"
    manifest_path.write_text(
        json.dumps(
            {
                "file": f"forecasts/{model}/{horizon}/{stratum}/{window_start}.json",
                "sha256": "a" * 64,
                "window_start_utc": f"{window_start}T00:00:00Z",
                "window_end_utc": f"{window_start}T00:00:00Z",
                "model": model,
                "stratum": stratum,
                "horizon": horizon,
                "published_at_utc": published_at.strftime("%Y-%m-%dT%H:%M:%SZ"),
                "anchors": {
                    "commit": {"sha": "deadbeef", "note": "n/a"},
                    "ci_run": {"present": False, "run_id": None, "note": "n/a"},
                },
            }
        ),
        encoding="utf-8",
    )
    return manifest_path


# ==========================================================================
# Criterion 5: healthy for fresh, unhealthy for 49 hours old
# ==========================================================================

def test_healthy_for_a_fresh_forecast(tmp_path):
    _write_manifest(
        tmp_path, model="baseline", horizon="daily", stratum="shallow",
        published_at=NOW - timedelta(hours=1),
    )
    check = health.check_health(directory=tmp_path, now=NOW)
    assert check.healthy is True
    assert check.age_hours() == pytest.approx(1.0, abs=0.01)


def test_unhealthy_for_a_forecast_49_hours_old(tmp_path):
    _write_manifest(
        tmp_path, model="baseline", horizon="daily", stratum="shallow",
        published_at=NOW - timedelta(hours=49),
    )
    check = health.check_health(directory=tmp_path, now=NOW)
    assert check.healthy is False
    assert check.age_hours() == pytest.approx(49.0, abs=0.01)
    assert "scheduler may have stopped" in check.reason.lower()


def test_exactly_at_the_48_hour_threshold_is_still_healthy(tmp_path):
    """The threshold itself is inclusive: 48.0 hours has not yet exceeded
    the 48 hour limit, only something strictly older has.
    """
    _write_manifest(
        tmp_path, model="baseline", horizon="daily", stratum="shallow",
        published_at=NOW - timedelta(hours=48),
    )
    check = health.check_health(directory=tmp_path, now=NOW)
    assert check.healthy is True


def test_one_second_past_the_threshold_is_unhealthy(tmp_path):
    _write_manifest(
        tmp_path, model="baseline", horizon="daily", stratum="shallow",
        published_at=NOW - timedelta(hours=48, seconds=1),
    )
    check = health.check_health(directory=tmp_path, now=NOW)
    assert check.healthy is False


# ==========================================================================
# Nothing ever published
# ==========================================================================

def test_unhealthy_when_nothing_has_ever_been_published(tmp_path):
    check = health.check_health(directory=tmp_path, now=NOW)
    assert check.healthy is False
    assert check.newest_published_at is None
    assert "nothing has ever been published" in check.reason.lower()


def test_unhealthy_when_the_directory_does_not_exist(tmp_path):
    check = health.check_health(directory=tmp_path / "does-not-exist", now=NOW)
    assert check.healthy is False


# ==========================================================================
# The newest across every model, horizon and stratum wins
# ==========================================================================

def test_newest_publication_is_found_across_all_eight_types(tmp_path):
    _write_manifest(
        tmp_path, model="baseline", horizon="daily", stratum="shallow",
        published_at=NOW - timedelta(hours=40),
    )
    _write_manifest(
        tmp_path, model="adaptive", horizon="weekly", stratum="deep",
        published_at=NOW - timedelta(hours=2), window_start="2027-01-06",
    )
    check = health.check_health(directory=tmp_path, now=NOW)
    assert check.healthy is True
    assert check.newest_forecast["model"] == "adaptive"
    assert check.newest_forecast["horizon"] == "weekly"
    assert check.newest_forecast["stratum"] == "deep"


def test_one_stale_type_among_seven_fresh_ones_is_still_reported_by_its_own_newest(tmp_path):
    """This function reports the newest across everything, which is the
    healthy answer even if one particular (model, horizon, stratum) has gone
    silent while the other seven keep running. Documented here so that
    limitation is visible rather than assumed away: per-type staleness is a
    real gap this function does not close on its own.
    """
    _write_manifest(
        tmp_path, model="baseline", horizon="daily", stratum="shallow",
        published_at=NOW - timedelta(hours=1),
    )
    _write_manifest(
        tmp_path, model="adaptive", horizon="weekly", stratum="deep",
        published_at=NOW - timedelta(hours=200), window_start="2026-12-01",
    )
    check = health.check_health(directory=tmp_path, now=NOW)
    assert check.healthy is True
    assert check.newest_forecast["model"] == "baseline"


# ==========================================================================
# GitHub issue text
# ==========================================================================

def test_issue_body_carries_the_marker_and_the_age(tmp_path):
    _write_manifest(
        tmp_path, model="baseline", horizon="daily", stratum="shallow",
        published_at=NOW - timedelta(hours=60),
    )
    check = health.check_health(directory=tmp_path, now=NOW)
    body = health.issue_body(check)
    assert health.ISSUE_MARKER in body
    assert "60.0 hours" in body
    assert "baseline" in body


def test_to_json_is_serialisable_and_carries_the_healthy_flag(tmp_path):
    _write_manifest(
        tmp_path, model="baseline", horizon="daily", stratum="shallow",
        published_at=NOW - timedelta(hours=1),
    )
    check = health.check_health(directory=tmp_path, now=NOW)
    payload = health.to_json(check)
    # Round trips through json.dumps without a TypeError, i.e. is genuinely
    # what a workflow step would write to a file or pass between steps.
    reloaded = json.loads(json.dumps(payload))
    assert reloaded["healthy"] is True
    assert reloaded["age_hours"] == pytest.approx(1.0, abs=0.01)
