"""Scores a published forecast against what actually happened.

Every other module in this project produces a number. This module is the one
that decides whether the number was any good, by handing a dense forecast and
an observed catalogue to pyCSEP's Poisson consistency tests: N (did the total
count match), S (did the spatial pattern match), M (did the magnitude
distribution match) and L (did the joint space-magnitude likelihood match).

The interface point that matters. Our grid stores integer decidegrees and
assigns a point to a cell by integer arithmetic, per D13.2. pyCSEP wants a
CartesianGrid2D built from float origins and indexes its internal data array
by the order those origins were given in, not by any property of the
coordinates themselves. `tests/test_expander_pycsep.py` already proved the two
binning mechanisms agree on the 39 adversarial edges D13.2 found. What this
module adds is the one place that agreement gets consumed: the origins handed
to `CartesianGrid2D.from_origins` are built in exactly `dense.cell_ids` order,
and the dense values are reshaped into `(n_cells, n_mag_bins)` in that same
order, so row i of the forecast array is cell i of the region pyCSEP builds
from it, for every i. A silent permutation here would leave every total
correct (a sum does not care about order) while making every spatial test
score against the wrong cells. `test_cell_ordering_round_trips_through_to_csep_forecast`
in the test suite exists specifically to catch that failure mode, by checking
individual cells, not just the total.

Out-of-region events (D1) and events at or above the top magnitude bin (D13.4)
are both real hazards, not theoretical ones: pyCSEP's own `get_index_of` and
`get_magnitude_index` raise on either, so silently handing it unfiltered
events would crash the scorer instead of merely producing a wrong number. Both
are counted and reported on `ScoreResult` rather than dropped, matching the
treatment D1 and D13.4 already require upstream.

Determinism. The S, M and L tests are not closed form: pyCSEP draws simulated
catalogues to build the null distribution each quantile is read off. Handed no
seed, that draw differs between runs, which would silently break the
determinism discipline D13.5 sets for everything upstream of this module.
`CONSISTENCY_TEST_SEED` fixes it, so scoring the same forecast and catalogue
twice reproduces the same quantiles, not merely the same observed statistic.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timezone

import numpy as np
from csep.core import poisson_evaluations as pe
from csep.core.catalogs import CSEPCatalog
from csep.core.forecasts import GriddedForecast
from csep.core.regions import CartesianGrid2D
from csep.utils.time_utils import datetime_to_utc_epoch

from eq import expander, region

# Not a value from DECISIONS.md: pyCSEP's Monte Carlo tests need some seed to
# be reproducible, and this is the one this project fixes. Chosen once and
# then frozen by being committed, the same way every other arbitrary-but-fixed
# constant in this codebase is frozen by commit rather than by derivation.
CONSISTENCY_TEST_SEED = 20260804
NUM_SIMULATIONS = 1000

STRATA = ("shallow", "deep")


@dataclass(frozen=True)
class ConsistencyTestResult:
    """One pyCSEP consistency test's outcome, kept in this project's own type
    rather than passing pyCSEP's EvaluationResult through, so ScoreResult's
    shape does not depend on a dependency's internal representation.
    """

    name: str
    observed_statistic: float
    quantile: object


@dataclass(frozen=True)
class ScoreResult:
    """A scored window: the four consistency tests plus a full accounting of
    every event considered, in scope or not.

    n_events_used is the count that actually reached pyCSEP's catalogue.
    n_out_of_region and n_above_mmax are D1's and D13.4's separate,
    never-silently-dropped counts. n_below_mmin is not required by either
    decision (M3.0 is the target set's own definition, per D2, not a boundary
    hazard the way the top magnitude bin is), but is carried anyway because a
    caller handing this the full unfiltered catalogue, rather than one already
    restricted to the target set, deserves to see where every event went.
    """

    window_start: datetime
    window_end: datetime
    n_test: ConsistencyTestResult
    s_test: ConsistencyTestResult
    m_test: ConsistencyTestResult
    l_test: ConsistencyTestResult
    expected_count: float
    n_events_used: int
    n_out_of_region: int
    n_above_mmax: int
    n_below_mmin: int


@dataclass(frozen=True)
class _FilteredEvents:
    used: list[dict]
    n_out_of_region: int
    n_above_mmax: int
    n_below_mmin: int


def _as_utc_datetime(value: date | datetime) -> datetime:
    """Normalise a date or an already timezone-aware datetime to UTC.

    Duplicated in miniature from eq.baseline rather than imported from it:
    this module has no other reason to depend on the baseline model, only on
    the forecast and catalogue shapes it produces, and a naive datetime is
    refused here for the same reason D12 refuses one there.
    """
    if isinstance(value, datetime):
        if value.tzinfo is None:
            raise ValueError(
                f"{value!r} has no timezone; window boundaries are UTC per D12 "
                f"and must say so explicitly"
            )
        return value.astimezone(timezone.utc)
    return datetime(value.year, value.month, value.day, tzinfo=timezone.utc)


_grid_lookup_cache: dict[int, tuple[int, int]] | None = None


def _grid_lookup() -> dict[int, tuple[int, int]]:
    """cell_id -> (lon_deci, lat_deci), cached the same way eq.region caches
    the grid itself: this module never recomputes the grid, only reads it.
    """
    global _grid_lookup_cache
    if _grid_lookup_cache is None:
        _grid_lookup_cache = {
            row["cell_id"]: (row["lon_deci"], row["lat_deci"]) for row in region.load_grid()
        }
    return _grid_lookup_cache


# --------------------------------------------------------------------------
# Conversion to pyCSEP types
# --------------------------------------------------------------------------

def to_csep_forecast(
    dense: expander.DenseForecast,
    window_start: date | datetime,
    window_end: date | datetime,
    *,
    name: str = "eq-forecast",
) -> GriddedForecast:
    """A DenseForecast, as a pyCSEP GriddedForecast over the frozen grid.

    The origins handed to CartesianGrid2D are built in dense.cell_ids order,
    and the values are reshaped into (n_cells, n_mag_bins) in that same order,
    so row i of the pyCSEP data array is cell dense.cell_ids[i], for every i.
    See the module docstring for why that correspondence is the one hazard
    this function exists to get right.
    """
    lookup = _grid_lookup()
    missing = [cid for cid in dense.cell_ids if cid not in lookup]
    if missing:
        raise ValueError(
            f"{len(missing)} cell ids in this dense forecast are not in the "
            f"frozen grid, e.g. {missing[:5]}. It was not built against the "
            f"grid this project reads."
        )

    origins = np.array(
        [[lookup[cid][0] / 10.0, lookup[cid][1] / 10.0] for cid in dense.cell_ids],
        dtype=np.float64,
    )
    csep_region = CartesianGrid2D.from_origins(origins, dh=region.CELL_SIZE_DEGREES)
    magnitudes = np.array([lo for lo, _hi in dense.bins], dtype=np.float64)

    n_cells = len(dense.cell_ids)
    n_bins = len(dense.bins)
    data = np.array(dense.values, dtype=np.float64).reshape(n_cells, n_bins)

    start_dt = _as_utc_datetime(window_start)
    end_dt = _as_utc_datetime(window_end)
    if end_dt <= start_dt:
        raise ValueError(f"window_end {end_dt} must be after window_start {start_dt}")

    return GriddedForecast(
        start_time=start_dt,
        end_time=end_dt,
        data=data,
        region=csep_region,
        magnitudes=magnitudes,
        name=name,
    )


def to_csep_catalogue(events: list[dict], *, name: str = "observed") -> CSEPCatalog:
    """Already-filtered event dicts, as a pyCSEP CSEPCatalog.

    A pure format conversion. Filtering against a forecast's region and
    magnitude range is score()'s and information_gain()'s job, not this
    function's, so a caller wanting a catalogue of raw events for some other
    purpose is not forced through the scoring filters to get one.
    """
    tuples = [
        (
            event.get("publicid", ""),
            datetime_to_utc_epoch(event["origintime"]),
            event["latitude"],
            event["longitude"],
            event["depth"],
            event["magnitude"],
        )
        for event in events
    ]
    return CSEPCatalog(data=tuples, name=name)


# --------------------------------------------------------------------------
# Filtering: the D1 / D13.4 accounting
# --------------------------------------------------------------------------

def _filter_events(
    events: list[dict],
    window_start: datetime,
    window_end: datetime,
    *,
    stratum: str | None = None,
) -> _FilteredEvents:
    """Restrict events to the window and the forecast's target set, counting
    every exclusion rather than dropping it silently.

    Filter order mirrors eq.baseline.fit's: window, then magnitude floor,
    then stratum, then region membership, then the magnitude ceiling. Region
    membership and the top-bin check are delegated to eq.region and
    eq.expander respectively, never reimplemented here.
    """
    used: list[dict] = []
    n_out_of_region = 0
    n_above_mmax = 0
    n_below_mmin = 0

    for event in events:
        if not (window_start <= event["origintime"] < window_end):
            continue
        if event["magnitude"] < expander.MMIN:
            n_below_mmin += 1
            continue
        if stratum is not None and region.stratum_for(event["depth"]) != stratum:
            continue
        cell = region.cell_id_for(event["longitude"], event["latitude"])
        if cell is None:
            # D1: counted and reported, never silently dropped.
            n_out_of_region += 1
            continue
        classification = expander.classify_magnitude(event["magnitude"])
        if not classification.in_range:
            # D13.4: counted and reported, never silently dropped or raised.
            n_above_mmax += 1
            continue
        used.append(event)

    return _FilteredEvents(
        used=used,
        n_out_of_region=n_out_of_region,
        n_above_mmax=n_above_mmax,
        n_below_mmin=n_below_mmin,
    )


def _check_stratum(stratum: str | None) -> None:
    if stratum is not None and stratum not in STRATA:
        raise ValueError(f"stratum must be one of {STRATA} or None, got {stratum!r}")


# --------------------------------------------------------------------------
# Scoring
# --------------------------------------------------------------------------

def score(
    dense: expander.DenseForecast,
    events: list[dict],
    window_start: date | datetime,
    window_end: date | datetime,
    *,
    stratum: str | None = None,
    num_simulations: int = NUM_SIMULATIONS,
    seed: int = CONSISTENCY_TEST_SEED,
) -> ScoreResult:
    """Score a dense forecast against the events observed in its window.

    events is the full, unfiltered catalogue (eq.storage.read_parquet
    output): this function does all the filtering, the same contract
    eq.baseline.fit uses. stratum restricts scoring to one depth stratum's
    events, per D3's registration rule that shallow and deep are always
    scored separately; leave it None to score against every event in region
    and in magnitude range regardless of depth.
    """
    _check_stratum(stratum)
    start_dt = _as_utc_datetime(window_start)
    end_dt = _as_utc_datetime(window_end)

    forecast = to_csep_forecast(dense, start_dt, end_dt)
    filtered = _filter_events(events, start_dt, end_dt, stratum=stratum)
    catalogue = to_csep_catalogue(filtered.used)
    catalogue.region = forecast.region

    n_result = pe.number_test(forecast, catalogue)
    s_result = pe.spatial_test(forecast, catalogue, num_simulations=num_simulations, seed=seed)
    m_result = pe.magnitude_test(forecast, catalogue, num_simulations=num_simulations, seed=seed)
    l_result = pe.likelihood_test(forecast, catalogue, num_simulations=num_simulations, seed=seed)

    return ScoreResult(
        window_start=start_dt,
        window_end=end_dt,
        n_test=ConsistencyTestResult(
            "N", float(n_result.observed_statistic), tuple(float(q) for q in n_result.quantile)
        ),
        s_test=ConsistencyTestResult("S", float(s_result.observed_statistic), float(s_result.quantile)),
        m_test=ConsistencyTestResult("M", float(m_result.observed_statistic), float(m_result.quantile)),
        l_test=ConsistencyTestResult("L", float(l_result.observed_statistic), float(l_result.quantile)),
        expected_count=float(forecast.event_count),
        n_events_used=len(filtered.used),
        n_out_of_region=filtered.n_out_of_region,
        n_above_mmax=filtered.n_above_mmax,
        n_below_mmin=filtered.n_below_mmin,
    )


def information_gain(
    dense_a: expander.DenseForecast,
    dense_b: expander.DenseForecast,
    events: list[dict],
    window_start: date | datetime,
    window_end: date | datetime,
    *,
    stratum: str | None = None,
    alpha: float = 0.05,
) -> float:
    """The Rhoades et al. (2011) paired-t-test information gain of forecast
    a over benchmark forecast b, evaluated against the same observed events.

    Positive means a outperformed b on this catalogue; the two forecasts and
    the catalogue must be built the same way score() builds them, or the
    comparison is not apples to apples, so this reuses to_csep_forecast,
    _filter_events and to_csep_catalogue rather than any bespoke path.
    """
    _check_stratum(stratum)
    start_dt = _as_utc_datetime(window_start)
    end_dt = _as_utc_datetime(window_end)

    forecast_a = to_csep_forecast(dense_a, start_dt, end_dt, name="a")
    forecast_b = to_csep_forecast(dense_b, start_dt, end_dt, name="b")
    filtered = _filter_events(events, start_dt, end_dt, stratum=stratum)
    catalogue = to_csep_catalogue(filtered.used)
    catalogue.region = forecast_a.region

    result = pe.paired_t_test(forecast_a, forecast_b, catalogue, alpha=alpha)
    return float(result.observed_statistic)
