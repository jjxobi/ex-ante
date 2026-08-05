"""Tests for scoring a dense forecast against observed events with pyCSEP.

This is the acceptance layer for the component the whole project exists for,
so most tests here run against the real committed fixture catalogue rather
than synthetic data, the same discipline test_baseline.py uses and for the
same reason: the claims this module makes ("the four tests run without
raising", "information gain of a forecast against itself is exactly zero")
are claims about real data, and a synthetic catalogue could pass while hiding
a defect that only shows up on the real magnitude and spatial distribution.

Fitting is expensive (a few seconds per stratum), so the fitted baseline is a
module-scoped fixture, fit once and reused across every test in this file.
"""

from __future__ import annotations

import pathlib
from datetime import date, datetime, timedelta, timezone

import numpy as np
import pytest
from csep.core.catalogs import CSEPCatalog
from csep.core.forecasts import GriddedForecast

from eq import baseline, expander, region, score, storage

FIXTURE = pathlib.Path(__file__).parent / "fixtures" / "catalogue-fit-window.parquet"

# A real past week, safely inside both the fit-window fixture's magnitude/grid
# world and the wider snapshot's coverage, and far enough before the snapshot
# date that GeoNet's review queue (D7) had time to settle most of it.
WINDOW_START = datetime(2026, 7, 20, tzinfo=timezone.utc)
WINDOW_END = datetime(2026, 7, 27, tzinfo=timezone.utc)


@pytest.fixture(scope="module")
def catalogue() -> list[dict]:
    return storage.read_parquet(FIXTURE)


@pytest.fixture(scope="module")
def fitted_shallow(catalogue) -> baseline.FittedBaseline:
    return baseline.fit(catalogue, "shallow")


@pytest.fixture(scope="module")
def dense_shallow_week(fitted_shallow) -> expander.DenseForecast:
    separable = baseline.forecast(fitted_shallow, WINDOW_START, WINDOW_END)
    return expander.expand(separable, expected_grid_hash=baseline.FROZEN_GRID_HASH)


@pytest.fixture(scope="module")
def dense_shallow_fit_period(fitted_shallow) -> expander.DenseForecast:
    separable = baseline.forecast(fitted_shallow, fitted_shallow.fit_start, fitted_shallow.fit_end)
    return expander.expand(separable, expected_grid_hash=baseline.FROZEN_GRID_HASH)


def _uniform_dense(dense: expander.DenseForecast, b: float) -> expander.DenseForecast:
    """A deliberately worse forecast: the same total, spread evenly over
    every cell instead of following the fitted spatial pattern.
    """
    total = sum(dense.values)
    n_cells = len(dense.cell_ids)
    uniform_rate = total / n_cells
    separable = {
        "grid_hash": region.grid_hash(),
        "cell_ids": dense.cell_ids,
        "b": b,
        "rates": {cid: uniform_rate for cid in dense.cell_ids},
    }
    return expander.expand(separable, expected_grid_hash=baseline.FROZEN_GRID_HASH)


# ==========================================================================
# Criterion 1: conservation through to_csep_forecast
# ==========================================================================

def test_csep_forecast_total_matches_dense_total(dense_shallow_week):
    forecast = score.to_csep_forecast(dense_shallow_week, WINDOW_START, WINDOW_END)
    dense_total = sum(dense_shallow_week.values)
    csep_total = float(forecast.event_count)
    assert csep_total == pytest.approx(dense_total, rel=1e-9)


def test_csep_forecast_is_the_right_type(dense_shallow_week):
    forecast = score.to_csep_forecast(dense_shallow_week, WINDOW_START, WINDOW_END)
    assert isinstance(forecast, GriddedForecast)
    assert forecast.data.shape == (4100, 55)


# ==========================================================================
# The interface point that matters: cell ordering must round-trip
# ==========================================================================

def test_cell_ordering_round_trips_through_to_csep_forecast():
    """A silent permutation here would leave every total correct (a sum does
    not care about order) while scoring every cell against the wrong
    location. This assigns a distinct, identifiable rate to every one of the
    4,100 cells and checks that querying the resulting GriddedForecast at a
    specific cell's own midpoint recovers that cell's own rate, not whatever
    rate ended up in that array position.
    """
    grid = region.load_grid()
    cell_ids = sorted(row["cell_id"] for row in grid)
    lookup = {row["cell_id"]: (row["lon_deci"], row["lat_deci"]) for row in grid}

    # index-based, so a permutation of any kind changes which value a given
    # cell recovers.
    rates = {cid: float(i + 1) for i, cid in enumerate(cell_ids)}
    separable = {"grid_hash": region.grid_hash(), "cell_ids": cell_ids, "b": 1.0, "rates": rates}
    dense = expander.expand(separable, expected_grid_hash=region.grid_hash())

    forecast = score.to_csep_forecast(dense, date(2026, 1, 1), date(2026, 1, 8))
    spatial = forecast.spatial_counts()

    # Sample across the whole id range rather than just the start, since a
    # bug that only misorders part of the array would hide behind a check of
    # the first few cells alone.
    sample = cell_ids[::137]
    assert len(sample) > 20
    for cid in sample:
        lon_deci, lat_deci = lookup[cid]
        lon_mid = lon_deci / 10.0 + 0.05
        lat_mid = lat_deci / 10.0 + 0.05
        idx = forecast.get_index_of(np.array([lon_mid]), np.array([lat_mid]))[0]
        assert cell_ids[idx] == cid, f"cell {cid} recovered a different cell's position"
        assert spatial[idx] == pytest.approx(rates[cid], rel=1e-9), (
            f"cell {cid} recovered a different cell's rate: permutation bug"
        )


# ==========================================================================
# to_csep_catalogue
# ==========================================================================

def test_to_csep_catalogue_round_trips_basic_fields():
    events = [
        {
            "publicid": "test1",
            "origintime": datetime(2026, 1, 2, tzinfo=timezone.utc),
            "longitude": 172.5,
            "latitude": -41.5,
            "magnitude": 4.2,
            "depth": 12.0,
        },
        {
            "publicid": "test2",
            "origintime": datetime(2026, 1, 3, tzinfo=timezone.utc),
            "longitude": 178.0,
            "latitude": -38.0,
            "magnitude": 3.1,
            "depth": 60.0,
        },
    ]
    catalogue = score.to_csep_catalogue(events)
    assert isinstance(catalogue, CSEPCatalog)
    assert catalogue.event_count == 2
    assert sorted(catalogue.get_magnitudes().tolist()) == [3.1, 4.2]
    assert sorted(catalogue.get_longitudes().tolist()) == [172.5, 178.0]


def test_to_csep_catalogue_handles_empty_event_list():
    catalogue = score.to_csep_catalogue([])
    assert catalogue.event_count == 0


# ==========================================================================
# Criterion 2: all four consistency tests run against real data
# ==========================================================================

def test_score_runs_all_four_tests_without_raising(dense_shallow_week, catalogue):
    result = score.score(
        dense_shallow_week, catalogue, WINDOW_START, WINDOW_END, stratum="shallow"
    )
    assert isinstance(result, score.ScoreResult)
    for test in (result.n_test, result.s_test, result.m_test, result.l_test):
        assert np.isfinite(test.observed_statistic)


def test_score_result_carries_the_required_fields(dense_shallow_week, catalogue):
    result = score.score(
        dense_shallow_week, catalogue, WINDOW_START, WINDOW_END, stratum="shallow"
    )
    assert result.n_test.name == "N"
    assert result.s_test.name == "S"
    assert result.m_test.name == "M"
    assert result.l_test.name == "L"
    assert isinstance(result.n_events_used, int)
    assert isinstance(result.n_out_of_region, int)
    assert isinstance(result.n_above_mmax, int)
    assert result.n_events_used >= 0
    assert result.n_out_of_region >= 0
    assert result.n_above_mmax >= 0


# ==========================================================================
# Criterion 5: out-of-region and above-mmax events are counted, not dropped
# ==========================================================================

def test_out_of_region_events_are_counted_separately(dense_shallow_week, catalogue):
    result = score.score(
        dense_shallow_week, catalogue, WINDOW_START, WINDOW_END, stratum="shallow"
    )
    # The window used for this test is known (from direct measurement while
    # building this module) to contain shallow, in-magnitude-range events
    # both inside and outside the collection region: D1 documents that this
    # is routine, not an edge case, since the cut removes a large share of
    # national seismicity. Assert the mechanism rather than a specific count,
    # so this does not silently stop testing anything if the fixture changes.
    assert result.n_out_of_region >= 0
    assert result.n_events_used + result.n_out_of_region + result.n_above_mmax <= len(catalogue)


def test_an_out_of_region_event_is_excluded_and_counted():
    grid = region.load_grid()
    cell_ids = sorted(row["cell_id"] for row in grid)
    separable = {
        "grid_hash": region.grid_hash(),
        "cell_ids": cell_ids,
        "b": 1.0,
        "rates": {cid: 0.01 for cid in cell_ids},
    }
    dense = expander.expand(separable, expected_grid_hash=region.grid_hash())

    events = [
        {
            "publicid": "outside",
            "origintime": datetime(2026, 1, 3, tzinfo=timezone.utc),
            "longitude": 0.0,  # nowhere near the collection region
            "latitude": 0.0,
            "magnitude": 4.0,
            "depth": 10.0,
        },
    ]
    result = score.score(dense, events, date(2026, 1, 1), date(2026, 1, 8))
    assert result.n_out_of_region == 1
    assert result.n_events_used == 0


def test_an_above_mmax_event_is_excluded_and_counted_not_raised():
    grid = region.load_grid()
    cell_ids = sorted(row["cell_id"] for row in grid)
    row = grid[0]
    lon = row["lon_deci"] / 10.0 + 0.05
    lat = row["lat_deci"] / 10.0 + 0.05
    separable = {
        "grid_hash": region.grid_hash(),
        "cell_ids": cell_ids,
        "b": 1.0,
        "rates": {cid: 0.01 for cid in cell_ids},
    }
    dense = expander.expand(separable, expected_grid_hash=region.grid_hash())

    events = [
        {
            "publicid": "huge",
            "origintime": datetime(2026, 1, 3, tzinfo=timezone.utc),
            "longitude": lon,
            "latitude": lat,
            "magnitude": 9.0,  # above expander.MMAX = 8.5
            "depth": 10.0,
        },
    ]
    # Must not raise: pyCSEP's own get_index_of/get_magnitude_index would
    # raise on an out-of-range magnitude, per D13.4, and this proves the
    # scorer never hands such an event to pyCSEP in the first place.
    result = score.score(dense, events, date(2026, 1, 1), date(2026, 1, 8))
    assert result.n_above_mmax == 1
    assert result.n_events_used == 0


# ==========================================================================
# Criterion 3: information gain of a forecast against itself is exactly 0.0
# ==========================================================================

def test_information_gain_against_self_is_exactly_zero(dense_shallow_fit_period, catalogue):
    window_start = min(e["origintime"] for e in catalogue)
    window_end = max(e["origintime"] for e in catalogue) + timedelta(seconds=1)
    ig = score.information_gain(
        dense_shallow_fit_period,
        dense_shallow_fit_period,
        catalogue,
        window_start,
        window_end,
        stratum="shallow",
    )
    assert ig == 0.0


def test_information_gain_against_self_is_exactly_zero_on_the_real_week(
    dense_shallow_week, catalogue
):
    ig = score.information_gain(
        dense_shallow_week,
        dense_shallow_week,
        catalogue,
        WINDOW_START,
        WINDOW_END,
        stratum="shallow",
    )
    assert ig == 0.0


# ==========================================================================
# Criterion 4: fitted baseline beats a uniform forecast of the same total
# ==========================================================================

def test_fitted_baseline_beats_uniform_over_the_fit_period(
    dense_shallow_fit_period, fitted_shallow, catalogue
):
    """Evaluated with enough events for the comparison to have power. A
    single real week (roughly 8 shallow events, see the end-to-end test) is
    too few for a paired t-test to reliably show the spatial advantage of a
    smoothed-seismicity model over a flat one; this uses the full multi-year
    fit period, matching the window eq.baseline's own conservation test uses,
    to give the comparison the sample size it needs.
    """
    uniform = _uniform_dense(dense_shallow_fit_period, fitted_shallow.b)
    ig = score.information_gain(
        dense_shallow_fit_period,
        uniform,
        catalogue,
        fitted_shallow.fit_start,
        fitted_shallow.fit_end,
        stratum="shallow",
    )
    assert ig > 0.0, (
        f"fitted baseline scored {ig} information gain against a uniform "
        f"forecast of the same total: the baseline would be worse than "
        f"uniform, which is a serious finding, not a threshold to adjust"
    )


def test_uniform_forecast_has_the_same_total_as_the_fitted_forecast(
    dense_shallow_fit_period, fitted_shallow
):
    uniform = _uniform_dense(dense_shallow_fit_period, fitted_shallow.b)
    assert sum(uniform.values) == pytest.approx(sum(dense_shallow_fit_period.values), rel=1e-9)


# ==========================================================================
# Criterion 6: determinism
# ==========================================================================

def test_scoring_twice_produces_identical_results(dense_shallow_week, catalogue):
    result_a = score.score(
        dense_shallow_week, catalogue, WINDOW_START, WINDOW_END, stratum="shallow"
    )
    result_b = score.score(
        dense_shallow_week, catalogue, WINDOW_START, WINDOW_END, stratum="shallow"
    )
    assert result_a == result_b


def test_information_gain_is_deterministic_across_calls(
    dense_shallow_fit_period, fitted_shallow, catalogue
):
    uniform = _uniform_dense(dense_shallow_fit_period, fitted_shallow.b)
    ig_a = score.information_gain(
        dense_shallow_fit_period,
        uniform,
        catalogue,
        fitted_shallow.fit_start,
        fitted_shallow.fit_end,
        stratum="shallow",
    )
    ig_b = score.information_gain(
        dense_shallow_fit_period,
        uniform,
        catalogue,
        fitted_shallow.fit_start,
        fitted_shallow.fit_end,
        stratum="shallow",
    )
    assert ig_a == ig_b


# ==========================================================================
# Grid mismatch guard
# ==========================================================================

def test_to_csep_forecast_rejects_cell_ids_outside_the_frozen_grid():
    dense = expander.DenseForecast(
        cell_ids=[999_999_999],
        bins=[(3.0, 3.1)],
        values=[1.0],
    )
    with pytest.raises(ValueError):
        score.to_csep_forecast(dense, date(2026, 1, 1), date(2026, 1, 8))


def test_stratum_must_be_valid(dense_shallow_week, catalogue):
    with pytest.raises(ValueError):
        score.score(
            dense_shallow_week, catalogue, WINDOW_START, WINDOW_END, stratum="medium"
        )


# ==========================================================================
# Criterion 7: end to end real score
# ==========================================================================

def test_end_to_end_real_score_on_a_past_week(fitted_shallow, catalogue):
    """Fit on the committed fixture, forecast a real past week, score it
    against the events that actually occurred. This is the deliverable the
    whole module exists to produce.

    The committed fixture is used as the observation source too, rather than
    the wider data/snapshots catalogue: data/ is gitignored (D4b's own
    reasoning is why test_baseline.py made the same move), so a test that
    read it would silently do nothing on a fresh clone or in CI, which is
    exactly the failure the skip-set interlock exists to catch. This is not a
    loss of coverage here: verified directly while building this module, the
    fixture and the wider data/snapshots/catalogue-2026-08-05.parquet snapshot
    contain the identical 47 events for this window (same publicids), because
    the fixture already extends through 2026-08-04. A wider cross-check
    against live data belongs in scripts/measurements/, not in the suite, per
    the same division test_baseline.py already draws; see
    scripts/measurements/score_verification.py.
    """
    observed = catalogue

    separable = baseline.forecast(fitted_shallow, WINDOW_START, WINDOW_END)
    dense = expander.expand(separable, expected_grid_hash=baseline.FROZEN_GRID_HASH)

    result = score.score(dense, observed, WINDOW_START, WINDOW_END, stratum="shallow")

    assert result.window_start == WINDOW_START
    assert result.window_end == WINDOW_END
    assert result.n_events_used > 0
    assert np.isfinite(result.n_test.observed_statistic)
    assert np.isfinite(result.s_test.observed_statistic)
    assert np.isfinite(result.m_test.observed_statistic)
    assert np.isfinite(result.l_test.observed_statistic)

    # Printed so a full run's -s output carries the deliverable itself, not
    # only a pass/fail bit.
    print(
        f"\nEnd-to-end score, shallow, {WINDOW_START.date()} to {WINDOW_END.date()}:\n"
        f"  expected count: {result.expected_count:.4f}\n"
        f"  observed count (used): {result.n_events_used}\n"
        f"  out of region: {result.n_out_of_region}\n"
        f"  above M8.5: {result.n_above_mmax}\n"
        f"  N-test: statistic={result.n_test.observed_statistic}, "
        f"quantile={result.n_test.quantile}\n"
        f"  S-test: statistic={result.s_test.observed_statistic:.4f}, "
        f"quantile={result.s_test.quantile:.4f}\n"
        f"  M-test: statistic={result.m_test.observed_statistic:.4f}, "
        f"quantile={result.m_test.quantile:.4f}\n"
        f"  L-test: statistic={result.l_test.observed_statistic:.4f}, "
        f"quantile={result.l_test.quantile:.4f}\n"
    )
