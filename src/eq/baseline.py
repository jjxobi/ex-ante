"""The time-invariant smoothed seismicity baseline.

This is the benchmark every later model in this project is measured against,
and it ships before anything clever, per the design spec section 4.4. A model
that cannot beat a constant Poisson rate per cell has demonstrated nothing.
Simplicity here is the point, not a shortcut taken under time pressure.

The model. One rate per grid cell, constant in time, fitted separately per
depth stratum (D3). The rate comes from smoothed historical event counts: raw
counts alone would leave any 0.1 degree cell with no historical event at
exactly zero, and a zero-rate cell hands the Poisson log likelihood negative
infinity the first time an event ever lands there. Smoothing spreads each
event's contribution across its neighbourhood so every cell in the frozen
grid, including ones with no history at all, ends up with a positive rate.

Fitting window. D1 records a defect: the region was qualified for
completeness on 2021-2026, but two of the 41 retained cells were not complete
that far back historically. The region itself is frozen and cannot be
touched, so the repair lives here, in the model layer, per the design spec's
"Fitting window" section. 2019 is the earliest year at which every retained
cell is complete, so this baseline fits on events from 2019-01-01 onward
only, uniformly across all cells rather than with per-cell exposure
machinery. That costs real data (2019-2026 supplies roughly a fifth of the
events 2005-2026 would), and the cost is accepted rather than hidden: a
per-cell exposure correction is the documented next model, registered
separately so what truncation costs is a measured, public number rather than
a footnote.

Declustering. Per D8, this fits on the full undeclustered catalogue,
aftershocks included, because the scored target is every catalogue event
above threshold, not a background rate with aftershocks removed. The
resulting rate is not a "background" rate and is not described as one.

The smoothing kernel. A Gaussian kernel in planar kilometre distance (a local
flat-earth approximation, accurate at this project's spatial scale; the local
longitude scale factor uses each event-cell pair's mean latitude rather than
one fixed reference latitude, since the region spans nine degrees of latitude
and a single reference would distort distances near the range's edges).

    Bandwidth. D13.4a records that this kernel originally used a correlation
    length of 30 km, chosen as "roughly the scale of a single crustal fault
    zone": a hand-picked bandwidth, the same defect D3 already rejected for
    the depth boundary and fixed with Silverman's rule. The kernel escaped
    that principle rather than being exempt from it, so D13.4a applies it:
    the bandwidth is now selected per stratum by leave-one-out Poisson
    log-likelihood cross validation over the fit period, with the events in
    every scored window structurally excluded from that selection (never
    merely by convention), and the result is frozen in
    KERNEL_BANDWIDTH_PATH with a recorded sensitivity curve, in the same
    style region/boundary.json records D3's. See
    scripts/measurements/kernel_bandwidth.py for the selection itself and
    `fit_kernel_bandwidth` below for the mechanism. HAND_PICKED_KERNEL_SIGMA_KM
    keeps the replaced 30 km value named, never as an operative default,
    purely so the number this project moved away from stays visible.

    Each event's Gaussian is normalised to sum to exactly one cell's worth of
    weight across the whole grid before being added in, so summing over
    events conserves the total event count by construction: no separate
    renormalising pass is needed, and none is done. A tiny floor
    (KERNEL_FLOOR, added to every raw weight before that per-event
    normalisation) guards against exp() underflowing to a hard 0.0 for a
    cell far outside the kernel's reach. Measured against the real fit
    catalogue this floor never actually binds: New Zealand's retained region
    is one contiguous corridor along the plate boundary, so the nearest
    event to any cell is always close enough that the unfloored Gaussian
    weight is already many orders of magnitude above it (the smallest
    observed raw contribution was of order 1e-7, against a floor of 1e-9).
    It stays in as insurance against a cell or a future region for which that
    is not true, which is exactly the failure mode this module is required
    to rule out rather than merely make unlikely.

The b-value. Aki-Utsu maximum likelihood with Utsu's delta-M/2 binning
correction, per D13.3, fit independently per stratum. D13.3's "from the
stratum's measured Mc upward" refers to the pooled Mc D2 measured on a
catalogue that goes down to M1.8/M2.2; this baseline only ever sees the
frozen M3.0-and-above snapshot, so M3.0 (D2's threshold, expander.MMIN) is
the effective floor of the data actually being fit, and is used as Mmin here.
The upper cutoff is D13.3's frozen M5.5.

Conservation and determinism. Expected counts for a window are the fitted
per-day rate times the window's duration in days, nothing more: the model is
time invariant, so the calendar position of a window never enters, only its
length. Event iteration is sorted explicitly (by origin time, then location,
then magnitude) before any array is built or any sum is taken, so the output
never depends on the order events happen to arrive from storage, matching the
determinism discipline D13.5 sets for the expander this module feeds.
"""

from __future__ import annotations

import json
import math
from dataclasses import dataclass
from datetime import date, datetime, timezone

import numpy as np

from eq import expander, paths, region

# --------------------------------------------------------------------------
# Frozen inputs, from DECISIONS.md and region.py
# --------------------------------------------------------------------------

# D1's committed grid hash. Checked before fitting or forecasting so a
# regeneration that silently changed the grid is refused rather than
# quietly fit against, per D1's hashing rule.
FROZEN_GRID_HASH = "14b2e0b854b5ae89771ad3346204e801f1f32580fd9a09481b9b6f6fe9cd4e44"

# Design spec section 4.4: the earliest year every retained region cell is
# complete, repairing the D1 defect at the model layer since the region
# itself is frozen.
FIT_START_DEFAULT = date(2019, 1, 1)

# D13.3: Aki-Utsu fitting range. Mmin is the catalogue's own floor (D2 / this
# is the same constant expander.py uses so the two never drift apart); Mmax
# is D13.3's frozen upper cutoff, above which two decades of data are too
# sparse to constrain the fit.
B_FIT_M_MIN = expander.MMIN
B_FIT_M_MAX = 5.5
B_FIT_DELTA_M = expander.BIN_WIDTH

STRATA = ("shallow", "deep")

# --------------------------------------------------------------------------
# The smoothing kernel. See the module docstring for the justification.
# --------------------------------------------------------------------------

KERNEL_FLOOR = 1e-9
KM_PER_DEGREE_LAT = 111.32

# The bandwidth this project moved away from per D13.4a: hand-picked as
# "roughly the scale of a single New Zealand crustal fault zone" rather than
# selected by a rule. Named and kept, but never used as an operative default
# below, purely so the replaced number stays visible to a reader or a test.
HAND_PICKED_KERNEL_SIGMA_KM = 30.0

# Committed artifact: the frozen, per-stratum kernel bandwidth and its
# sensitivity curve, in the same style region/boundary.json records D3's.
# Lives under region/ alongside the grid and depth boundary rather than in a
# new directory, because that is this project's established home for
# committed, derived, non-data numbers (see eq.paths.REGION_DIR); it is
# baseline.py's own frozen artifact, not part of the collection region
# itself.
KERNEL_BANDWIDTH_PATH = paths.REGION_DIR / "kernel_bandwidth.json"

# Sensitivity sweep candidates, in km. There is no closed-form rule to sweep
# a multiplier around here, unlike D3's Silverman bandwidth, because the
# whole point of this selection is that the optimum is found by search
# rather than computed from a formula. Spans almost two orders of magnitude
# so the curve shows the optimum sitting inside a real interior, not pinned
# to either edge of the search range (the same failure D3's rejected Otsu
# criterion suffered from a wide search range having no interior optimum).
KERNEL_BANDWIDTH_CANDIDATES_KM = (
    1.0, 2.0, 3.0, 6.0, 10.0, 15.0, 20.0, 25.0, 30.0, 40.0, 55.0, 75.0, 100.0, 130.0,
)


class KernelBandwidthNotBuiltError(RuntimeError):
    """Raised when the frozen kernel bandwidth is read before it has been
    selected and written by scripts/measurements/kernel_bandwidth.py.
    """


@dataclass(frozen=True)
class FittedBaseline:
    """A fitted time-invariant Poisson rate field for one depth stratum.

    rates_per_day is expected events per cell per day, covering every cell
    in the frozen grid, never a subset. Everything else here is provenance,
    kept so a report can state what was actually fit rather than what was
    intended.
    """

    stratum: str
    grid_hash: str
    b: float
    cell_ids: list[int]
    rates_per_day: dict[int, float]
    fit_start: datetime
    fit_end: datetime
    exposure_days: float
    n_events_used: int
    n_out_of_region: int
    earliest_event_used: datetime
    kernel_sigma_km: float
    kernel_floor: float


# --------------------------------------------------------------------------
# Small helpers
# --------------------------------------------------------------------------

def _as_utc_datetime(value: date | datetime) -> datetime:
    """Normalise a date or an already timezone-aware datetime to UTC.

    A naive datetime is refused rather than silently assumed to be UTC,
    because that silent assumption is exactly the class of defect D12
    documents for the origin_date timezone fault.
    """
    if isinstance(value, datetime):
        if value.tzinfo is None:
            raise ValueError(
                f"{value!r} has no timezone; window boundaries are UTC per D12 "
                f"and must say so explicitly"
            )
        return value.astimezone(timezone.utc)
    return datetime(value.year, value.month, value.day, tzinfo=timezone.utc)


def _sort_key(event: dict) -> tuple:
    """A total order over events, independent of arrival order from storage."""
    return (
        event["origintime"],
        event["longitude"],
        event["latitude"],
        event["depth"],
        event["magnitude"],
        event.get("publicid", ""),
    )


def fit_b_value(
    magnitudes: list[float],
    m_min: float = B_FIT_M_MIN,
    m_max: float = B_FIT_M_MAX,
    delta_m: float = B_FIT_DELTA_M,
) -> float:
    """Aki-Utsu maximum likelihood b, with Utsu's binning correction, per D13.3.

    b = log10(e) / (mean(M) - (Mmin - delta_m / 2))

    Least squares on the cumulative frequency-magnitude distribution is
    rejected in D13.3 because its points are not independent; this is the
    closed-form, deterministic alternative the literature and D13.3 both
    specify. The correction subtracts half a magnitude bin from Mmin (Utsu
    1965): without it, the estimator implicitly assumes events are recorded
    at infinite precision at exactly Mmin and above, which biases b high by
    treating the bin's lower half as unobserved. GeoNet magnitudes are not
    binned (D13.1), but the correction is applied anyway per D13.3's explicit
    instruction, using this project's bin width (D13.2/expander.BIN_WIDTH) as
    delta_m, which is the standard practical treatment in the literature.
    """
    sample = [m for m in magnitudes if m_min <= m < m_max]
    n = len(sample)
    if n < 2:
        raise ValueError(
            f"only {n} events fall in [{m_min}, {m_max}); cannot fit a b-value"
        )
    mean_m = sum(sample) / n
    denominator = mean_m - m_min + delta_m / 2
    if denominator <= 0:
        raise ValueError(
            "mean magnitude at or below the fitting floor after the binning "
            "correction; cannot fit a positive b"
        )
    return math.log10(math.e) / denominator


def _smoothed_counts(
    cell_lons: np.ndarray,
    cell_lats: np.ndarray,
    event_lons: np.ndarray,
    event_lats: np.ndarray,
    sigma_km: float,
    floor: float,
    chunk_size: int = 1000,
) -> np.ndarray:
    """Gaussian-kernel smoothed event counts on the fixed grid.

    Each event contributes a probability distribution across every cell,
    a Gaussian in planar km distance normalised to sum to exactly one across
    the grid, so summing those distributions over events conserves the total
    event count by construction rather than by a renormalising pass done
    afterwards. See the module docstring for the floor's purpose.

    Processed in chunks of events, not as one n_events by n_cells matrix, so
    peak memory stays bounded regardless of how many events a fit is given;
    the chunk boundary does not change the result, only how it accumulates.
    """
    n_cells = cell_lons.shape[0]
    totals = np.zeros(n_cells, dtype=np.float64)
    two_sigma_sq = 2.0 * sigma_km * sigma_km
    n_events = event_lons.shape[0]
    for start in range(0, n_events, chunk_size):
        lon_chunk = event_lons[start:start + chunk_size]
        lat_chunk = event_lats[start:start + chunk_size]
        mean_lat = (lat_chunk[:, None] + cell_lats[None, :]) / 2.0
        dx_km = (
            (lon_chunk[:, None] - cell_lons[None, :])
            * KM_PER_DEGREE_LAT
            * np.cos(np.radians(mean_lat))
        )
        dy_km = (lat_chunk[:, None] - cell_lats[None, :]) * KM_PER_DEGREE_LAT
        dist_sq = dx_km * dx_km + dy_km * dy_km
        weights = np.exp(-dist_sq / two_sigma_sq) + floor
        weights /= weights.sum(axis=1, keepdims=True)
        totals += weights.sum(axis=0)
    return totals


# --------------------------------------------------------------------------
# Kernel bandwidth selection: leave-one-out Poisson log-likelihood cross
# validation over the fit period, per D13.4a. See the module docstring's
# "Bandwidth" paragraph and scripts/measurements/kernel_bandwidth.py, which
# calls fit_kernel_bandwidth below across KERNEL_BANDWIDTH_CANDIDATES_KM and
# writes KERNEL_BANDWIDTH_PATH.
# --------------------------------------------------------------------------

def _load_kernel_bandwidth_km(stratum: str) -> float:
    """The frozen, per-stratum bandwidth selected by
    scripts/measurements/kernel_bandwidth.py, read from the committed file.
    Never recomputed here: this module's read side matches eq.region's
    read/build split exactly.
    """
    if not KERNEL_BANDWIDTH_PATH.exists():
        raise KernelBandwidthNotBuiltError(
            f"{KERNEL_BANDWIDTH_PATH} does not exist. Run "
            f"scripts/measurements/kernel_bandwidth.py to select and freeze "
            f"the kernel bandwidth before fitting, or pass kernel_sigma_km "
            f"explicitly."
        )
    data = json.loads(KERNEL_BANDWIDTH_PATH.read_text(encoding="utf-8"))
    try:
        return float(data["strata"][stratum]["selected_bandwidth_km"])
    except KeyError as exc:
        raise KernelBandwidthNotBuiltError(
            f"{KERNEL_BANDWIDTH_PATH} has no selected bandwidth for stratum "
            f"{stratum!r}"
        ) from exc


def _event_cell_indices(cell_ids: list[int], lons: np.ndarray, lats: np.ndarray) -> np.ndarray:
    """Each event's position as an index into cell_ids, via eq.region's own
    lookup rather than a reimplemented one. Raises if an event is not on the
    frozen grid: callers are expected to have already filtered to region.
    """
    index_of = {cid: i for i, cid in enumerate(cell_ids)}
    out = np.empty(len(lons), dtype=np.int64)
    for i in range(len(lons)):
        cid = region.cell_id_for(float(lons[i]), float(lats[i]))
        if cid is None or cid not in index_of:
            raise ValueError(
                "event is not on the frozen grid; filter to region before "
                "computing cell indices"
            )
        out[i] = index_of[cid]
    return out


def _loo_log_likelihood(
    cell_lons: np.ndarray,
    cell_lats: np.ndarray,
    event_lons: np.ndarray,
    event_lats: np.ndarray,
    event_cell_idx: np.ndarray,
    sigma_km: float,
    floor: float,
    chunk_size: int = 1000,
) -> float:
    """Leave-one-out Poisson log-likelihood of the smoothed rate field, at
    this bandwidth, over these events.

    A Poisson process's log-likelihood is sum_i log(lambda(x_i)) minus the
    integral of lambda over the observed region. Each event's kernel is
    normalised to sum to exactly one across the grid (see the module
    docstring), so summed over events the integral term equals the number of
    events, regardless of bandwidth: a constant that does not change which
    bandwidth maximises the expression, so it is dropped and this returns
    sum_i log(lambda_{-i}(cell(x_i))) alone, lambda_{-i} being the rate with
    event i's own contribution left out.

    The leave-one-out part is not optional. Without it, the criterion is
    maximised by shrinking the bandwidth toward zero: every event's kernel
    collapses onto its own cell, driving the (non-leave-one-out) density
    there to infinity, which selects nothing. Leaving each event out of its
    own evaluation removes that degenerate direction and is what makes this
    a real bandwidth-selection criterion.
    """
    n_cells = cell_lons.shape[0]
    totals = np.zeros(n_cells, dtype=np.float64)
    n_events = event_lons.shape[0]
    self_weights = np.zeros(n_events, dtype=np.float64)
    two_sigma_sq = 2.0 * sigma_km * sigma_km
    for start in range(0, n_events, chunk_size):
        lon_chunk = event_lons[start:start + chunk_size]
        lat_chunk = event_lats[start:start + chunk_size]
        idx_chunk = event_cell_idx[start:start + chunk_size]
        mean_lat = (lat_chunk[:, None] + cell_lats[None, :]) / 2.0
        dx_km = (
            (lon_chunk[:, None] - cell_lons[None, :])
            * KM_PER_DEGREE_LAT
            * np.cos(np.radians(mean_lat))
        )
        dy_km = (lat_chunk[:, None] - cell_lats[None, :]) * KM_PER_DEGREE_LAT
        dist_sq = dx_km * dx_km + dy_km * dy_km
        weights = np.exp(-dist_sq / two_sigma_sq) + floor
        weights /= weights.sum(axis=1, keepdims=True)
        totals += weights.sum(axis=0)
        rows = np.arange(len(idx_chunk))
        self_weights[start:start + chunk_size] = weights[rows, idx_chunk]

    loo_rate = totals[event_cell_idx] - self_weights
    if np.any(loo_rate <= 0):
        # Guards the selection criterion rather than letting it silently
        # produce -inf/NaN; not expected to bind given KERNEL_FLOOR and any
        # of this project's candidate bandwidths, per the module docstring's
        # discussion of the floor.
        loo_rate = np.clip(loo_rate, a_min=np.finfo(np.float64).tiny, a_max=None)
    return float(np.sum(np.log(loo_rate)))


def fit_kernel_bandwidth(
    events: list[dict],
    stratum: str,
    *,
    fit_start: date = FIT_START_DEFAULT,
    holdout_start: date | datetime | None = None,
    candidates_km: tuple[float, ...] = KERNEL_BANDWIDTH_CANDIDATES_KM,
    kernel_floor: float = KERNEL_FLOOR,
) -> dict:
    """Select the kernel bandwidth by leave-one-out Poisson log-likelihood
    cross validation, per D13.4a.

    events is the full unfiltered catalogue, filtered here exactly the way
    fit() filters it (fit_start, magnitude floor, stratum, region
    membership), so this selects a bandwidth for precisely the events fit()
    would use to build the rate field.

    holdout_start, if given, additionally excludes every event at or after
    that instant from the selection sum, even though fit() itself will still
    use them once a bandwidth is frozen. This is how D13.4a's "never on
    scored windows" is enforced: callers pass the start of the earliest
    scored window and that window's events (and every later one) are
    structurally absent from the sum a candidate bandwidth is scored on, not
    merely unused by convention. The rate level and the events fit() uses to
    produce it are untouched by this function; only the bandwidth choice is
    holdout-restricted.
    """
    region.assert_grid_hash(FROZEN_GRID_HASH)
    if stratum not in STRATA:
        raise ValueError(f"stratum must be one of {STRATA}, got {stratum!r}")

    fit_start_dt = _as_utc_datetime(fit_start)
    holdout_start_dt = _as_utc_datetime(holdout_start) if holdout_start is not None else None

    used: list[dict] = []
    for e in events:
        if e["origintime"] < fit_start_dt:
            continue
        if holdout_start_dt is not None and e["origintime"] >= holdout_start_dt:
            continue
        if e["magnitude"] < expander.MMIN:
            continue
        if region.stratum_for(e["depth"]) != stratum:
            continue
        if region.cell_id_for(e["longitude"], e["latitude"]) is None:
            continue
        used.append(e)

    if len(used) < 30:
        raise ValueError(
            f"only {len(used)} {stratum} events available for bandwidth "
            f"selection after excluding held-out scored windows; too few to "
            f"select a bandwidth reliably"
        )

    grid = region.load_grid()
    cell_ids = sorted(row["cell_id"] for row in grid)
    centers = {
        row["cell_id"]: (row["lon_deci"] / 10 + 0.05, row["lat_deci"] / 10 + 0.05)
        for row in grid
    }
    cell_lons = np.array([centers[c][0] for c in cell_ids], dtype=np.float64)
    cell_lats = np.array([centers[c][1] for c in cell_ids], dtype=np.float64)

    event_lons = np.array([e["longitude"] for e in used], dtype=np.float64)
    event_lats = np.array([e["latitude"] for e in used], dtype=np.float64)
    event_cell_idx = _event_cell_indices(cell_ids, event_lons, event_lats)

    curve = []
    coarse_best_bandwidth = None
    coarse_best_ll = -math.inf
    sorted_candidates = sorted(candidates_km)
    for h in sorted_candidates:
        ll = _loo_log_likelihood(
            cell_lons, cell_lats, event_lons, event_lats, event_cell_idx, h, kernel_floor
        )
        curve.append({"bandwidth_km": h, "loo_log_likelihood": ll})
        if ll > coarse_best_ll:
            coarse_best_ll = ll
            coarse_best_bandwidth = h

    # Refine past the coarse grid's own spacing. A coarse sweep can only ever
    # resolve the optimum to its own step size; this searches a fine grid
    # bounded by the coarse optimum's immediate neighbours (or the search
    # range's own edge, if the coarse optimum sits at the end of
    # candidates_km, which is itself worth surfacing rather than silently
    # extrapolating past the searched range) so the frozen value is not an
    # artifact of which round numbers happened to be in candidates_km. The
    # coarse curve above, not this fine one, is what gets recorded as the
    # sensitivity curve, in boundary.json's own style.
    coarse_index = sorted_candidates.index(coarse_best_bandwidth)
    refine_lo = sorted_candidates[max(coarse_index - 1, 0)]
    refine_hi = sorted_candidates[min(coarse_index + 1, len(sorted_candidates) - 1)]

    best_bandwidth = coarse_best_bandwidth
    best_ll = coarse_best_ll
    if refine_hi > refine_lo:
        for h in np.linspace(refine_lo, refine_hi, 37):
            h = float(h)
            ll = _loo_log_likelihood(
                cell_lons, cell_lats, event_lons, event_lats, event_cell_idx, h, kernel_floor
            )
            if ll > best_ll:
                best_ll = ll
                best_bandwidth = h

    return {
        "stratum": stratum,
        "n_events": len(used),
        "fit_start": fit_start_dt.isoformat(),
        "holdout_start": holdout_start_dt.isoformat() if holdout_start_dt else None,
        "selected_bandwidth_km": best_bandwidth,
        "selected_log_likelihood": best_ll,
        "coarse_best_bandwidth_km": coarse_best_bandwidth,
        "refined_search_range_km": [refine_lo, refine_hi],
        "sensitivity_curve": curve,
    }


# --------------------------------------------------------------------------
# fit and forecast
# --------------------------------------------------------------------------

def fit(
    events: list[dict],
    stratum: str,
    *,
    fit_start: date = FIT_START_DEFAULT,
    kernel_sigma_km: float | None = None,
    kernel_floor: float = KERNEL_FLOOR,
) -> FittedBaseline:
    """Fit a time-invariant Poisson rate per cell for one depth stratum.

    events is the full loaded catalogue (eq.storage.read_parquet output),
    unfiltered: this function does all the filtering. fit_start is applied
    with the same half-open, inclusive-lower convention D12 uses for
    forecast windows, so an event at exactly midnight on fit_start is used
    and nothing earlier is. Region membership and stratum assignment are
    both delegated to eq.region, never reimplemented here, per that module's
    contract.

    kernel_sigma_km defaults to None, meaning: read the frozen, per-stratum
    bandwidth selected by scripts/measurements/kernel_bandwidth.py from
    KERNEL_BANDWIDTH_PATH, per D13.4a. Pass an explicit value to override it,
    which existing callers (tests exercising a specific bandwidth, the
    selection script itself while it is still searching for the best value)
    do; production fitting never overrides it.
    """
    region.assert_grid_hash(FROZEN_GRID_HASH)
    if stratum not in STRATA:
        raise ValueError(f"stratum must be one of {STRATA}, got {stratum!r}")
    if kernel_sigma_km is None:
        kernel_sigma_km = _load_kernel_bandwidth_km(stratum)

    fit_start_dt = _as_utc_datetime(fit_start)
    fit_end_dt = max(e["origintime"] for e in events)
    if fit_end_dt <= fit_start_dt:
        raise ValueError(
            f"fit window has no exposure: catalogue ends at {fit_end_dt}, at or "
            f"before fit_start {fit_start_dt}"
        )
    exposure_days = (fit_end_dt - fit_start_dt).total_seconds() / 86400.0

    grid = region.load_grid()
    cell_ids = sorted(row["cell_id"] for row in grid)
    centers = {
        row["cell_id"]: (row["lon_deci"] / 10 + 0.05, row["lat_deci"] / 10 + 0.05)
        for row in grid
    }
    cell_lons = np.array([centers[c][0] for c in cell_ids], dtype=np.float64)
    cell_lats = np.array([centers[c][1] for c in cell_ids], dtype=np.float64)

    used: list[dict] = []
    n_out_of_region = 0
    for e in events:
        if e["origintime"] < fit_start_dt:
            continue
        if e["magnitude"] < expander.MMIN:
            continue
        if region.stratum_for(e["depth"]) != stratum:
            continue
        cell = region.cell_id_for(e["longitude"], e["latitude"])
        if cell is None:
            # D1: out-of-region events are counted, never silently dropped.
            # They cannot be assigned a cell in this grid, so they are
            # tallied here rather than fed into the fit.
            n_out_of_region += 1
            continue
        used.append(e)

    if not used:
        raise ValueError(
            f"no {stratum} events in region for the fit window starting "
            f"{fit_start}"
        )

    used.sort(key=_sort_key)
    earliest_used = used[0]["origintime"]
    assert earliest_used >= fit_start_dt, (
        f"fit used an event at {earliest_used}, before fit_start {fit_start_dt}"
    )

    event_lons = np.array([e["longitude"] for e in used], dtype=np.float64)
    event_lats = np.array([e["latitude"] for e in used], dtype=np.float64)
    smoothed = _smoothed_counts(
        cell_lons, cell_lats, event_lons, event_lats, kernel_sigma_km, kernel_floor
    )

    magnitudes = [e["magnitude"] for e in used]
    b = fit_b_value(magnitudes)

    rates_per_day = {
        cell_ids[i]: float(smoothed[i]) / exposure_days for i in range(len(cell_ids))
    }

    return FittedBaseline(
        stratum=stratum,
        grid_hash=region.grid_hash(),
        b=b,
        cell_ids=cell_ids,
        rates_per_day=rates_per_day,
        fit_start=fit_start_dt,
        fit_end=fit_end_dt,
        exposure_days=exposure_days,
        n_events_used=len(used),
        n_out_of_region=n_out_of_region,
        earliest_event_used=earliest_used,
        kernel_sigma_km=kernel_sigma_km,
        kernel_floor=kernel_floor,
    )


def forecast(
    fitted: FittedBaseline,
    window_start: date | datetime,
    window_end: date | datetime,
) -> dict:
    """Expected counts for a window: a separable dict for expander.expand().

    The model is time invariant, so a window's calendar position never
    enters; only its duration does. rates are the fitted per-day rate times
    the window length in days, expected COUNTS for that duration, matching
    the expander contract rather than a per-day rate.
    """
    region.assert_grid_hash(FROZEN_GRID_HASH)
    start = _as_utc_datetime(window_start)
    end = _as_utc_datetime(window_end)
    if end <= start:
        raise ValueError(f"window_end {end} must be after window_start {start}")
    duration_days = (end - start).total_seconds() / 86400.0

    cell_ids = sorted(fitted.rates_per_day)
    rates = {cid: fitted.rates_per_day[cid] * duration_days for cid in cell_ids}

    return {
        "grid_hash": fitted.grid_hash,
        "cell_ids": cell_ids,
        "b": fitted.b,
        "rates": rates,
    }
