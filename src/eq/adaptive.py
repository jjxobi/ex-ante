"""The adaptive-bandwidth smoothed seismicity model.

D13.4a froze a fixed Gaussian smoothing kernel for the baseline and found the
freeze made the spatial (S) test worse, not better: 46 percent of the 26 scored
weekly windows rejected at the 5 percent level against 5 percent expected. The
diagnosis in D13.4b is that a single bandwidth imposes exactly one spatial
scale on all of New Zealand, while real seismicity clusters at several. This
module is the registered remedy: the same Gaussian smoothing baseline.py uses,
with one change. Each event's kernel width is no longer one fixed number, it is
set from the distance to that event's own k-th nearest neighbour, so dense
regions (Marlborough, Bay of Plenty) draw a tight kernel and sparse regions
(the quiet southwest) draw a wide one.

Everything else is deliberately unchanged from baseline.py: the same fitting
window (2019-01-01 onward, repairing the D1 completeness defect at the model
layer), the same undeclustered catalogue (D8), the same per-event Gaussian
normalised to sum to exactly one across the grid (so conservation holds by
construction, independent of bandwidth), the same Aki-Utsu b-value estimator
per stratum (D13.3), and the same separable forecast contract expander.expand()
consumes. This module does not import eq.baseline, on purpose: D13.4b registers
the adaptive kernel as a SEPARATE model, scored on identical windows, never
folded into the baseline. Sharing code would recreate the coupling the
"separate model" framing exists to rule out, so the small amount of duplicated
scaffolding (the datetime guard, the sort key, the b-value estimator) is
duplicated in miniature here rather than imported, the same choice eq.score
already made for the same reason.

Two new bounds, both frozen by rule rather than by hand, per D13.4b:

    Floor: SIGMA_FLOOR_KM = 8.4, one grid cell width east to west, the smaller
    of the two cell dimensions at New Zealand latitudes and therefore binding
    for an isotropic kernel (see baseline.py's own derivation, reproduced here
    unchanged because it is a property of the grid, not of the kernel form). A
    kernel narrower than the grid it is discretised onto cannot represent
    anything: the forecast is a rate per cell, and sub-cell structure has
    nowhere to live. This is not searched for; D13.4a already searched for it
    on the fixed kernel and it is architecturally the same limit here, so it is
    enforced directly rather than re-discovered.

    Ceiling: SIGMA_CEILING_KM, selected by
    scripts/measurements/adaptive_bandwidth.py and frozen in
    ADAPTIVE_PARAMS_PATH. Without a ceiling, an isolated event's k-th nearest
    neighbour can sit a very long way off (41.6 percent of occupied deep cells
    hold exactly one event, per D13.4b's own measurement), so its kernel would
    grow without bound and smear rate across a large part of the region: a
    different way of failing the S-test than the one this model exists to fix.

k, the neighbour count, is selected PER STRATUM (deep is 1.85 times sparser
than shallow, so one k gives a systematically broader kernel there), by
leave-one-out Poisson log-likelihood cross validation over fit-period data
only, the same criterion and the same discipline baseline.py's bandwidth
selection uses, with the events in every scored window structurally excluded
before a single candidate is scored. See adaptive_bandwidth.fit_adaptive_params
and scripts/measurements/adaptive_bandwidth.py for the selection itself.

Neighbour search. Distances used to find the k-th nearest neighbour are planar
km, in a single local flat-earth projection per stratum (longitude scaled by
the cosine of the fit set's OWN mean latitude, not the pairwise mean latitude
baseline.py's grid-smoothing step uses). A single reference is required here
because cKDTree needs one fixed metric space to index into, unlike the
grid-smoothing step, which computes a fresh pairwise mean latitude for every
event-cell pair and has no such constraint. The approximation this introduces
is bounded and small: this project's latitude range is -49.2 to -32.3, so
cos(49.2) = 0.653 against cos(32.3) = 0.844, a spread of about 15 percent
around the mean-latitude reference at the range's extremes, on a search that
is only ever used to RANK neighbours by distance, not to compute a value that
is later summed or hashed. It does not touch the grid-smoothing step, which
keeps baseline.py's own exact pairwise convention unchanged.

scipy's cKDTree does the search. Its query() method does not raise when asked
for more neighbours than exist, or when a point's own index does not appear
among the neighbours returned for it (both measured directly against the
library in tests/test_adaptive.py, not assumed): it silently pads with
distance=inf and an out-of-bounds sentinel index in the first case, and can
silently omit a point's own index from its own neighbour list under an exact
multi-way distance tie in the second, because ties are broken by the tree's
internal traversal order, not by matching the query point back to itself. Both
are exactly the class of defect this project's operating instructions warn
about: a library call that returns without error while returning something
plausible and wrong, the same shape as pyCSEP's quantile of 1.0 on an empty
window (D7.1a). Neither silent case is allowed to reach a bandwidth here: see
_kth_neighbour_distances_km's docstring for how each is detected and handled
rather than propagated.
"""

from __future__ import annotations

import json
import math
from dataclasses import dataclass
from datetime import date, datetime, timezone

import numpy as np
from scipy.spatial import cKDTree

from eq import expander, paths, region

# --------------------------------------------------------------------------
# Frozen inputs, from DECISIONS.md and region.py. Duplicated from baseline.py
# rather than imported: see the module docstring for why the two models stay
# uncoupled.
# --------------------------------------------------------------------------

FROZEN_GRID_HASH = "14b2e0b854b5ae89771ad3346204e801f1f32580fd9a09481b9b6f6fe9cd4e44"

FIT_START_DEFAULT = date(2019, 1, 1)

B_FIT_M_MIN = expander.MMIN
B_FIT_M_MAX = 5.5
B_FIT_DELTA_M = expander.BIN_WIDTH

STRATA = ("shallow", "deep")

KM_PER_DEGREE_LAT = 111.32

# The additive numerical floor added to every raw Gaussian weight before
# per-event normalisation, guarding against exp() underflowing to a hard 0.0.
# Same value and same purpose as baseline.KERNEL_FLOOR; named separately (not
# imported) for the same reason every other frozen constant here is
# duplicated rather than shared.
KERNEL_FLOOR = 1e-9

# D13.4b: one grid cell width east to west, the smaller of the two cell
# dimensions and therefore binding for an isotropic kernel. Not searched for:
# it is the same architectural limit D13.4a already found on the fixed
# kernel, reproduced here because it is a property of the grid, not of the
# kernel form.
SIGMA_FLOOR_KM = 8.4

# Committed artifact: the frozen ceiling and per-stratum k, with sensitivity
# curves for both, in the same style region/boundary.json and
# region/kernel_bandwidth.json record their own selections. See
# scripts/measurements/adaptive_bandwidth.py.
ADAPTIVE_PARAMS_PATH = paths.REGION_DIR / "adaptive_bandwidth.json"


class AdaptiveParamsNotBuiltError(RuntimeError):
    """Raised when the frozen ceiling or k is read before
    scripts/measurements/adaptive_bandwidth.py has selected and written them.
    """


@dataclass(frozen=True)
class FittedAdaptive:
    """A fitted time-invariant Poisson rate field for one depth stratum,
    smoothed with a per-event adaptive Gaussian kernel.

    Mirrors baseline.FittedBaseline's shape exactly (rates_per_day covers
    every cell in the frozen grid, never a subset) so a caller can treat the
    two models identically; the extra fields (k, sigma_floor_km,
    sigma_ceiling_km, sigma_km summary stats) are this model's own
    provenance, not part of the shared contract.
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
    k: int
    sigma_floor_km: float
    sigma_ceiling_km: float
    kernel_floor: float
    sigma_km_min: float
    sigma_km_max: float


# --------------------------------------------------------------------------
# Small helpers, duplicated in miniature from baseline.py (see module
# docstring for why).
# --------------------------------------------------------------------------

def _as_utc_datetime(value: date | datetime) -> datetime:
    """Normalise a date or an already timezone-aware datetime to UTC.

    A naive datetime is refused rather than silently assumed to be UTC,
    matching D12's discipline and baseline.py's own guard.
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

    Identical mechanism to baseline.fit_b_value (the b-value estimator is not
    part of what this model changes), duplicated rather than imported per the
    module docstring's "separate model" discipline.
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


# --------------------------------------------------------------------------
# Neighbour search
# --------------------------------------------------------------------------

def _project_km(lons: np.ndarray, lats: np.ndarray, ref_lat: float) -> np.ndarray:
    """A single local flat-earth projection to planar km, for cKDTree.

    See the module docstring's "Neighbour search" paragraph for why one fixed
    reference latitude is used here, rather than the pairwise mean latitude
    the grid-smoothing step below uses.
    """
    x = lons * KM_PER_DEGREE_LAT * np.cos(np.radians(ref_lat))
    y = lats * KM_PER_DEGREE_LAT
    return np.column_stack([x, y])


def _kth_neighbour_distances_km(lons: np.ndarray, lats: np.ndarray, k: int) -> np.ndarray:
    """Distance in planar km from each point to its k-th nearest OTHER point.

    Two library behaviours are handled explicitly rather than trusted, both
    measured directly against scipy in tests/test_adaptive.py:

    1. cKDTree.query does not raise when asked for more neighbours than exist
       (k >= n): it silently pads the result with distance=inf and an
       out-of-bounds index equal to n. Propagating that would clip an inf
       distance straight to the ceiling, which happens to look like a
       plausible answer. This function raises ValueError instead, before any
       query is made, whenever there are not at least k+1 points (k others
       plus the point itself).

    2. A point's own index is not guaranteed to appear among the k+1 nearest
       neighbours returned for it. This only happens under an exact many-way
       distance tie (several points at identical coordinates), but it is
       measured to happen: with five points coincident at one location, two
       of those five do not see their own index in a k=2 query, because
       scipy's tie-breaking follows internal tree traversal order, not any
       correspondence to the query point's own identity. Naively dropping
       "the first returned neighbour" as self, which is safe only when self
       is always column zero, would silently exclude the WRONG point exactly
       in this case (also measured: a coincident duplicate can be returned
       ahead of self, in either column). This function locates and removes
       self by index equality instead, and widens the query (doubling k,
       capped at n) whenever self is not found or too few non-self entries
       remain, until every point is resolved against the exact k-th nearest
       OTHER point.

    A single local flat-earth projection is used (see _project_km): this
    function only ranks points by distance, so the small approximation it
    introduces at the range's latitude extremes never reaches a summed or
    hashed value.
    """
    n = lons.shape[0]
    if k < 1:
        raise ValueError(f"k must be a positive integer, got {k}")
    if n <= k:
        raise ValueError(
            f"only {n} points available; the {k}-th nearest neighbour needs "
            f"at least {k + 1} points (k others plus the point itself). "
            f"cKDTree does not raise on this itself: see this function's "
            f"docstring for what it does instead, which is not treated as a "
            f"real answer here."
        )

    ref_lat = float(np.mean(lats))
    points = _project_km(lons, lats, ref_lat)
    tree = cKDTree(points)

    result = np.full(n, np.nan, dtype=np.float64)
    unresolved = np.arange(n)
    query_k = min(k + 1, n)

    while unresolved.size:
        dist, idx = tree.query(points[unresolved], k=query_k)
        if query_k == 1:  # pragma: no cover - query_k >= 2 whenever n > k >= 1
            dist = dist[:, None]
            idx = idx[:, None]
        still_unresolved = []
        for row, global_i in enumerate(unresolved):
            row_idx = idx[row]
            row_dist = dist[row]
            self_pos = np.flatnonzero(row_idx == global_i)
            if self_pos.size == 0:
                still_unresolved.append(global_i)
                continue
            others = np.delete(row_dist, self_pos)
            if others.shape[0] < k:
                still_unresolved.append(global_i)
                continue
            result[global_i] = np.sort(others)[k - 1]
        if not still_unresolved:
            break
        if query_k >= n:
            # Unreachable in practice: at query_k == n every row spans the
            # full point set, so self (distance 0 to itself) is necessarily
            # among the results. Kept as a loud failure rather than an
            # infinite loop in case that invariant is ever violated.
            raise RuntimeError(
                f"could not resolve the {k}-th nearest neighbour for "
                f"{len(still_unresolved)} point(s) even querying the full "
                f"point set; this should be unreachable"
            )
        unresolved = np.array(still_unresolved, dtype=np.int64)
        query_k = min(query_k * 4, n)

    return result


def adaptive_sigma_km(
    lons: np.ndarray,
    lats: np.ndarray,
    k: int,
    floor_km: float = SIGMA_FLOOR_KM,
    ceiling_km: float | None = None,
) -> np.ndarray:
    """Per-point kernel width: distance to the k-th nearest other point,
    clipped to [floor_km, ceiling_km].

    ceiling_km has no default: a caller must decide it explicitly (production
    code reads the frozen one from ADAPTIVE_PARAMS_PATH via fit(); selection
    code sweeps candidates), so a forgotten ceiling can never silently become
    "no ceiling" by falling through to some large sentinel.
    """
    if ceiling_km is None:
        raise ValueError("ceiling_km must be given explicitly; there is no default")
    if ceiling_km < floor_km:
        raise ValueError(f"ceiling_km {ceiling_km} is below floor_km {floor_km}")
    raw = _kth_neighbour_distances_km(lons, lats, k)
    return np.clip(raw, floor_km, ceiling_km)


# --------------------------------------------------------------------------
# The smoothing kernel: identical to baseline.py's, except sigma varies per
# event rather than being one constant.
# --------------------------------------------------------------------------

def _smoothed_counts(
    cell_lons: np.ndarray,
    cell_lats: np.ndarray,
    event_lons: np.ndarray,
    event_lats: np.ndarray,
    sigmas_km: np.ndarray,
    floor: float,
    chunk_size: int = 1000,
) -> np.ndarray:
    """Gaussian-kernel smoothed event counts on the fixed grid, per-event sigma.

    Each event's Gaussian is normalised to sum to exactly one across the grid
    before being added in, exactly as baseline._smoothed_counts does, so
    summing over events conserves the total event count by construction
    regardless of how sigma varies from event to event. See baseline.py's
    module docstring for the floor's purpose; unchanged here.
    """
    n_cells = cell_lons.shape[0]
    totals = np.zeros(n_cells, dtype=np.float64)
    n_events = event_lons.shape[0]
    for start in range(0, n_events, chunk_size):
        lon_chunk = event_lons[start:start + chunk_size]
        lat_chunk = event_lats[start:start + chunk_size]
        sigma_chunk = sigmas_km[start:start + chunk_size]
        mean_lat = (lat_chunk[:, None] + cell_lats[None, :]) / 2.0
        dx_km = (
            (lon_chunk[:, None] - cell_lons[None, :])
            * KM_PER_DEGREE_LAT
            * np.cos(np.radians(mean_lat))
        )
        dy_km = (lat_chunk[:, None] - cell_lats[None, :]) * KM_PER_DEGREE_LAT
        dist_sq = dx_km * dx_km + dy_km * dy_km
        two_sigma_sq = 2.0 * sigma_chunk[:, None] * sigma_chunk[:, None]
        weights = np.exp(-dist_sq / two_sigma_sq) + floor
        weights /= weights.sum(axis=1, keepdims=True)
        totals += weights.sum(axis=0)
    return totals


def _loo_log_likelihood(
    cell_lons: np.ndarray,
    cell_lats: np.ndarray,
    event_lons: np.ndarray,
    event_lats: np.ndarray,
    event_cell_idx: np.ndarray,
    sigmas_km: np.ndarray,
    floor: float,
    chunk_size: int = 1000,
) -> float:
    """Leave-one-out Poisson log-likelihood of the smoothed rate field, at
    these per-event bandwidths, over these events.

    Identical mechanism to baseline._loo_log_likelihood (see its docstring
    for the derivation of why the leave-one-out term is not optional), with
    sigma read per event rather than being one shared value.
    """
    n_cells = cell_lons.shape[0]
    totals = np.zeros(n_cells, dtype=np.float64)
    n_events = event_lons.shape[0]
    self_weights = np.zeros(n_events, dtype=np.float64)
    for start in range(0, n_events, chunk_size):
        lon_chunk = event_lons[start:start + chunk_size]
        lat_chunk = event_lats[start:start + chunk_size]
        sigma_chunk = sigmas_km[start:start + chunk_size]
        idx_chunk = event_cell_idx[start:start + chunk_size]
        mean_lat = (lat_chunk[:, None] + cell_lats[None, :]) / 2.0
        dx_km = (
            (lon_chunk[:, None] - cell_lons[None, :])
            * KM_PER_DEGREE_LAT
            * np.cos(np.radians(mean_lat))
        )
        dy_km = (lat_chunk[:, None] - cell_lats[None, :]) * KM_PER_DEGREE_LAT
        dist_sq = dx_km * dx_km + dy_km * dy_km
        two_sigma_sq = 2.0 * sigma_chunk[:, None] * sigma_chunk[:, None]
        weights = np.exp(-dist_sq / two_sigma_sq) + floor
        weights /= weights.sum(axis=1, keepdims=True)
        totals += weights.sum(axis=0)
        rows = np.arange(len(idx_chunk))
        self_weights[start:start + chunk_size] = weights[rows, idx_chunk]

    loo_rate = totals[event_cell_idx] - self_weights
    if np.any(loo_rate <= 0):
        loo_rate = np.clip(loo_rate, a_min=np.finfo(np.float64).tiny, a_max=None)
    return float(np.sum(np.log(loo_rate)))


def _event_cell_indices(cell_ids: list[int], lons: np.ndarray, lats: np.ndarray) -> np.ndarray:
    """Each event's position as an index into cell_ids, via eq.region's own
    lookup. Raises if an event is not on the frozen grid.
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


def _grid_arrays() -> tuple[list[int], np.ndarray, np.ndarray]:
    grid = region.load_grid()
    cell_ids = sorted(row["cell_id"] for row in grid)
    centers = {
        row["cell_id"]: (row["lon_deci"] / 10 + 0.05, row["lat_deci"] / 10 + 0.05)
        for row in grid
    }
    cell_lons = np.array([centers[c][0] for c in cell_ids], dtype=np.float64)
    cell_lats = np.array([centers[c][1] for c in cell_ids], dtype=np.float64)
    return cell_ids, cell_lons, cell_lats


def _select_used_events(
    events: list[dict],
    stratum: str,
    fit_start_dt: datetime,
    holdout_start_dt: datetime | None,
) -> list[dict]:
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
    return used


# --------------------------------------------------------------------------
# Parameter selection: leave-one-out Poisson log-likelihood cross validation
# over fit-period data only, per D13.4b. See
# scripts/measurements/adaptive_bandwidth.py, which calls these across a
# candidate grid and writes ADAPTIVE_PARAMS_PATH.
# --------------------------------------------------------------------------

def loo_log_likelihood_for_params(
    events: list[dict],
    stratum: str,
    k: int,
    ceiling_km: float,
    *,
    fit_start: date = FIT_START_DEFAULT,
    holdout_start: date | datetime | None = None,
    floor_km: float = SIGMA_FLOOR_KM,
    kernel_floor: float = KERNEL_FLOOR,
) -> dict:
    """Leave-one-out Poisson log-likelihood at one (k, ceiling) candidate,
    per stratum, over fit-period data only.

    holdout_start, if given, excludes every event at or after that instant
    from the selection, structurally rather than by convention, matching
    baseline.fit_kernel_bandwidth's own holdout_start contract exactly (see
    its docstring). This is what makes D13.4b's "never on scored windows"
    real: the caller passes the earliest scored window's start and every
    later event is absent from the sum before a single candidate is scored.
    """
    region.assert_grid_hash(FROZEN_GRID_HASH)
    if stratum not in STRATA:
        raise ValueError(f"stratum must be one of {STRATA}, got {stratum!r}")

    fit_start_dt = _as_utc_datetime(fit_start)
    holdout_start_dt = _as_utc_datetime(holdout_start) if holdout_start is not None else None
    used = _select_used_events(events, stratum, fit_start_dt, holdout_start_dt)

    if len(used) < 30:
        raise ValueError(
            f"only {len(used)} {stratum} events available for adaptive "
            f"parameter selection after excluding held-out scored windows; "
            f"too few to select reliably"
        )

    cell_ids, cell_lons, cell_lats = _grid_arrays()
    event_lons = np.array([e["longitude"] for e in used], dtype=np.float64)
    event_lats = np.array([e["latitude"] for e in used], dtype=np.float64)
    event_cell_idx = _event_cell_indices(cell_ids, event_lons, event_lats)

    sigmas = adaptive_sigma_km(event_lons, event_lats, k, floor_km, ceiling_km)
    ll = _loo_log_likelihood(
        cell_lons, cell_lats, event_lons, event_lats, event_cell_idx, sigmas, kernel_floor
    )
    return {
        "stratum": stratum,
        "k": k,
        "ceiling_km": ceiling_km,
        "n_events": len(used),
        "loo_log_likelihood": ll,
        "sigma_km_min": float(sigmas.min()),
        "sigma_km_max": float(sigmas.max()),
    }


# --------------------------------------------------------------------------
# Read the frozen selection
# --------------------------------------------------------------------------

def _load_adaptive_params(stratum: str) -> tuple[int, float]:
    """The frozen, per-stratum k and the frozen (shared) ceiling, selected
    by scripts/measurements/adaptive_bandwidth.py, read from the committed
    file. Never recomputed here, matching baseline._load_kernel_bandwidth_km's
    own read/build split.
    """
    if not ADAPTIVE_PARAMS_PATH.exists():
        raise AdaptiveParamsNotBuiltError(
            f"{ADAPTIVE_PARAMS_PATH} does not exist. Run "
            f"scripts/measurements/adaptive_bandwidth.py to select and "
            f"freeze k and the ceiling before fitting, or pass k and "
            f"ceiling_km explicitly."
        )
    data = json.loads(ADAPTIVE_PARAMS_PATH.read_text(encoding="utf-8"))
    try:
        k = int(data["strata"][stratum]["selected_k"])
        ceiling_km = float(data["ceiling_selection"]["selected_ceiling_km"])
    except KeyError as exc:
        raise AdaptiveParamsNotBuiltError(
            f"{ADAPTIVE_PARAMS_PATH} has no selected k or ceiling for "
            f"stratum {stratum!r}"
        ) from exc
    return k, ceiling_km


# --------------------------------------------------------------------------
# fit and forecast: same contract as baseline.py's.
# --------------------------------------------------------------------------

def fit(
    events: list[dict],
    stratum: str,
    *,
    fit_start: date = FIT_START_DEFAULT,
    k: int | None = None,
    ceiling_km: float | None = None,
    floor_km: float = SIGMA_FLOOR_KM,
    kernel_floor: float = KERNEL_FLOOR,
) -> FittedAdaptive:
    """Fit a time-invariant Poisson rate per cell for one depth stratum,
    using the adaptive per-event Gaussian kernel.

    events is the full loaded catalogue, unfiltered: this function does all
    the filtering, identically to baseline.fit (fit_start, magnitude floor,
    stratum, region membership), so this fits on precisely the events
    baseline.fit would use for the same stratum. k and ceiling_km default to
    None, meaning: read the frozen, per-stratum k and the frozen (shared)
    ceiling from ADAPTIVE_PARAMS_PATH, per D13.4b. Pass explicit values to
    override, which tests and the selection script itself do; production
    fitting never overrides them.
    """
    region.assert_grid_hash(FROZEN_GRID_HASH)
    if stratum not in STRATA:
        raise ValueError(f"stratum must be one of {STRATA}, got {stratum!r}")
    if k is None or ceiling_km is None:
        frozen_k, frozen_ceiling = _load_adaptive_params(stratum)
        if k is None:
            k = frozen_k
        if ceiling_km is None:
            ceiling_km = frozen_ceiling

    fit_start_dt = _as_utc_datetime(fit_start)
    fit_end_dt = max(e["origintime"] for e in events)
    if fit_end_dt <= fit_start_dt:
        raise ValueError(
            f"fit window has no exposure: catalogue ends at {fit_end_dt}, at or "
            f"before fit_start {fit_start_dt}"
        )
    exposure_days = (fit_end_dt - fit_start_dt).total_seconds() / 86400.0

    cell_ids, cell_lons, cell_lats = _grid_arrays()

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
    sigmas = adaptive_sigma_km(event_lons, event_lats, k, floor_km, ceiling_km)
    smoothed = _smoothed_counts(
        cell_lons, cell_lats, event_lons, event_lats, sigmas, kernel_floor
    )

    magnitudes = [e["magnitude"] for e in used]
    b = fit_b_value(magnitudes)

    rates_per_day = {
        cell_ids[i]: float(smoothed[i]) / exposure_days for i in range(len(cell_ids))
    }

    return FittedAdaptive(
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
        k=k,
        sigma_floor_km=floor_km,
        sigma_ceiling_km=ceiling_km,
        kernel_floor=kernel_floor,
        sigma_km_min=float(sigmas.min()),
        sigma_km_max=float(sigmas.max()),
    )


def forecast(
    fitted: FittedAdaptive,
    window_start: date | datetime,
    window_end: date | datetime,
) -> dict:
    """Expected counts for a window: a separable dict for expander.expand().

    Identical contract and mechanism to baseline.forecast: the model is time
    invariant, so only the window's duration matters, never its calendar
    position.
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
