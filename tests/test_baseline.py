"""Tests for the time-invariant smoothed seismicity baseline.

Two kinds of test live here. Most fit against the real, committed snapshot
catalogue, because the acceptance criteria this module exists to satisfy are
stated in terms of the real data (b lands in a plausible range, weekly totals
land near D6's reference figures, conservation holds against the real fit
period). A few use small synthetic catalogues instead, where the point is to
check a specific mechanism in isolation: the b-value estimator against a
catalogue whose true b is known by construction, and the grid hash guard,
which should never depend on what the real catalogue happens to contain.

The real-data fixtures are module scoped. Fitting is the expensive part of
this module (a few seconds per stratum against the full catalogue), so it
runs once per stratum per test session rather than once per test.
"""

from __future__ import annotations

import math
import os
import random
import subprocess
import sys
from datetime import date, datetime, timezone

import pytest

from eq import baseline, expander, paths, region, storage

# --------------------------------------------------------------------------
# Fixtures against the real catalogue
# --------------------------------------------------------------------------

def _newest_snapshot() -> "os.PathLike":
    """The newest dated snapshot, never the lexically greatest filename.

    Per D4b's recorded implementation trap: a CI slice named catalogue-ci
    would sort after a real dated snapshot and silently select the wrong
    file. Filtering to the dated pattern before taking the max avoids that.
    """
    candidates = sorted(paths.SNAPSHOT_DIR.glob("catalogue-????-??-??.parquet"))
    if not candidates:
        pytest.skip("no snapshot catalogue present in data/snapshots")
    return candidates[-1]


@pytest.fixture(scope="module")
def catalogue() -> list[dict]:
    return storage.read_parquet(_newest_snapshot())


@pytest.fixture(scope="module")
def fitted_shallow(catalogue) -> baseline.FittedBaseline:
    return baseline.fit(catalogue, "shallow")


@pytest.fixture(scope="module")
def fitted_deep(catalogue) -> baseline.FittedBaseline:
    return baseline.fit(catalogue, "deep")


# ==========================================================================
# Grid hash: asserted before fitting or forecasting
# ==========================================================================

def test_fit_raises_on_a_wrong_grid_hash(catalogue, monkeypatch):
    monkeypatch.setattr(baseline, "FROZEN_GRID_HASH", "0" * 64)
    with pytest.raises(region.GridHashMismatchError):
        baseline.fit(catalogue, "shallow")


def test_forecast_raises_on_a_wrong_grid_hash(fitted_shallow, monkeypatch):
    monkeypatch.setattr(baseline, "FROZEN_GRID_HASH", "0" * 64)
    with pytest.raises(region.GridHashMismatchError):
        baseline.forecast(fitted_shallow, date(2026, 1, 1), date(2026, 1, 8))


def test_fitted_grid_hash_matches_the_committed_grid(fitted_shallow):
    assert fitted_shallow.grid_hash == region.grid_hash()
    assert fitted_shallow.grid_hash == baseline.FROZEN_GRID_HASH


# ==========================================================================
# Criterion 2: no event before the fit window's start
# ==========================================================================

def test_no_event_before_fit_start_is_used(fitted_shallow, fitted_deep):
    fit_start_dt = datetime(2019, 1, 1, tzinfo=timezone.utc)
    assert fitted_shallow.earliest_event_used >= fit_start_dt
    assert fitted_deep.earliest_event_used >= fit_start_dt


def test_an_event_before_fit_start_is_excluded():
    events = [
        {
            "publicid": "before",
            "origintime": datetime(2018, 12, 31, 23, 59, 59, tzinfo=timezone.utc),
            "longitude": 172.5,
            "latitude": -42.5,
            "magnitude": 5.0,
            "depth": 10.0,
        },
        {
            "publicid": "at-boundary",
            "origintime": datetime(2019, 1, 1, 0, 0, 0, tzinfo=timezone.utc),
            "longitude": 172.5,
            "latitude": -42.5,
            "magnitude": 4.0,
            "depth": 10.0,
        },
        {
            "publicid": "after",
            "origintime": datetime(2020, 1, 1, tzinfo=timezone.utc),
            "longitude": 172.6,
            "latitude": -42.6,
            "magnitude": 3.5,
            "depth": 10.0,
        },
    ]
    fitted = baseline.fit(events, "shallow", fit_start=date(2019, 1, 1))
    # D12's half-open convention: the event at exactly midnight on fit_start
    # is used, the one a second earlier is not.
    assert fitted.n_events_used == 2
    assert fitted.earliest_event_used == datetime(2019, 1, 1, tzinfo=timezone.utc)


# ==========================================================================
# The smoothing kernel: positivity everywhere
# ==========================================================================

def test_every_cell_has_a_positive_rate_shallow(fitted_shallow):
    assert len(fitted_shallow.rates_per_day) == 4100
    assert all(rate > 0.0 for rate in fitted_shallow.rates_per_day.values())


def test_every_cell_has_a_positive_rate_deep(fitted_deep):
    assert len(fitted_deep.rates_per_day) == 4100
    assert all(rate > 0.0 for rate in fitted_deep.rates_per_day.values())


def test_no_cell_has_a_rate_of_exactly_zero(fitted_shallow, fitted_deep):
    """The failure this kernel exists to prevent: a zero rate hands the
    Poisson log likelihood negative infinity the moment an event lands there.
    """
    assert 0.0 not in fitted_shallow.rates_per_day.values()
    assert 0.0 not in fitted_deep.rates_per_day.values()


def test_a_cell_with_no_historical_events_still_gets_a_positive_rate(
    catalogue, fitted_shallow
):
    """Finds an actual cell with zero raw shallow events in the fit window
    and confirms the smoothed rate there is still strictly positive: this is
    the whole reason the kernel exists rather than raw counts being used
    directly.
    """
    fit_start_dt = datetime(2019, 1, 1, tzinfo=timezone.utc)
    raw_counts: dict[int, int] = {cid: 0 for cid in fitted_shallow.cell_ids}
    for e in catalogue:
        if e["origintime"] < fit_start_dt:
            continue
        if e["magnitude"] < expander.MMIN:
            continue
        if region.stratum_for(e["depth"]) != "shallow":
            continue
        cell = region.cell_id_for(e["longitude"], e["latitude"])
        if cell is not None:
            raw_counts[cell] += 1

    empty_cells = [cid for cid, n in raw_counts.items() if n == 0]
    assert empty_cells, "expected at least one cell with zero raw shallow events"
    for cid in empty_cells:
        assert fitted_shallow.rates_per_day[cid] > 0.0


# ==========================================================================
# Criterion 6: conservation over the fit period
# ==========================================================================

def test_conservation_over_the_fit_period_shallow(fitted_shallow):
    separable = baseline.forecast(fitted_shallow, fitted_shallow.fit_start, fitted_shallow.fit_end)
    predicted_total = sum(separable["rates"].values())
    observed = fitted_shallow.n_events_used
    assert predicted_total == pytest.approx(observed, rel=0.01)


def test_conservation_over_the_fit_period_deep(fitted_deep):
    separable = baseline.forecast(fitted_deep, fitted_deep.fit_start, fitted_deep.fit_end)
    predicted_total = sum(separable["rates"].values())
    observed = fitted_deep.n_events_used
    assert predicted_total == pytest.approx(observed, rel=0.01)


# ==========================================================================
# Time invariance: only duration matters
# ==========================================================================

def test_doubling_window_duration_doubles_expected_counts(fitted_shallow):
    start = datetime(2026, 1, 5, tzinfo=timezone.utc)
    one_week = baseline.forecast(fitted_shallow, start, datetime(2026, 1, 12, tzinfo=timezone.utc))
    two_weeks = baseline.forecast(fitted_shallow, start, datetime(2026, 1, 19, tzinfo=timezone.utc))
    for cid in one_week["cell_ids"]:
        assert two_weeks["rates"][cid] == pytest.approx(2 * one_week["rates"][cid], rel=1e-9)
    assert sum(two_weeks["rates"].values()) == pytest.approx(
        2 * sum(one_week["rates"].values()), rel=1e-9
    )


def test_forecast_does_not_depend_on_the_windows_calendar_position(fitted_shallow):
    """Time invariance means only the duration matters, never when the
    window sits on the calendar."""
    a = baseline.forecast(fitted_shallow, date(2026, 1, 1), date(2026, 1, 8))
    b = baseline.forecast(fitted_shallow, date(2031, 6, 1), date(2031, 6, 8))
    assert a["rates"] == b["rates"]


# ==========================================================================
# Criteria 4 and 5: b-value range and weekly totals near D6's reference
# ==========================================================================

def test_b_values_land_in_the_plausible_range_for_new_zealand(fitted_shallow, fitted_deep):
    assert 0.8 <= fitted_shallow.b <= 1.3
    assert 0.8 <= fitted_deep.b <= 1.3


def test_weekly_totals_are_within_30_percent_of_d6(fitted_shallow, fitted_deep):
    start = date(2026, 1, 5)
    end = date(2026, 1, 12)
    shallow_week = sum(baseline.forecast(fitted_shallow, start, end)["rates"].values())
    deep_week = sum(baseline.forecast(fitted_deep, start, end)["rates"].values())
    assert shallow_week == pytest.approx(16.4, rel=0.30)
    assert deep_week == pytest.approx(8.6, rel=0.30)


# ==========================================================================
# Criterion 1: the expander consumes this unchanged
# ==========================================================================

def test_expander_produces_the_full_dense_forecast(fitted_shallow):
    separable = baseline.forecast(fitted_shallow, date(2026, 1, 5), date(2026, 1, 12))
    dense = expander.expand(separable, expected_grid_hash=baseline.FROZEN_GRID_HASH)
    assert len(dense.cell_ids) == 4100
    assert len(dense.bins) == 55
    assert len(dense.values) == 4100 * 55


def test_separable_dict_has_exactly_the_expander_contract_keys(fitted_shallow):
    separable = baseline.forecast(fitted_shallow, date(2026, 1, 5), date(2026, 1, 12))
    assert set(separable.keys()) == {"grid_hash", "cell_ids", "b", "rates"}
    assert isinstance(separable["grid_hash"], str)
    assert isinstance(separable["cell_ids"], list)
    assert isinstance(separable["b"], float)
    assert isinstance(separable["rates"], dict)


# ==========================================================================
# Determinism, per criterion 7 and D13.5's discipline
# ==========================================================================

def test_forecast_is_byte_identical_across_repeated_calls(fitted_shallow):
    a = expander.canonical_bytes(
        expander.expand(baseline.forecast(fitted_shallow, date(2026, 1, 5), date(2026, 1, 12)))
    )
    b = expander.canonical_bytes(
        expander.expand(baseline.forecast(fitted_shallow, date(2026, 1, 5), date(2026, 1, 12)))
    )
    assert a == b


# A small, self-contained fit-and-forecast script for the cross-process
# checks. It builds its own synthetic catalogue rather than importing this
# test module, per the same discipline test_expander.py uses: a subprocess
# that imports the test suite fails for reasons unrelated to the property
# under test. The synthetic events sit inside region cell (172, -43), a real
# cell in the frozen onshore South Island grid, confirmed against
# eq.region.cell_id_for during development.
SUBPROCESS_SCRIPT = """
import sys
sys.path.insert(0, "src")
import random
from datetime import datetime, timezone
from eq import baseline, expander

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

fitted = baseline.fit(events, "shallow")
separable = baseline.forecast(
    fitted,
    datetime(2026, 1, 5, tzinfo=timezone.utc),
    datetime(2026, 1, 12, tzinfo=timezone.utc),
)
dense = expander.expand(separable, expected_grid_hash=baseline.FROZEN_GRID_HASH)
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
# b-value estimator: recovery on a synthetic catalogue with known b
# ==========================================================================

def _synthetic_binned_magnitudes(b_true, n, recorded_min=3.0, delta_m=0.1, seed=7):
    """A magnitude sample whose true b is known by construction.

    The Aki-Utsu MLE with Utsu's binning correction assumes the recorded
    threshold is itself a bin centre: true continuous magnitudes are drawn
    from an unbounded Gutenberg-Richter law starting at recorded_min -
    delta_m / 2, then rounded to the nearest delta_m. Values that round up to
    recorded_min are the ones the correction exists to account for. Testing
    the corrected estimator against plain continuous (unrounded) samples
    checks the wrong thing: it would fault the correction for compensating a
    binning effect that was never introduced. See the fit_b_value docstring.
    """
    true_min = recorded_min - delta_m / 2
    rng = random.Random(seed)
    sample = []
    while len(sample) < n:
        u = rng.random()
        m = true_min - math.log10(1 - u) / b_true
        binned = round(m / delta_m) * delta_m
        if binned >= recorded_min - 1e-9:
            sample.append(round(binned, 10))
    return sample


@pytest.mark.parametrize("b_true", [0.8, 1.0, 1.2])
def test_b_value_recovered_within_0_05_on_synthetic_catalogue(b_true):
    magnitudes = _synthetic_binned_magnitudes(b_true, n=5000)
    estimated = baseline.fit_b_value(magnitudes, m_min=3.0, m_max=9.0, delta_m=0.1)
    assert estimated == pytest.approx(b_true, abs=0.05)


def test_fit_b_value_rejects_too_few_events():
    with pytest.raises(ValueError):
        baseline.fit_b_value([3.1], m_min=3.0, m_max=5.5)


# ==========================================================================
# Two strata, independently fit
# ==========================================================================

def test_strata_are_fit_independently_with_their_own_b(fitted_shallow, fitted_deep):
    assert fitted_shallow.stratum == "shallow"
    assert fitted_deep.stratum == "deep"
    # Not asserting a direction, since D3 only requires independence, not
    # that one stratum's b is higher; recorded here as evidence the two runs
    # produced genuinely different fits rather than one being a copy of the
    # other.
    assert fitted_shallow.b != fitted_deep.b
    assert fitted_shallow.n_events_used != fitted_deep.n_events_used


def test_invalid_stratum_is_rejected(catalogue):
    with pytest.raises(ValueError):
        baseline.fit(catalogue, "medium")
