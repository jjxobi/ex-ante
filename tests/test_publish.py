"""Tests for eq.publish: Rule 1, made executable.

Hermetic by construction, same discipline as test_freeze.py and
test_score.py: real events come only from the committed
tests/fixtures/catalogue-fit-window.parquet fixture, and every forecast and
manifest a test writes lands under tmp_path, never under the repository's
own forecasts/. Fitting a baseline (and an adaptive model) is expensive, so
both are module-scoped fixtures, fit once and reused.

DO NOT RUN THE SCHEDULER: nothing here calls publish_cycle with `now=None`
(the real clock) or writes into the repository's own forecasts/ directory.
Every window used is synthetic and every `now` is passed explicitly.
"""

from __future__ import annotations

import json
import pathlib
from datetime import datetime, timedelta, timezone

import pytest

from eq import adaptive, anchor, baseline, expander, publish, region

FIXTURE = pathlib.Path(__file__).parent / "fixtures" / "catalogue-fit-window.parquet"

# A synthetic future window, far from any real calendar date this fixture's
# catalogue actually covers, so "the window has already started" is always a
# statement about the synthetic `now` passed in, never about real data.
WINDOW_START = datetime(2027, 3, 8, tzinfo=timezone.utc)  # a Monday
WINDOW_END = WINDOW_START + timedelta(days=7)


@pytest.fixture(scope="module")
def events() -> list[dict]:
    from eq import storage

    return storage.read_parquet(FIXTURE)


@pytest.fixture(scope="module")
def fitted_shallow(events) -> baseline.FittedBaseline:
    return baseline.fit(events, "shallow")


@pytest.fixture(scope="module")
def separable_shallow_week(fitted_shallow) -> dict:
    return baseline.forecast(fitted_shallow, WINDOW_START, WINDOW_END)


@pytest.fixture(autouse=True)
def _no_ci_env(monkeypatch):
    """Every test in this file starts from a clean slate for the CI anchor,
    so a developer's own GITHUB_RUN_ID (there should not be one, but nothing
    prevents one) never leaks into an assertion about its absence.
    """
    monkeypatch.delenv("GITHUB_RUN_ID", raising=False)
    monkeypatch.delenv("GITHUB_REPOSITORY", raising=False)
    monkeypatch.delenv("GITHUB_RUN_ATTEMPT", raising=False)
    monkeypatch.delenv("GITHUB_WORKFLOW", raising=False)


# ==========================================================================
# Criterion 1: Rule 1 itself
# ==========================================================================

def test_publish_refuses_a_window_that_has_already_started(tmp_path, separable_shallow_week):
    """THE test named in the brief. now is exactly the window's own start:
    per D12's half-open [start, end) convention that instant already belongs
    to the window, so this must refuse.
    """
    with pytest.raises(publish.WindowAlreadyStartedError, match="already started"):
        publish.publish_forecast(
            model="baseline",
            horizon="weekly",
            stratum="shallow",
            window_start=WINDOW_START,
            window_end=WINDOW_END,
            separable=separable_shallow_week,
            input_catalogue_hash="deadbeef",
            now=WINDOW_START,
            output_dir=tmp_path,
        )
    # And nothing was written: a refused publish leaves no trace.
    assert not any(tmp_path.rglob("*.json"))


def test_publish_refuses_a_window_long_past(tmp_path, separable_shallow_week):
    """Not just the boundary instant: a window that started days ago is
    refused identically. A missing forecast for a long-past window is a
    permanent gap (D11), never something this function will backfill.
    """
    with pytest.raises(publish.WindowAlreadyStartedError):
        publish.publish_forecast(
            model="baseline",
            horizon="weekly",
            stratum="shallow",
            window_start=WINDOW_START,
            window_end=WINDOW_END,
            separable=separable_shallow_week,
            input_catalogue_hash="deadbeef",
            now=WINDOW_START + timedelta(days=30),
            output_dir=tmp_path,
        )


# ==========================================================================
# Criterion 2: T-2h succeeds, and 30 minutes of jitter costs nothing, because
# the boundary is the window start, not the run time
# ==========================================================================

def test_publish_succeeds_at_t_minus_2_hours(tmp_path, separable_shallow_week):
    result = publish.publish_forecast(
        model="baseline",
        horizon="weekly",
        stratum="shallow",
        window_start=WINDOW_START,
        window_end=WINDOW_END,
        separable=separable_shallow_week,
        input_catalogue_hash="deadbeef",
        now=WINDOW_START - publish.PUBLICATION_LEAD,  # exactly T-2h
        output_dir=tmp_path,
    )
    assert result.forecast_path.exists()
    assert result.manifest_path.exists()


def test_publish_still_succeeds_when_the_run_is_delayed_30_minutes(tmp_path, separable_shallow_week):
    """A run delayed by 30 minutes past the T-2h target is still 90 minutes
    ahead of the window opening, so it still succeeds. Only the window's own
    start is the refusal boundary, never how close to T-2h the run landed.
    """
    delayed_now = WINDOW_START - publish.PUBLICATION_LEAD + timedelta(minutes=30)
    result = publish.publish_forecast(
        model="baseline",
        horizon="weekly",
        stratum="shallow",
        window_start=WINDOW_START,
        window_end=WINDOW_END,
        separable=separable_shallow_week,
        input_catalogue_hash="deadbeef",
        now=delayed_now,
        output_dir=tmp_path,
    )
    assert result.forecast_path.exists()
    assert delayed_now < WINDOW_START


def test_publish_succeeds_one_second_before_the_window_opens(tmp_path, separable_shallow_week):
    """The boundary is exact, not a buffer: even one second of headroom
    still succeeds. Combined with the two refusal tests above, this pins the
    exact instant the boundary sits at rather than merely "somewhere before
    the window".
    """
    result = publish.publish_forecast(
        model="baseline",
        horizon="weekly",
        stratum="shallow",
        window_start=WINDOW_START,
        window_end=WINDOW_END,
        separable=separable_shallow_week,
        input_catalogue_hash="deadbeef",
        now=WINDOW_START - timedelta(seconds=1),
        output_dir=tmp_path,
    )
    assert result.forecast_path.exists()


# ==========================================================================
# Criterion 4: manifest fields
# ==========================================================================

def test_manifest_carries_sha256_commit_and_explicit_ci_absence(tmp_path, separable_shallow_week):
    result = publish.publish_forecast(
        model="baseline",
        horizon="weekly",
        stratum="shallow",
        window_start=WINDOW_START,
        window_end=WINDOW_END,
        separable=separable_shallow_week,
        input_catalogue_hash="deadbeef" * 8,
        now=WINDOW_START - publish.PUBLICATION_LEAD,
        output_dir=tmp_path,
    )
    manifest = result.manifest
    assert manifest["sha256"] == anchor.sha256_file(result.forecast_path)
    assert manifest["anchors"]["commit"]["sha"]
    # No GITHUB_RUN_ID in this test process: the CI anchor must be an
    # explicit, distinguishable absence, never a bare null.
    assert manifest["anchors"]["ci_run"]["present"] is False
    assert manifest["anchors"]["ci_run"]["run_id"] is None
    assert "no GITHUB_RUN_ID" in manifest["anchors"]["ci_run"]["note"]
    assert manifest["model_version"] == publish.MODEL_VERSIONS["baseline"]
    assert manifest["input_catalogue_hash"] == "deadbeef" * 8
    assert manifest["grid_hash"] == separable_shallow_week["grid_hash"]
    assert manifest["horizon"] == "weekly"


def test_manifest_records_a_present_ci_anchor_when_running_under_actions(
    tmp_path, separable_shallow_week, monkeypatch
):
    monkeypatch.setenv("GITHUB_RUN_ID", "123456")
    monkeypatch.setenv("GITHUB_REPOSITORY", "example/eq-project")
    result = publish.publish_forecast(
        model="baseline",
        horizon="weekly",
        stratum="shallow",
        window_start=WINDOW_START,
        window_end=WINDOW_END,
        separable=separable_shallow_week,
        input_catalogue_hash="deadbeef",
        now=WINDOW_START - publish.PUBLICATION_LEAD,
        output_dir=tmp_path,
    )
    ci = result.manifest["anchors"]["ci_run"]
    assert ci["present"] is True
    assert ci["run_id"] == "123456"
    assert ci["run_url"] == "https://github.com/example/eq-project/actions/runs/123456"


def test_manifest_is_valid_json_on_disk(tmp_path, separable_shallow_week):
    result = publish.publish_forecast(
        model="baseline",
        horizon="weekly",
        stratum="shallow",
        window_start=WINDOW_START,
        window_end=WINDOW_END,
        separable=separable_shallow_week,
        input_catalogue_hash="deadbeef",
        now=WINDOW_START - publish.PUBLICATION_LEAD,
        output_dir=tmp_path,
    )
    on_disk = json.loads(result.manifest_path.read_text(encoding="utf-8"))
    assert on_disk == result.manifest


# ==========================================================================
# Round trip: what is written can be read back into an equivalent separable
# forecast, per eq.expander's contract
# ==========================================================================

def test_load_forecast_round_trips(tmp_path, separable_shallow_week):
    result = publish.publish_forecast(
        model="baseline",
        horizon="weekly",
        stratum="shallow",
        window_start=WINDOW_START,
        window_end=WINDOW_END,
        separable=separable_shallow_week,
        input_catalogue_hash="deadbeef",
        now=WINDOW_START - publish.PUBLICATION_LEAD,
        output_dir=tmp_path,
    )
    loaded = publish.load_forecast(result.forecast_path)
    assert loaded["grid_hash"] == separable_shallow_week["grid_hash"]
    assert loaded["b"] == separable_shallow_week["b"]
    assert sorted(loaded["cell_ids"]) == sorted(separable_shallow_week["cell_ids"])
    for cell_id, rate in separable_shallow_week["rates"].items():
        assert loaded["rates"][cell_id] == pytest.approx(rate)

    # And the loaded forecast still expands cleanly, i.e. it is genuinely
    # usable downstream, not merely round-trippable as JSON.
    dense = expander.expand(loaded, expected_grid_hash=region.grid_hash())
    assert len(dense.cell_ids) == len(separable_shallow_week["cell_ids"])


def test_publish_refuses_a_forecast_built_for_the_wrong_grid(tmp_path, separable_shallow_week):
    tampered = dict(separable_shallow_week)
    tampered["grid_hash"] = "not-the-frozen-grid-hash"
    with pytest.raises(region.GridHashMismatchError):
        publish.publish_forecast(
            model="baseline",
            horizon="weekly",
            stratum="shallow",
            window_start=WINDOW_START,
            window_end=WINDOW_END,
            separable=tampered,
            input_catalogue_hash="deadbeef",
            now=WINDOW_START - publish.PUBLICATION_LEAD,
            output_dir=tmp_path,
        )


# ==========================================================================
# next_daily_window / next_weekly_window
# ==========================================================================

def test_next_daily_window_is_tomorrow_midnight_utc():
    now = datetime(2027, 6, 15, 21, 58, tzinfo=timezone.utc)
    start, end = publish.next_daily_window(now)
    assert start == datetime(2027, 6, 16, tzinfo=timezone.utc)
    assert end == datetime(2027, 6, 17, tzinfo=timezone.utc)
    assert start > now


def test_next_weekly_window_is_the_upcoming_monday():
    # 2027-06-15 is a Tuesday.
    now = datetime(2027, 6, 15, 12, 0, tzinfo=timezone.utc)
    start, end = publish.next_weekly_window(now)
    assert start.weekday() == 0
    assert start == datetime(2027, 6, 21, tzinfo=timezone.utc)
    assert end - start == timedelta(days=7)


def test_next_weekly_window_on_a_monday_is_next_monday_not_today():
    now = datetime(2027, 6, 21, 5, 0, tzinfo=timezone.utc)  # itself a Monday
    start, _end = publish.next_weekly_window(now)
    assert start == datetime(2027, 6, 28, tzinfo=timezone.utc)
    assert start > now


# ==========================================================================
# Criterion 3: a full publication cycle produces exactly 8 forecasts
# ==========================================================================

@pytest.fixture(scope="module")
def cycle_result(tmp_path_factory, events):
    output_dir = tmp_path_factory.mktemp("publish_cycle")
    now = datetime(2027, 6, 15, 12, 0, tzinfo=timezone.utc)
    result = publish.publish_cycle(
        events, input_catalogue_path=FIXTURE, now=now, output_dir=output_dir
    )
    return result, output_dir


def test_publish_cycle_produces_exactly_sixteen_forecasts(cycle_result):
    """2 models x 2 horizons x 2 strata x 2 windows ahead.

    Was eight until the first two scheduled runs measured 210 and 79 minutes
    late. Publishing only the next window meant a run delayed past midnight
    skipped a window that Rule 1 then forbade backfilling.
    """
    result, _output_dir = cycle_result
    assert len(result.published) == 16
    combos = {(p.model, p.horizon, p.stratum) for p in result.published}
    expected = {
        (model, horizon, stratum)
        for model in publish.MODELS
        for horizon in publish.HORIZONS
        for stratum in publish.STRATA
    }
    assert combos == expected

    # Each combination covers LOOKAHEAD_WINDOWS distinct, consecutive windows,
    # so the count comes from real lookahead rather than from duplicates.
    for combo in expected:
        starts = sorted(
            p.manifest["window_start_utc"]
            for p in result.published
            if (p.model, p.horizon, p.stratum) == combo
        )
        assert len(set(starts)) == publish.LOOKAHEAD_WINDOWS, combo


def test_publish_cycle_every_forecast_has_a_manifest_on_disk(cycle_result):
    result, _output_dir = cycle_result
    for published in result.published:
        assert published.forecast_path.exists()
        assert published.manifest_path.exists()
        manifest = json.loads(published.manifest_path.read_text(encoding="utf-8"))
        assert manifest["input_catalogue_hash"] == result.input_catalogue_hash
        assert manifest["model_version"] == publish.MODEL_VERSIONS[published.model]


def test_publish_cycle_input_catalogue_hash_matches_the_fixture_file(cycle_result):
    result, _output_dir = cycle_result
    assert result.input_catalogue_hash == anchor.sha256_file(FIXTURE)


def test_publish_cycle_total_bytes_and_file_names(cycle_result):
    """Not a correctness assertion so much as a fixed record of what a real
    cycle produces, for the acceptance report: file names and total bytes on
    disk across all 32 files (16 forecasts + 16 manifests).
    """
    result, output_dir = cycle_result
    all_files = sorted(output_dir.rglob("*.json"))
    assert len(all_files) == 32
    total_bytes = sum(f.stat().st_size for f in all_files)
    assert total_bytes > 0
    # Every forecast file has a same-named manifest beside it.
    forecast_files = [f for f in all_files if not f.name.endswith(".manifest.json")]
    assert len(forecast_files) == 16


# --------------------------------------------------------------------------
# Lookahead: a late or dropped scheduler run must not cost a window
# --------------------------------------------------------------------------


def test_upcoming_windows_returns_consecutive_daily_windows():
    reference = datetime(2026, 8, 7, 1, 30, tzinfo=timezone.utc)
    windows = publish.upcoming_windows("daily", reference)

    assert [start for start, _ in windows] == [
        datetime(2026, 8, 8, tzinfo=timezone.utc),
        datetime(2026, 8, 9, tzinfo=timezone.utc),
    ]
    for start, end in windows:
        assert end - start == timedelta(days=1)


def test_upcoming_weekly_windows_stay_on_the_monday_boundary():
    """Chained, not offset. A fixed seven day step from an arbitrary reference
    would drift off D12's Monday boundary."""
    reference = datetime(2026, 8, 7, 1, 30, tzinfo=timezone.utc)  # a Friday
    windows = publish.upcoming_windows("weekly", reference)

    assert [start for start, _ in windows] == [
        datetime(2026, 8, 10, tzinfo=timezone.utc),
        datetime(2026, 8, 17, tzinfo=timezone.utc),
    ]
    assert all(start.weekday() == 0 for start, _ in windows)


def test_a_late_run_still_covers_the_window_an_on_time_run_would_have():
    """The failure this exists to prevent, stated as a test.

    Measured on the first two scheduled runs, GitHub queued them 210 and 79
    minutes late. A run intended for 22:00 that lands at 01:30 has missed the
    window it was meant to publish. With a lookahead, the previous run already
    covered it, so the delay costs a refresh rather than a permanent gap that
    Rule 1 forbids ever filling.
    """
    on_time = datetime(2026, 8, 6, 22, 0, tzinfo=timezone.utc)
    delayed = datetime(2026, 8, 8, 1, 30, tzinfo=timezone.utc)  # next day's run, late

    covered = {start for start, _ in publish.upcoming_windows("daily", on_time)}
    covered |= {start for start, _ in publish.upcoming_windows("daily", delayed)}

    # The window the delayed run itself can no longer publish.
    skipped_by_the_late_run = datetime(2026, 8, 8, tzinfo=timezone.utc)
    assert skipped_by_the_late_run in covered, (
        "a run delayed past midnight must not leave the window it skipped "
        "unpublished, or Rule 1 makes that gap permanent"
    )


def test_lookahead_windows_are_all_still_in_the_future():
    """Every window the cycle targets must be publishable under Rule 1, or the
    extra lookahead would simply raise instead of protecting anything."""
    reference = datetime(2026, 8, 7, 1, 30, tzinfo=timezone.utc)
    for horizon in publish.HORIZONS:
        for start, _ in publish.upcoming_windows(horizon, reference):
            assert start > reference
