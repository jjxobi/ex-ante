"""Tests for eq.render: the four D7.2 states and the D10 skill caveat, in JSON.

Two layers. Most tests build `eq.freeze.FrozenEvaluation` and
`eq.score.ScoreResult` values directly, the same shortcut
tests/test_zero_event_window.py takes, so a state's rendering can be checked
in isolation without the cost of fitting a model or writing a synthetic
snapshot directory. One integration test at the bottom exercises the real
path end to end: publish two windows, leave one as a gap, and render all
three through `eq.render.build_scoreboard`, using the same real fixture
catalogue and target-date arithmetic tests/test_freeze.py already
establishes.

Hermetic: every file this module writes lands under tmp_path.
"""

from __future__ import annotations

import dataclasses
import json
import pathlib
from datetime import datetime, timedelta, timezone

import pytest

from eq import expander, freeze, publish, region, render, score, storage
from eq.masked import MaskedCount

FIXTURE = pathlib.Path(__file__).parent / "fixtures" / "catalogue-fit-window.parquet"

WINDOW_START = datetime(2026, 7, 20, tzinfo=timezone.utc)
WINDOW_END = datetime(2026, 7, 27, tzinfo=timezone.utc)


def _score_result(n_events_used: int, **overrides) -> score.ScoreResult:
    """A ScoreResult built directly, matching test_zero_event_window.py's own
    helper: fast, and exercises exactly the applicable/inapplicable shape
    render_window has to respect, without fitting anything.
    """
    mask = region.grid_hash()
    defaults = {
        "n_test": score.ConsistencyTestResult(
            "N", 0.0, (1.0, 1.0), applicable=True, conditions_on_observations=False
        ),
        "s_test": score.ConsistencyTestResult("S", 0.31, 0.42, applicable=n_events_used > 0),
        "m_test": score.ConsistencyTestResult("M", 0.31, 0.55, applicable=n_events_used > 0),
        "l_test": score.ConsistencyTestResult("L", 0.31, 0.67, applicable=n_events_used > 0),
    }
    defaults.update(overrides)
    return score.ScoreResult(
        window_start=WINDOW_START,
        window_end=WINDOW_END,
        expected_count=MaskedCount(5.0, mask),
        observed_count=MaskedCount(float(n_events_used), mask),
        n_events_used=n_events_used,
        n_out_of_region=0,
        n_above_mmax=0,
        n_below_mmin=0,
        **defaults,
    )


# published_at_utc is not optional decoration: eq.anchor writes it on every
# manifest it produces, and the scoreboard derives the publication lead from it,
# which is the project's central claim. A fixture without it was not a smaller
# manifest, it was one that could never exist, and it hid the fact that render
# now depends on the field.
SAMPLE_MANIFEST = {
    "sha256": "a" * 64,
    "published_at_utc": "2026-01-01T00:00:00Z",
    "anchors": {
        "commit": {"sha": "deadbeef", "note": "n/a"},
        "ci_run": {"present": False, "run_id": None, "note": "no GITHUB_RUN_ID"},
    },
}


# ==========================================================================
# render_test: D7.1a's label
# ==========================================================================

def test_render_test_not_applicable_carries_the_no_seismicity_label():
    test = score.ConsistencyTestResult("S", 0.0, 1.0, applicable=False)
    rendered = render.render_test(test)
    assert rendered["status"] == "NOT_APPLICABLE"
    assert rendered["label"] == "no seismicity in window"
    assert rendered["quantile"] is None


def test_render_test_applicable_carries_the_real_quantile():
    test = score.ConsistencyTestResult("S", 0.31, 0.42, applicable=True)
    rendered = render.render_test(test)
    assert rendered["status"] == "APPLICABLE"
    assert rendered["label"] is None
    assert rendered["quantile"] == 0.42


def test_render_test_never_leaks_the_misleading_1_0_quantile_when_inapplicable():
    """The exact defect D7.1a documents: pyCSEP's own quantile for an
    inapplicable test is 1.0, the most passing value there is. Confirms
    render_test discards it rather than passing it through.
    """
    test = score.ConsistencyTestResult("M", 0.0, 1.0, applicable=False)
    rendered = render.render_test(test)
    assert rendered["quantile"] != 1.0
    assert rendered["quantile"] is None


# ==========================================================================
# Criterion 7: skill_block always carries the baseline weakness caveat
# ==========================================================================

def test_skill_block_carries_the_information_gain_and_the_caveat():
    block = render.skill_block(0.42)
    assert block["information_gain"] == 0.42
    assert "46 percent" in block["baseline_caveat"]
    assert "D13.4b" in block["baseline_caveat"]


def test_render_window_with_information_gain_always_includes_skill_block():
    evaluation = freeze.FrozenEvaluation(
        window_start=WINDOW_START,
        window_end=WINDOW_END,
        state=freeze.WindowState.SCORED,
        target_snapshot_date=freeze.target_snapshot_date(WINDOW_END),
        n_events=12,
        score=_score_result(12),
    )
    rendered = render.render_window(
        evaluation, model="adaptive", horizon="weekly", stratum="shallow", information_gain=0.17
    )
    assert rendered["skill"]["information_gain"] == 0.17
    assert "baseline_caveat" in rendered["skill"]
    assert rendered["skill"]["baseline_caveat"] == render.BASELINE_WEAKNESS_CAVEAT


def test_render_window_without_information_gain_omits_skill_entirely():
    evaluation = freeze.FrozenEvaluation(
        window_start=WINDOW_START,
        window_end=WINDOW_END,
        state=freeze.WindowState.SCORED,
        target_snapshot_date=freeze.target_snapshot_date(WINDOW_END),
        n_events=12,
        score=_score_result(12),
    )
    rendered = render.render_window(evaluation, model="baseline", horizon="weekly", stratum="shallow")
    assert "skill" not in rendered


# ==========================================================================
# Criterion 6: the four D7.2 states, distinguished, one JSON example each
# ==========================================================================

def test_never_published_state_is_a_gap_with_no_manifest_and_no_tests():
    evaluation = freeze.FrozenEvaluation(
        window_start=WINDOW_START,
        window_end=WINDOW_END,
        state=freeze.WindowState.NEVER_PUBLISHED,
        target_snapshot_date=freeze.target_snapshot_date(WINDOW_END),
        reason="no forecast was ever published for this window (Rule 1)",
    )
    rendered = render.render_window(
        evaluation, model="baseline", horizon="weekly", stratum="shallow", manifest=SAMPLE_MANIFEST
    )
    assert rendered["state"] == "NEVER_PUBLISHED"
    assert rendered["state_label"] == "never published"
    assert "manifest" not in rendered
    assert "tests" not in rendered
    assert "reason" in rendered


def test_published_not_yet_scoreable_state_carries_the_manifest_but_no_tests():
    evaluation = freeze.FrozenEvaluation(
        window_start=WINDOW_START,
        window_end=WINDOW_END,
        state=freeze.WindowState.PUBLISHED_NOT_YET_SCOREABLE,
        target_snapshot_date=freeze.target_snapshot_date(WINDOW_END),
        reason="window closes 2026-07-27; the T+45 date 2026-09-10 has not arrived yet",
    )
    rendered = render.render_window(
        evaluation, model="baseline", horizon="weekly", stratum="shallow", manifest=SAMPLE_MANIFEST
    )
    assert rendered["state"] == "PUBLISHED_NOT_YET_SCOREABLE"
    assert rendered["manifest"]["sha256"] == "a" * 64
    assert "tests" not in rendered
    assert "reason" in rendered


def test_scoring_failed_state_is_visible_not_blank():
    evaluation = freeze.FrozenEvaluation(
        window_start=WINDOW_START,
        window_end=WINDOW_END,
        state=freeze.WindowState.SCORING_FAILED,
        target_snapshot_date=freeze.target_snapshot_date(WINDOW_END),
        reason="no snapshot dated 2026-09-10 was found",
    )
    rendered = render.render_window(
        evaluation, model="baseline", horizon="weekly", stratum="shallow", manifest=SAMPLE_MANIFEST
    )
    assert rendered["state"] == "SCORING_FAILED"
    assert rendered["manifest"] is not None
    assert "tests" not in rendered
    assert rendered["reason"] == "no snapshot dated 2026-09-10 was found"


def test_scored_state_on_a_populated_window_marks_every_test_applicable():
    evaluation = freeze.FrozenEvaluation(
        window_start=WINDOW_START,
        window_end=WINDOW_END,
        state=freeze.WindowState.SCORED,
        target_snapshot_date=freeze.target_snapshot_date(WINDOW_END),
        n_events=12,
        score=_score_result(12),
    )
    rendered = render.render_window(
        evaluation, model="baseline", horizon="weekly", stratum="shallow", manifest=SAMPLE_MANIFEST
    )
    assert rendered["state"] == "SCORED"
    assert rendered["n_events"] == 12
    for name in ("N", "S", "M", "L"):
        assert rendered["tests"][name]["status"] == "APPLICABLE"


def test_scored_state_on_an_empty_window_labels_s_m_l_not_applicable():
    """D14's display obligation: roughly 30 percent of daily windows are
    empty and must read "no seismicity in window", not blank and not failed.
    """
    evaluation = freeze.FrozenEvaluation(
        window_start=WINDOW_START,
        window_end=WINDOW_END,
        state=freeze.WindowState.SCORED,
        target_snapshot_date=freeze.target_snapshot_date(WINDOW_END),
        n_events=0,
        score=_score_result(0),
    )
    rendered = render.render_window(
        evaluation, model="baseline", horizon="daily", stratum="shallow", manifest=SAMPLE_MANIFEST
    )
    assert rendered["state"] == "SCORED"
    assert rendered["tests"]["N"]["status"] == "APPLICABLE"  # unconditional, per D7.1a
    for name in ("S", "M", "L"):
        assert rendered["tests"][name]["status"] == "NOT_APPLICABLE"
        assert rendered["tests"][name]["label"] == "no seismicity in window"


def test_all_four_states_are_distinguishable_by_state_field_alone():
    states = set()
    for state in freeze.WindowState:
        evaluation = freeze.FrozenEvaluation(
            window_start=WINDOW_START,
            window_end=WINDOW_END,
            state=state,
            target_snapshot_date=freeze.target_snapshot_date(WINDOW_END),
            reason="synthetic",
            n_events=3 if state is freeze.WindowState.SCORED else None,
            score=_score_result(3) if state is freeze.WindowState.SCORED else None,
        )
        rendered = render.render_window(evaluation, model="baseline", horizon="weekly", stratum="shallow")
        states.add(rendered["state"])
    assert states == {"PUBLISHED_NOT_YET_SCOREABLE", "SCORED", "SCORING_FAILED", "NEVER_PUBLISHED"}


# ==========================================================================
# discover_manifests / expected_window_starts
# ==========================================================================

def test_expected_window_starts_stops_at_now_never_including_the_future():
    starts = render.expected_window_starts(
        "weekly", datetime(2026, 7, 6, tzinfo=timezone.utc), datetime(2026, 7, 21, tzinfo=timezone.utc)
    )
    assert starts == [
        datetime(2026, 7, 6, tzinfo=timezone.utc),
        datetime(2026, 7, 13, tzinfo=timezone.utc),
        datetime(2026, 7, 20, tzinfo=timezone.utc),
    ]


def test_discover_manifests_returns_empty_for_a_directory_with_nothing_published(tmp_path):
    found = render.discover_manifests("baseline", "weekly", "shallow", forecasts_dir=tmp_path)
    assert found == {}


def test_earliest_published_window_start_is_none_before_the_first_publish(tmp_path):
    assert render.earliest_published_window_start("baseline", "weekly", "shallow", forecasts_dir=tmp_path) is None


def test_earliest_published_window_start_picks_the_minimum(tmp_path, dense_shallow_week):
    later = WINDOW_START + timedelta(days=7)
    publish.publish_forecast(
        model="baseline", horizon="weekly", stratum="shallow",
        window_start=later, window_end=later + timedelta(days=7), separable=dense_shallow_week,
        input_catalogue_hash="x", now=later - timedelta(hours=2), output_dir=tmp_path,
    )
    publish.publish_forecast(
        model="baseline", horizon="weekly", stratum="shallow",
        window_start=WINDOW_START, window_end=WINDOW_END, separable=dense_shallow_week,
        input_catalogue_hash="x", now=WINDOW_START - timedelta(hours=2), output_dir=tmp_path,
    )
    earliest = render.earliest_published_window_start("baseline", "weekly", "shallow", forecasts_dir=tmp_path)
    assert earliest == WINDOW_START


# ==========================================================================
# render_site / write_site
# ==========================================================================

def test_write_site_round_trips_through_json(tmp_path):
    payload = render.render_site({"baseline/daily/shallow": [{"state": "SCORED"}]}, now=WINDOW_START)
    destination = tmp_path / "index.json"
    render.write_site(payload, destination)
    reloaded = json.loads(destination.read_text(encoding="utf-8"))
    assert reloaded == payload
    assert reloaded["generated_at_utc"] == "2026-07-20T00:00:00Z"


# ==========================================================================
# Criterion 8 / integration: publish two windows, leave one a gap, render all
# three, on the real fixture catalogue and real freeze machinery.
# ==========================================================================

@pytest.fixture(scope="module")
def fit_events() -> list[dict]:
    return storage.read_parquet(FIXTURE)


@pytest.fixture(scope="module")
def dense_shallow_week(fit_events) -> dict:
    from eq import baseline

    fitted = baseline.fit(fit_events, "shallow")
    return baseline.forecast(fitted, WINDOW_START, WINDOW_END)


def test_build_scoreboard_distinguishes_scored_gap_and_not_yet_scoreable(
    tmp_path, fit_events, dense_shallow_week
):
    forecasts_dir = tmp_path / "forecasts"
    snapshot_dir = tmp_path / "snapshots"
    evaluation_dir = tmp_path / "evaluation"
    snapshot_dir.mkdir()

    # W1 [07-20, 07-27): published, and its T+45 snapshot (2026-09-10) exists
    # with the real fixture events, so it scores.
    w1_start, w1_end = WINDOW_START, WINDOW_END
    publish.publish_forecast(
        model="baseline", horizon="weekly", stratum="shallow",
        window_start=w1_start, window_end=w1_end, separable=dense_shallow_week,
        input_catalogue_hash="abc123", now=w1_start - timedelta(hours=2), output_dir=forecasts_dir,
    )
    storage.write_parquet_atomic(fit_events, snapshot_dir / "catalogue-2026-09-10.parquet")

    # W2 [07-27, 08-03): never published. A permanent gap.
    w2_start = w1_end

    # W3 [08-03, 08-10): published, but its T+45 date (2026-09-24) has not
    # arrived by the render time used below (2026-09-11).
    w3_start = w2_start + timedelta(days=7)
    w3_end = w3_start + timedelta(days=7)
    publish.publish_forecast(
        model="baseline", horizon="weekly", stratum="shallow",
        window_start=w3_start, window_end=w3_end, separable=dense_shallow_week,
        input_catalogue_hash="abc123", now=w3_start - timedelta(hours=2), output_dir=forecasts_dir,
    )

    render_now = datetime(2026, 9, 11, tzinfo=timezone.utc)
    windows = render.build_scoreboard(
        "baseline", "weekly", "shallow",
        record_start=w1_start,
        now=render_now,
        forecasts_dir=forecasts_dir,
        snapshot_dir=snapshot_dir,
        evaluation_output_dir=evaluation_dir,
    )

    def iso(value):
        return value.strftime("%Y-%m-%dT%H:%M:%SZ")

    by_start = {w["window_start_utc"]: w for w in windows}
    w1 = by_start[iso(w1_start)]
    w2 = by_start[iso(w2_start)]
    w3 = by_start[iso(w3_start)]

    assert w1["state"] == "SCORED"
    assert w1["n_events"] > 0
    assert "manifest" in w1

    assert w2["state"] == "NEVER_PUBLISHED"
    assert "manifest" not in w2

    assert w3["state"] == "PUBLISHED_NOT_YET_SCOREABLE"
    assert "manifest" in w3
    assert "tests" not in w3

    # Never backfilled: the gap has no forecast file on disk at all.
    gap_dir = forecasts_dir / "baseline" / "weekly" / "shallow"
    assert not (gap_dir / f"{w2_start.date().isoformat()}.json").exists()


def test_the_scoreboard_carries_the_publication_lead():
    """The lead between publishing and the window opening is the project's
    central claim, so the scoreboard states it rather than leaving every
    consumer to reconstruct it and risk disagreeing with the manifest it is
    displaying.
    """
    # WINDOW_START is 2026-07-20 00:00Z, so publishing at 11:00Z the day before
    # is a lead of exactly 13 hours.
    manifest = dict(SAMPLE_MANIFEST, published_at_utc="2026-07-19T11:00:00Z")
    evaluation = freeze.FrozenEvaluation(
        window_start=WINDOW_START,
        window_end=WINDOW_END,
        state=freeze.WindowState.PUBLISHED_NOT_YET_SCOREABLE,
        target_snapshot_date=freeze.target_snapshot_date(WINDOW_END),
        reason="the T+45 date has not arrived yet",
    )
    rendered = render.render_window(
        evaluation, model="baseline", horizon="daily", stratum="shallow", manifest=manifest
    )

    assert rendered["manifest"]["lead_hours"] == 13.0
    assert rendered["manifest"]["published_at_utc"] == "2026-07-19T11:00:00Z"


def test_a_negative_lead_would_be_visible_rather_than_hidden():
    """Rule 1 makes this state unreachable through eq.publish, which refuses
    once a window has started. If it ever appears in the record anyway, the
    scoreboard must show it as the negative number it is rather than clamping
    it to zero and rendering a violation as though it were fine.
    """
    manifest = dict(SAMPLE_MANIFEST, published_at_utc="2026-07-20T06:00:00Z")
    evaluation = freeze.FrozenEvaluation(
        window_start=WINDOW_START,
        window_end=WINDOW_END,
        state=freeze.WindowState.PUBLISHED_NOT_YET_SCOREABLE,
        target_snapshot_date=freeze.target_snapshot_date(WINDOW_END),
        reason="the T+45 date has not arrived yet",
    )
    rendered = render.render_window(
        evaluation, model="baseline", horizon="daily", stratum="shallow", manifest=manifest
    )

    assert rendered["manifest"]["lead_hours"] == -6.0


def test_a_scored_window_carries_predicted_against_actual():
    """The one accuracy statement a reader without statistics can read.

    The four consistency tests are more informative and far less legible. A
    scoreboard that emits only quantiles can describe how a forecast did without
    ever telling most visitors whether it was right.
    """
    evaluation = freeze.FrozenEvaluation(
        window_start=WINDOW_START,
        window_end=WINDOW_END,
        state=freeze.WindowState.SCORED,
        target_snapshot_date=freeze.target_snapshot_date(WINDOW_END),
        n_events=12,
        score=_score_result(12),
    )
    rendered = render.render_window(
        evaluation, model="baseline", horizon="weekly", stratum="shallow"
    )

    counts = rendered["counts"]
    assert counts["predicted"] == 5.0
    assert counts["actual"] == 12.0
    assert counts["difference"] == 7.0
    # The mask travels with the numbers, so a reader can tell what region they
    # were taken over rather than assuming.
    assert counts["mask_id"] == region.grid_hash()


def test_predicted_and_actual_are_never_emitted_across_different_masks():
    """compare_counts is the guard, and this pins that render goes through it.

    Reading expected_count and observed_count directly would look identical and
    silently publish a count over the region beside a count over everything,
    which is the error that once reversed the sign of the D14 finding.
    """
    result = _score_result(12)
    mismatched = dataclasses.replace(
        result, observed_count=MaskedCount(12.0, "a-different-region")
    )
    evaluation = freeze.FrozenEvaluation(
        window_start=WINDOW_START,
        window_end=WINDOW_END,
        state=freeze.WindowState.SCORED,
        target_snapshot_date=freeze.target_snapshot_date(WINDOW_END),
        n_events=12,
        score=mismatched,
    )
    with pytest.raises(ValueError):
        render.render_window(
            evaluation, model="baseline", horizon="weekly", stratum="shallow"
        )
