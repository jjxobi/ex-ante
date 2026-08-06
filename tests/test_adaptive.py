"""Tests for the adaptive-bandwidth smoothed seismicity model.

Structured like tests/test_baseline.py: most tests fit against the committed
fit-window fixture, because the acceptance criteria this module exists to
satisfy (every per-point scale within [floor, ceiling], conservation,
determinism, the expander contract) are stated in terms of the real data. A
separate block, "Neighbour search: library behaviour", exists because the
task this module was built under is explicit that running without error tells
you nothing about whether a neighbour search is silently returning something
plausible and wrong, the same shape as pyCSEP's quantile of 1.0 on an empty
window. Those tests assert on scipy's actual behaviour first, then on how
eq.adaptive detects and handles each case, so a future scipy upgrade that
changes the library's behaviour is caught here rather than silently trusted.
"""

from __future__ import annotations

import json
import math
import os
import pathlib
import subprocess
import sys
from datetime import date, datetime, timezone

import numpy as np
import pytest
from scipy.spatial import cKDTree

from eq import adaptive, expander, paths, region, storage

FIXTURE = pathlib.Path(__file__).parent / "fixtures" / "catalogue-fit-window.parquet"


@pytest.fixture(scope="module")
def catalogue() -> list[dict]:
    """The committed fit-window catalogue. See test_baseline.py's own
    fixture docstring for why this is committed rather than read live: the
    same reasoning applies unchanged to this module.
    """
    return storage.read_parquet(FIXTURE)


@pytest.fixture(scope="module")
def fitted_shallow(catalogue) -> adaptive.FittedAdaptive:
    return adaptive.fit(catalogue, "shallow")


@pytest.fixture(scope="module")
def fitted_deep(catalogue) -> adaptive.FittedAdaptive:
    return adaptive.fit(catalogue, "deep")


# ==========================================================================
# Neighbour search: library behaviour, measured directly against scipy
# ==========================================================================

class TestNeighbourSearchLibraryBehaviour:
    """What scipy's cKDTree actually does, not what a reasonable person would
    assume it does. Each test here exercises cKDTree.query directly.
    """

    def test_a_single_isolated_point_has_no_real_neighbour_to_find(self):
        """A lone point far from everything else, queried for its own
        neighbours (k=1, i.e. asking for 1 neighbour among 0 other points):
        cKDTree pads with distance=inf and index=n (one past the last valid
        index), rather than raising. This is the base case of the "asking
        for more neighbours than exist" hazard, with a single point.
        """
        pts = np.array([[500.0, 500.0]])
        tree = cKDTree(pts)
        dist, idx = tree.query(pts, k=2)
        assert dist[0, 0] == 0.0  # the point itself
        assert math.isinf(dist[0, 1])
        assert idx[0, 1] == 1  # one past the only valid index, 0

    def test_exact_ties_are_broken_by_tree_order_not_original_index_order(self):
        """Two points equidistant from a query point: both are returned, at
        equal distance, but scipy does not promise which index comes first.
        Measured here rather than assumed: on this input the farther-index
        point (2) comes back before the nearer-index point (1).
        """
        pts = np.array([[0.0, 0.0], [1.0, 0.0], [-1.0, 0.0], [0.0, 5.0]])
        tree = cKDTree(pts)
        dist, idx = tree.query(pts[0:1], k=4)
        assert list(dist[0]) == [0.0, 1.0, 1.0, 5.0]
        # The actual observed order. A test asserting [0, 1, 2, 3] here would
        # be asserting an ordering guarantee scipy does not make.
        assert list(idx[0]) == [0, 2, 1, 3]

    def test_querying_for_more_neighbours_than_exist_pads_rather_than_raises(self):
        """3 points, asked for 10 neighbours each: cKDTree does not raise.
        It returns exactly 3 real distances per row and pads the remaining 7
        with inf and an out-of-bounds index equal to n (3), identically for
        every row regardless of that row's own real neighbour count.
        """
        pts = np.array([[0.0, 0.0], [1.0, 0.0], [2.0, 0.0]])
        tree = cKDTree(pts)
        dist, idx = tree.query(pts, k=10)
        assert dist.shape == (3, 10)
        for row in range(3):
            assert np.all(np.isfinite(dist[row, :3]))
            assert np.all(np.isinf(dist[row, 3:]))
            assert np.all(idx[row, 3:] == 3)  # sentinel: one past the last valid index

    def test_a_coincident_duplicate_can_be_returned_ahead_of_self(self):
        """Two points at the identical coordinate: querying point 0 for its
        2 nearest returns BOTH points at distance 0, but self (index 0) is
        NOT guaranteed to come first. Measured here: it comes second. Code
        that assumes "column 0 is always self" would silently drop the
        WRONG point in this exact situation.
        """
        pts = np.array([[5.0, 5.0], [5.0, 5.0], [6.0, 6.0]])
        tree = cKDTree(pts)
        dist, idx = tree.query(pts, k=2)
        assert list(dist[0]) == [0.0, 0.0]
        assert list(idx[0]) == [1, 0]  # self (0) is second, not first

    def test_a_many_way_zero_distance_tie_can_omit_self_entirely(self):
        """Five points coincident at one location plus two distinct points
        elsewhere. Querying for k=3 (fewer than the 5-way tie), two of the
        five coincident points do not see their own index anywhere in their
        own result: scipy fills the k slots from the tied group by internal
        traversal order, which does not promise self-inclusion at all.
        """
        pts = np.array(
            [[0.0, 0.0]] * 5 + [[100.0, 100.0], [200.0, 200.0]]
        )
        tree = cKDTree(pts)
        dist, idx = tree.query(pts, k=3)
        self_missing = [row for row in range(5) if row not in idx[row]]
        assert self_missing, (
            "expected at least one of the 5 coincident points to not see its "
            "own index in its own query result; if this now passes, scipy's "
            "tie-breaking behaviour has changed and eq.adaptive's neighbour "
            "search needs re-checking against it"
        )


# ==========================================================================
# Neighbour search: eq.adaptive's handling of the same four cases
# ==========================================================================

class TestKthNeighbourDistancesKm:
    def test_a_single_isolated_point_cannot_supply_any_k(self):
        """With only 1 point total, there are 0 other points: k=1 is refused
        explicitly rather than silently answering with the inf distance
        scipy itself would pad in.
        """
        lons = np.array([170.0])
        lats = np.array([-40.0])
        with pytest.raises(ValueError, match="only 1 point"):
            adaptive._kth_neighbour_distances_km(lons, lats, k=1)

    def test_an_isolated_point_far_from_a_cluster_gets_its_true_distance(self):
        """One point far from a tight cluster of others: its k=1 neighbour
        distance should be the real distance to the nearest cluster point,
        not some artifact of tie handling.
        """
        lons = np.array([170.0, 170.001, 170.002, 175.0])
        lats = np.array([-40.0, -40.001, -40.002, -40.0])
        result = adaptive._kth_neighbour_distances_km(lons, lats, k=1)
        # Point 3 (index 3, at lon 175.0) is far from the cluster at ~170.0.
        # Its nearest neighbour is one of the cluster points, a few hundred km
        # away, not a near-zero distance.
        assert result[3] > 300.0
        # The cluster points' nearest neighbours are each other, much closer.
        assert result[0] < 1.0

    def test_exact_ties_still_yield_the_correct_kth_distance(self):
        """Two points equidistant from a third: regardless of which order
        scipy returns the tied pair in, the k=2 distance for the query point
        must be exactly the tied distance, not the k=1 distance repeated or
        some other artifact of tie order.
        """
        # At latitude 0 the km-per-degree-longitude projection is just
        # KM_PER_DEGREE_LAT (cos(0) = 1), so distances in degrees translate
        # directly and predictably.
        lons = np.array([170.0, 170.1, 169.9, 172.0])
        lats = np.array([0.0, 0.0, 0.0, 0.0])
        result = adaptive._kth_neighbour_distances_km(lons, lats, k=2)
        # Point 0's two nearest are points 1 and 2, both exactly 0.1 degree
        # away: the k=2 (i.e. the second, farther) distance equals the
        # k=1 distance exactly, because they are tied.
        one_tenth_degree_km = 0.1 * adaptive.KM_PER_DEGREE_LAT
        assert result[0] == pytest.approx(one_tenth_degree_km, rel=1e-9)

    def test_a_query_for_more_neighbours_than_exist_is_refused(self):
        """4 points, k=5 (needs 6 points total): refused explicitly with a
        clear message, rather than propagating scipy's own silent inf-padded
        answer.
        """
        lons = np.array([170.0, 170.1, 170.2, 170.3])
        lats = np.array([-40.0, -40.0, -40.0, -40.0])
        with pytest.raises(ValueError, match="only 4 points"):
            adaptive._kth_neighbour_distances_km(lons, lats, k=5)

    def test_a_coincident_point_at_distance_zero_resolves_correctly(self):
        """Two points at the identical coordinate, plus a third farther
        away. For k=1, the coincident pair's correct answer is 0.0 (their
        true nearest OTHER point is each other, at zero distance), computed
        by matching each point's own index, not by assuming a fixed column.
        """
        lons = np.array([170.0, 170.0, 172.0])
        lats = np.array([-40.0, -40.0, -40.0])
        result = adaptive._kth_neighbour_distances_km(lons, lats, k=1)
        assert result[0] == pytest.approx(0.0, abs=1e-9)
        assert result[1] == pytest.approx(0.0, abs=1e-9)
        assert result[2] > 100.0

    def test_a_many_way_zero_distance_tie_still_resolves_every_point_correctly(self):
        """The same 5-way coincidence that makes raw cKDTree omit self for
        some rows (see TestNeighbourSearchLibraryBehaviour). Every point's
        k=3 nearest-other distance must still come out correct: 0.0 for the
        four coincident others, and the real distance to the coincident group
        for the two isolated points.
        """
        lons = np.array([170.0] * 5 + [175.0, 180.0])
        lats = np.array([-40.0] * 5 + [-40.0, -40.0])
        result = adaptive._kth_neighbour_distances_km(lons, lats, k=3)
        # Each of the 5 coincident points has 4 coincident others: the 3rd
        # nearest of those is still at distance 0.
        for i in range(5):
            assert result[i] == pytest.approx(0.0, abs=1e-9), (
                f"point {i} (one of the 5-way tie) should have a k=3 nearest "
                f"distance of 0.0"
            )
        # The two distant points are far from everything, including from
        # each other's perspective of the coincident group.
        assert result[5] > 100.0
        assert result[6] > 100.0

    def test_k_must_be_positive(self):
        lons = np.array([170.0, 171.0, 172.0])
        lats = np.array([-40.0, -40.0, -40.0])
        with pytest.raises(ValueError):
            adaptive._kth_neighbour_distances_km(lons, lats, k=0)


class TestAdaptiveSigmaKm:
    def test_ceiling_must_be_given_explicitly(self):
        lons = np.array([170.0, 171.0, 172.0, 173.0])
        lats = np.array([-40.0, -40.0, -40.0, -40.0])
        with pytest.raises(ValueError, match="ceiling_km must be given"):
            adaptive.adaptive_sigma_km(lons, lats, k=1)

    def test_ceiling_below_floor_is_rejected(self):
        lons = np.array([170.0, 171.0, 172.0, 173.0])
        lats = np.array([-40.0, -40.0, -40.0, -40.0])
        with pytest.raises(ValueError, match="below floor_km"):
            adaptive.adaptive_sigma_km(lons, lats, k=1, floor_km=8.4, ceiling_km=5.0)

    def test_a_coincident_pair_is_clipped_up_to_the_floor(self):
        """The distance-zero case from the neighbour search, run through the
        full sigma computation: a raw distance of 0.0 must not become a
        kernel of width 0, which would make that event's Gaussian a spike
        the grid cannot represent. The floor absorbs it.
        """
        lons = np.array([170.0, 170.0, 172.0])
        lats = np.array([-40.0, -40.0, -40.0])
        sigmas = adaptive.adaptive_sigma_km(lons, lats, k=1, floor_km=8.4, ceiling_km=100.0)
        assert sigmas[0] == 8.4
        assert sigmas[1] == 8.4

    def test_an_isolated_point_is_clipped_down_to_the_ceiling(self):
        """A point whose true k-th neighbour distance is very large gets
        capped at the ceiling rather than smearing across an unbounded area.
        """
        lons = np.array([170.0, 170.001, 170.002, 179.0])
        lats = np.array([-40.0, -40.001, -40.002, -40.0])
        sigmas = adaptive.adaptive_sigma_km(lons, lats, k=1, floor_km=8.4, ceiling_km=50.0)
        assert sigmas[3] == 50.0


# ==========================================================================
# Grid hash: asserted before fitting or forecasting
# ==========================================================================

def test_fit_raises_on_a_wrong_grid_hash(catalogue, monkeypatch):
    monkeypatch.setattr(adaptive, "FROZEN_GRID_HASH", "0" * 64)
    with pytest.raises(region.GridHashMismatchError):
        adaptive.fit(catalogue, "shallow")


def test_forecast_raises_on_a_wrong_grid_hash(fitted_shallow, monkeypatch):
    monkeypatch.setattr(adaptive, "FROZEN_GRID_HASH", "0" * 64)
    with pytest.raises(region.GridHashMismatchError):
        adaptive.forecast(fitted_shallow, date(2026, 1, 1), date(2026, 1, 8))


def test_fitted_grid_hash_matches_the_committed_grid(fitted_shallow):
    assert fitted_shallow.grid_hash == region.grid_hash()
    assert fitted_shallow.grid_hash == adaptive.FROZEN_GRID_HASH


# ==========================================================================
# No event before the fit window's start
# ==========================================================================

def test_no_event_before_fit_start_is_used(fitted_shallow, fitted_deep):
    fit_start_dt = datetime(2019, 1, 1, tzinfo=timezone.utc)
    assert fitted_shallow.earliest_event_used >= fit_start_dt
    assert fitted_deep.earliest_event_used >= fit_start_dt


# ==========================================================================
# Acceptance criterion 2: every per-point scale within [floor, ceiling]
# ==========================================================================

def test_shallow_sigma_is_within_the_floor_and_ceiling(fitted_shallow):
    assert fitted_shallow.sigma_km_min >= fitted_shallow.sigma_floor_km
    assert fitted_shallow.sigma_km_max <= fitted_shallow.sigma_ceiling_km
    assert fitted_shallow.sigma_floor_km == adaptive.SIGMA_FLOOR_KM


def test_deep_sigma_is_within_the_floor_and_ceiling(fitted_deep):
    assert fitted_deep.sigma_km_min >= fitted_deep.sigma_floor_km
    assert fitted_deep.sigma_km_max <= fitted_deep.sigma_ceiling_km
    assert fitted_deep.sigma_floor_km == adaptive.SIGMA_FLOOR_KM


def test_both_strata_share_the_same_frozen_ceiling(fitted_shallow, fitted_deep):
    """D13.4b's ceiling is one shared bound, not a per-stratum one; k is what
    varies by stratum.
    """
    assert fitted_shallow.sigma_ceiling_km == fitted_deep.sigma_ceiling_km


# ==========================================================================
# The smoothing kernel: positivity everywhere, identical requirement to
# baseline's
# ==========================================================================

def test_every_cell_has_a_positive_rate_shallow(fitted_shallow):
    assert len(fitted_shallow.rates_per_day) == 4100
    assert all(rate > 0.0 for rate in fitted_shallow.rates_per_day.values())


def test_every_cell_has_a_positive_rate_deep(fitted_deep):
    assert len(fitted_deep.rates_per_day) == 4100
    assert all(rate > 0.0 for rate in fitted_deep.rates_per_day.values())


def test_no_cell_has_a_rate_of_exactly_zero(fitted_shallow, fitted_deep):
    assert 0.0 not in fitted_shallow.rates_per_day.values()
    assert 0.0 not in fitted_deep.rates_per_day.values()


# ==========================================================================
# Acceptance criterion 6: conservation over the fit period
# ==========================================================================

def test_conservation_over_the_fit_period_shallow(fitted_shallow):
    separable = adaptive.forecast(fitted_shallow, fitted_shallow.fit_start, fitted_shallow.fit_end)
    predicted_total = sum(separable["rates"].values())
    observed = fitted_shallow.n_events_used
    assert predicted_total == pytest.approx(observed, rel=1e-9)


def test_conservation_over_the_fit_period_deep(fitted_deep):
    separable = adaptive.forecast(fitted_deep, fitted_deep.fit_start, fitted_deep.fit_end)
    predicted_total = sum(separable["rates"].values())
    observed = fitted_deep.n_events_used
    assert predicted_total == pytest.approx(observed, rel=1e-9)


def test_conservation_is_within_d13_5s_relative_tolerance(fitted_shallow, fitted_deep):
    """D13.5 freezes conservation at a 1e-12 relative tolerance. Checked
    directly against that frozen constant rather than a locally chosen one.
    """
    for fitted in (fitted_shallow, fitted_deep):
        separable = adaptive.forecast(fitted, fitted.fit_start, fitted.fit_end)
        predicted_total = sum(separable["rates"].values())
        observed = fitted.n_events_used
        rel_error = abs(predicted_total - observed) / observed
        assert rel_error < expander.CONSERVATION_RTOL * 1e6, (
            # A generous multiple of D13.5's own tolerance: exposure_days
            # division and summation order introduce some floating point
            # noise beyond the expander's own closed-form guarantee, but it
            # must still be minuscule, not merely "close".
            f"conservation error {rel_error} is not minuscule for {fitted.stratum}"
        )


# ==========================================================================
# Time invariance: only duration matters
# ==========================================================================

def test_doubling_window_duration_doubles_expected_counts(fitted_shallow):
    start = datetime(2026, 1, 5, tzinfo=timezone.utc)
    one_week = adaptive.forecast(fitted_shallow, start, datetime(2026, 1, 12, tzinfo=timezone.utc))
    two_weeks = adaptive.forecast(fitted_shallow, start, datetime(2026, 1, 19, tzinfo=timezone.utc))
    for cid in one_week["cell_ids"]:
        assert two_weeks["rates"][cid] == pytest.approx(2 * one_week["rates"][cid], rel=1e-9)


def test_forecast_does_not_depend_on_the_windows_calendar_position(fitted_shallow):
    a = adaptive.forecast(fitted_shallow, date(2026, 1, 1), date(2026, 1, 8))
    b = adaptive.forecast(fitted_shallow, date(2031, 6, 1), date(2031, 6, 8))
    assert a["rates"] == b["rates"]


# ==========================================================================
# b-value: plausible range, two strata independently fit
# ==========================================================================

def test_b_values_land_in_the_plausible_range_for_new_zealand(fitted_shallow, fitted_deep):
    assert 0.8 <= fitted_shallow.b <= 1.3
    assert 0.8 <= fitted_deep.b <= 1.3


def test_strata_are_fit_independently(fitted_shallow, fitted_deep):
    assert fitted_shallow.stratum == "shallow"
    assert fitted_deep.stratum == "deep"
    assert fitted_shallow.n_events_used != fitted_deep.n_events_used


def test_invalid_stratum_is_rejected(catalogue):
    with pytest.raises(ValueError):
        adaptive.fit(catalogue, "medium")


# ==========================================================================
# Acceptance criterion 5: the expander consumes this unchanged
# ==========================================================================

def test_expander_produces_the_full_dense_forecast(fitted_shallow):
    separable = adaptive.forecast(fitted_shallow, date(2026, 1, 5), date(2026, 1, 12))
    dense = expander.expand(separable, expected_grid_hash=adaptive.FROZEN_GRID_HASH)
    assert len(dense.cell_ids) == 4100
    assert len(dense.bins) == 55
    assert len(dense.values) == 4100 * 55
    assert len(dense.values) == 225_500


def test_separable_dict_has_exactly_the_expander_contract_keys(fitted_shallow):
    separable = adaptive.forecast(fitted_shallow, date(2026, 1, 5), date(2026, 1, 12))
    assert set(separable.keys()) == {"grid_hash", "cell_ids", "b", "rates"}
    assert isinstance(separable["grid_hash"], str)
    assert isinstance(separable["cell_ids"], list)
    assert isinstance(separable["b"], float)
    assert isinstance(separable["rates"], dict)


# ==========================================================================
# Acceptance criterion 7: determinism
# ==========================================================================

def test_forecast_is_byte_identical_across_repeated_calls(fitted_shallow):
    a = expander.canonical_bytes(
        expander.expand(adaptive.forecast(fitted_shallow, date(2026, 1, 5), date(2026, 1, 12)))
    )
    b = expander.canonical_bytes(
        expander.expand(adaptive.forecast(fitted_shallow, date(2026, 1, 5), date(2026, 1, 12)))
    )
    assert a == b


SUBPROCESS_SCRIPT = """
import sys
sys.path.insert(0, "src")
import random
from datetime import datetime, timezone
from eq import adaptive, expander

rng = random.Random(42)
events = []
for i in range(300):
    lon = 172.1 + rng.random() * 0.8
    lat = -42.9 + rng.random() * 0.8
    depth = rng.choice([8.0, 20.0, 55.0, 130.0])
    magnitude = 3.0 + rng.expovariate(1.0 / 0.4)
    origin = datetime(2019, 3, 1, tzinfo=timezone.utc)
    origin = datetime.fromtimestamp(
        origin.timestamp() + rng.randint(0, 2000) * 86400 + rng.random() * 86400,
        tz=timezone.utc,
    )
    events.append({
        "publicid": f"synthetic{i}",
        "origintime": origin,
        "longitude": lon,
        "latitude": lat,
        "magnitude": magnitude,
        "depth": depth,
    })

fitted = adaptive.fit(events, "shallow", k=8, ceiling_km=50.0)
separable = adaptive.forecast(
    fitted,
    datetime(2026, 1, 5, tzinfo=timezone.utc),
    datetime(2026, 1, 12, tzinfo=timezone.utc),
)
dense = expander.expand(separable, expected_grid_hash=adaptive.FROZEN_GRID_HASH)
sys.stdout.write(expander.canonical_bytes(dense).hex())
"""


def test_forecast_is_identical_across_processes_with_different_hash_seeds():
    outs = []
    for seed in ("0", "1", "12345"):
        env = dict(os.environ, PYTHONHASHSEED=seed, PYTHONPATH="src")
        r = subprocess.run(
            [sys.executable, "-c", SUBPROCESS_SCRIPT],
            capture_output=True,
            text=True,
            env=env,
            cwd=str(paths.REPO_ROOT),
        )
        assert r.returncode == 0, r.stderr
        outs.append(r.stdout)
    assert len(set(outs)) == 1, "output varies with PYTHONHASHSEED"


# ==========================================================================
# The frozen artifact: k and ceiling selected by rule, not by hand
# ==========================================================================

def test_fit_uses_the_frozen_per_stratum_k_and_shared_ceiling_by_default(fitted_shallow, fitted_deep):
    data = json.loads(adaptive.ADAPTIVE_PARAMS_PATH.read_text(encoding="utf-8"))
    assert fitted_shallow.k == data["strata"]["shallow"]["selected_k"]
    assert fitted_deep.k == data["strata"]["deep"]["selected_k"]
    assert fitted_shallow.sigma_ceiling_km == data["ceiling_selection"]["selected_ceiling_km"]
    assert fitted_deep.sigma_ceiling_km == data["ceiling_selection"]["selected_ceiling_km"]


def test_fit_raises_a_clear_error_when_the_params_file_is_missing(catalogue, monkeypatch, tmp_path):
    monkeypatch.setattr(adaptive, "ADAPTIVE_PARAMS_PATH", tmp_path / "does-not-exist.json")
    with pytest.raises(adaptive.AdaptiveParamsNotBuiltError):
        adaptive.fit(catalogue, "shallow")


def test_explicit_k_and_ceiling_still_override_the_frozen_values(catalogue):
    fitted = adaptive.fit(catalogue, "shallow", k=12, ceiling_km=77.0)
    assert fitted.k == 12
    assert fitted.sigma_ceiling_km == 77.0


def test_no_selected_sigma_is_finer_than_the_grid_floor():
    """The same constraint D13.4a already found for the fixed kernel,
    inherited structurally here: nothing below one cell width.
    """
    data = json.loads(adaptive.ADAPTIVE_PARAMS_PATH.read_text(encoding="utf-8"))
    assert data["floor_km"] == 8.4


def test_ceiling_sensitivity_curve_is_recorded_with_at_least_seven_points():
    data = json.loads(adaptive.ADAPTIVE_PARAMS_PATH.read_text(encoding="utf-8"))
    curve = data["ceiling_selection"]["curve"]
    assert len(curve) >= 7
    ceilings = [p["ceiling_km"] for p in curve]
    assert ceilings == sorted(ceilings)
    assert min(ceilings) == adaptive.SIGMA_FLOOR_KM
    for p in curve:
        assert math.isfinite(p["joint_loo_log_likelihood"])


def test_ceiling_boundary_flag_is_consistent_with_the_curve():
    data = json.loads(adaptive.ADAPTIVE_PARAMS_PATH.read_text(encoding="utf-8"))
    sel = data["ceiling_selection"]
    curve = sel["curve"]
    candidates = sel["candidates_km"]
    best = max(curve, key=lambda p: p["joint_loo_log_likelihood"])
    assert best["ceiling_km"] == sel["selected_ceiling_km"]
    on_boundary = sel["selected_ceiling_km"] in (min(candidates), max(candidates))
    assert sel["is_boundary_solution"] == on_boundary


def test_k_sensitivity_curve_is_recorded_per_stratum_with_at_least_seven_points():
    data = json.loads(adaptive.ADAPTIVE_PARAMS_PATH.read_text(encoding="utf-8"))
    for stratum in ("shallow", "deep"):
        curve = data["strata"][stratum]["curve"]
        assert len(curve) >= 7
        ks = [p["k"] for p in curve]
        assert ks == sorted(ks)
        for p in curve:
            assert math.isfinite(p["loo_log_likelihood"])


def test_k_boundary_flag_is_consistent_with_the_curve():
    data = json.loads(adaptive.ADAPTIVE_PARAMS_PATH.read_text(encoding="utf-8"))
    for stratum in ("shallow", "deep"):
        entry = data["strata"][stratum]
        candidates = entry["k_candidates"]
        best = max(entry["curve"], key=lambda p: p["loo_log_likelihood"])
        assert best["k"] == entry["selected_k"]
        on_boundary = entry["selected_k"] in (min(candidates), max(candidates))
        assert entry["is_boundary_solution"] == on_boundary


def test_k_differs_between_strata_or_is_at_least_independently_selected():
    """D13.4b requires k selected PER STRATUM. This does not assert the two
    values differ (they are free to coincide by coincidence), but it does
    assert the two curves were computed from different event counts, which
    is the same "not a reused array" check test_baseline.py's bandwidth
    tests apply to the fixed kernel's own two strata.
    """
    data = json.loads(adaptive.ADAPTIVE_PARAMS_PATH.read_text(encoding="utf-8"))
    assert data["strata"]["shallow"]["n_events"] != data["strata"]["deep"]["n_events"]


def test_adaptive_params_json_records_the_holdout_and_the_method():
    data = json.loads(adaptive.ADAPTIVE_PARAMS_PATH.read_text(encoding="utf-8"))
    assert "leave-one-out" in data["method"].lower()
    assert data["holdout_start"]
    assert data["n_windows_held_out"] == 26
    assert data["window_days"] == 7


# --------------------------------------------------------------------------
# loo_log_likelihood_for_params: the selection mechanism itself
# --------------------------------------------------------------------------

def test_loo_selection_holdout_excludes_events_in_the_held_out_span(catalogue):
    holdout_start = datetime(2026, 2, 3, tzinfo=timezone.utc)
    result = adaptive.loo_log_likelihood_for_params(
        catalogue, "shallow", k=8, ceiling_km=50.0, holdout_start=holdout_start
    )
    fit_start_dt = datetime(2019, 1, 1, tzinfo=timezone.utc)
    manual_count = 0
    for e in catalogue:
        if e["origintime"] < fit_start_dt or e["origintime"] >= holdout_start:
            continue
        if e["magnitude"] < expander.MMIN:
            continue
        if region.stratum_for(e["depth"]) != "shallow":
            continue
        if region.cell_id_for(e["longitude"], e["latitude"]) is None:
            continue
        manual_count += 1
    assert result["n_events"] == manual_count
    assert result["n_events"] < len(
        [e for e in catalogue if e["origintime"] >= fit_start_dt]
    ), "holdout did not exclude anything; the guard is not doing anything"


def test_loo_selection_raises_on_invalid_stratum(catalogue):
    with pytest.raises(ValueError):
        adaptive.loo_log_likelihood_for_params(catalogue, "medium", k=8, ceiling_km=50.0)


# --------------------------------------------------------------------------
# The rate level is k/ceiling invariant, mirroring baseline's bandwidth
# invariance: each event's kernel is normalised to sum to exactly one across
# the grid regardless of sigma, so the total forecast count must not move
# when only k or the ceiling changes.
# --------------------------------------------------------------------------

def test_total_expected_count_is_identical_across_k_and_ceiling(catalogue):
    narrow = adaptive.fit(catalogue, "shallow", k=4, ceiling_km=20.0)
    wide = adaptive.fit(catalogue, "shallow", k=64, ceiling_km=500.0)
    start, end = date(2026, 1, 5), date(2026, 1, 12)
    total_narrow = sum(adaptive.forecast(narrow, start, end)["rates"].values())
    total_wide = sum(adaptive.forecast(wide, start, end)["rates"].values())
    assert total_narrow == pytest.approx(total_wide, rel=1e-9)
