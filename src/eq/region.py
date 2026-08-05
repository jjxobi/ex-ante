"""The frozen collection region: spatial grid and depth boundary.

This is the artifact every later component asserts against. D1 defines the
collection region: a 1 degree cell is IN if it has at least MIN_EVENTS events
in the 2021-2026 reference window AND its measured magnitude of completeness
is MC_MAX or lower. D5 defines the forecast grid as every 0.1 degree cell
inside the retained 1 degree cells. D3 fits the depth boundary by kernel
density estimate on log10 depth, bandwidth fixed by Silverman's rule.

Two halves live here, deliberately kept in one module because they share the
region-cell computation and the coordinate conventions.

**The build side** (`retained_region_cells`, `build_grid_rows`,
`fit_depth_boundary`, `build_and_write`) regenerates `region/grid.parquet`,
`region/grid.sha256` and `region/boundary.json` from the cached measurement
catalogues. It is ported from `scripts/measurements/region_rule.py`, the
exploratory script that originally computed the region, without changing the
logic: the rule is frozen, and this module's job is to make it reproducible,
not to improve it. Run once; the outputs are committed and hashed.

**The read side** (`load_grid`, `grid_hash`, `assert_grid_hash`,
`cell_id_for`, `stratum_for`) is what every other component imports. It never
recomputes the region: it reads the committed, hashed files.

Cell assignment, per D13.2
---------------------------
A point belongs to cell `[x, x + 0.1)`, lower inclusive, upper exclusive, in
both longitude and latitude. Cell identifiers and bounds are integers,
expressed in decidegrees (tenths of a degree), never floats.

D13.2 measures a real hazard in the naive expression
`math.floor((x - origin) / dh)`: 39 of the 195 longitude cell edges in this
project's range bin differently than the exact decimal answer, because
subtracting two similarly sized floats and dividing by a value that is not
exactly representable in binary64 accumulates error. The fix it prescribes is
to convert to integer decidegrees at the boundary and do the rest in integer
arithmetic.

`tests/test_grid_binning.py` implements that fix as `integer_bin`, which
recovers the correct decidegree by *rounding* `x * 10` to the nearest integer.
That is correct, and is exactly what this module's tests check it against, but
only because every value that function is ever exercised on is itself, up to
representation noise, an exact multiple of 0.1: a grid edge. Round-to-nearest
is not usable as a general per-event binning rule: applied to an arbitrary
event coordinate it would assign anything in the upper half of a cell to the
next cell up, which is wrong for the lower-inclusive convention roughly half
the time, not a rare edge case.

`to_decidegree` below instead floors the scaled coordinate:
`math.floor(x * 10)`. This is measured, not assumed, to agree with
`integer_bin` on every one of the 39 adversarial longitude edges (see
`tests/test_region.py`), while also agreeing with plain floating point
`floor((x - origin) / dh)` on every one of half a million random interior
points swept during development. Multiplication by 10 is a single correctly
rounded IEEE 754 operation with no subtraction of similarly sized values, so
it does not carry the hazard the naive expression does, and unlike rounding it
preserves the floor semantics D13.2 and D12 both require.
"""

from __future__ import annotations

import csv
import hashlib
import json
import math
from collections import Counter, defaultdict
from pathlib import Path

from eq import paths, storage

# --------------------------------------------------------------------------
# Frozen constants, from DECISIONS.md
# --------------------------------------------------------------------------

# D1: the collection region rule.
MC_MAX = 2.6
MIN_EVENTS = 150

# D1's reference window is 2021-01-01 to 2026-01-01. The cached measurement
# catalogues are chunked by year, cat_1.5_2021_2022.csv .. cat_1.5_2025_2026.csv.
REGION_REFERENCE_YEARS = range(2021, 2026)

# D13.2's coordinate ranges, the bounding box the whole project measures against.
LON_MIN, LON_MAX = 163.6, 183.0
LAT_MIN, LAT_MAX = -49.2, -32.3

# D5: the forecast grid resolution.
CELL_SIZE_DEGREES = 0.1

# D1 / D5's expected results. Not tuning knobs: a self check that fires loudly
# if a regeneration ever produces a different answer, per the instruction not
# to adjust the rule to reach these numbers but to stop and report if they
# diverge.
EXPECTED_REGION_CELLS = 41
EXPECTED_GRID_CELLS = 4100

# D3: the depth boundary.
DEPTH_BOUNDARY_KM = 41
DEPTH_FIT_RANGES = ((2005, 2010), (2010, 2015), (2015, 2020), (2020, 2025), (2025, 2026))
DEPTH_LOG10_GRID = [1.0 + i * 0.002 for i in range(501)]  # 10 km to 100 km
SENSITIVITY_MULTIPLIERS = (0.7, 0.85, 1.0, 1.2, 1.5)

# Committed artifact paths.
GRID_PATH = paths.REGION_DIR / "grid.parquet"
HASH_PATH = paths.REGION_DIR / "grid.sha256"
BOUNDARY_PATH = paths.REGION_DIR / "boundary.json"

# The origin decidegree cell identifiers are built relative to, so that every
# identifier is a small positive integer rather than depending on the sign of
# latitude. Purely an encoding choice; it carries no scientific meaning.
_LAT_ORIGIN_DECI = round(LAT_MIN * 10)


class GridNotBuiltError(RuntimeError):
    """Raised when the frozen grid is read before it has been generated."""


class GridHashMismatchError(RuntimeError):
    """Raised when a caller's expected grid hash does not match the committed one."""


class RegionRuleDivergedError(RuntimeError):
    """Raised when regenerating the region no longer reproduces the frozen counts.

    Per the operating instructions for this component: if the rule no longer
    produces 41 region cells and 4,100 grid cells, that is a finding to report,
    not a bug to paper over by adjusting the rule. This exception exists so a
    regeneration fails loudly rather than silently freezing a different region.
    """


# --------------------------------------------------------------------------
# Coordinate conventions
# --------------------------------------------------------------------------

def lon360(longitude: float) -> float:
    """Normalise to the continuous [163.6, 183.0] convention.

    A negative longitude gets 360 added. This never wraps: New Zealand
    seismicity extends past longitude 180, and wrapping at the antimeridian
    would put a discontinuity through active crust, per DECISIONS.md D1.
    """
    return longitude + 360 if longitude < 0 else longitude


def to_decidegree(value: float) -> int:
    """Floor a coordinate to its integer decidegree, robustly.

    See the module docstring for why this floors the scaled value rather than
    rounding it, and how that choice was verified against the D13.2 hazard.
    """
    return math.floor(value * 10)


def one_degree_cell(longitude: float, latitude: float) -> tuple[int, int]:
    """The 1 degree cell (lon, lat) a point falls in, per D1's rule scale.

    Longitude is normalised through lon360 first; latitude needs no
    normalisation because this project's latitudes never cross a wrap point.
    """
    lon = lon360(longitude)
    return (to_decidegree(lon) // 10, to_decidegree(latitude) // 10)


def cell_id(lon_deci: int, lat_deci: int) -> int:
    """The integer identifier for the 0.1 degree cell at this decidegree corner.

    A deterministic, invertible encoding: lon_deci * 10_000 plus a small
    positive latitude offset. 10_000 comfortably exceeds the latitude span
    this project ever needs (LAT_MIN to LAT_MAX is 169 decidegrees), so the
    encoding never collides. It carries no meaning beyond uniqueness and
    reproducibility.
    """
    return lon_deci * 10_000 + (lat_deci - _LAT_ORIGIN_DECI)


def stratum_for(depth: float) -> str:
    """The depth stratum, per DECISIONS.md D3. Shallow is inclusive at 41 km."""
    return "shallow" if depth <= DEPTH_BOUNDARY_KM else "deep"


# --------------------------------------------------------------------------
# Build side: reproduces scripts/measurements/region_rule.py without changing
# its logic.
# --------------------------------------------------------------------------

def _load_region_rule_events(cache_dir: Path = paths.MEASUREMENTS_DIR) -> list[tuple[float, float, float, float]]:
    """Magnitude, longitude, latitude, depth over D1's reference window.

    Ported unchanged from region_rule.py's event loading. Longitude is
    normalised on load, matching the exploratory script.
    """
    rows: list[dict] = []
    for year in REGION_REFERENCE_YEARS:
        path = cache_dir / f"cat_1.5_{year}_{year + 1}.csv"
        with path.open(encoding="utf-8") as fh:
            rows += list(csv.DictReader(fh))
    return [
        (float(r["magnitude"]), lon360(float(r["longitude"])), float(r["latitude"]), float(r["depth"]))
        for r in rows
        if r["magnitude"] and r["depth"]
    ]


def _maximum_curvature_mc(magnitudes: list[float], minimum_events: int = MIN_EVENTS) -> float | None:
    """Maximum curvature Mc: the modal bin of the rounded, non-cumulative FMD.

    Ported unchanged from region_rule.py's mc(). None means unmeasurable for
    lack of data, which D1 treats as excluded, the conservative direction.
    """
    if len(magnitudes) < minimum_events:
        return None
    binned = Counter(round(m, 1) for m in magnitudes)
    best_tenth = max(range(15, 50), key=lambda i: binned.get(i / 10, 0))
    return best_tenth / 10


def compute_region_cells(cache_dir: Path = paths.MEASUREMENTS_DIR) -> dict[tuple[int, int], float | None]:
    """Measured Mc per 1 degree cell over the D1 reference window.

    None means the cell could not be measured. This is D1's rule, unchanged
    from region_rule.py, applied through this module's coordinate helpers.
    """
    events = _load_region_rule_events(cache_dir)
    cells: dict[tuple[int, int], list[float]] = defaultdict(list)
    for magnitude, longitude, latitude, _depth in events:
        cells[(to_decidegree(longitude) // 10, to_decidegree(latitude) // 10)].append(magnitude)
    return {cell: _maximum_curvature_mc(mags) for cell, mags in cells.items()}


def retained_region_cells(cache_dir: Path = paths.MEASUREMENTS_DIR) -> set[tuple[int, int]]:
    """The 1 degree cells that satisfy D1's rule: measurable and Mc <= MC_MAX."""
    measured = compute_region_cells(cache_dir)
    return {cell for cell, mc in measured.items() if mc is not None and mc <= MC_MAX}


def build_grid_rows(region_cells: set[tuple[int, int]]) -> list[dict]:
    """Every 0.1 degree cell inside the given 1 degree cells, per D5.

    Sorted by cell_id ascending, so the output (and therefore the hash) does
    not depend on set iteration order.
    """
    rows: list[dict] = []
    for lon_deg, lat_deg in region_cells:
        for i in range(10):
            for j in range(10):
                lon_deci = lon_deg * 10 + i
                lat_deci = lat_deg * 10 + j
                rows.append(
                    {
                        "cell_id": cell_id(lon_deci, lat_deci),
                        "lon_deci": lon_deci,
                        "lat_deci": lat_deci,
                        "region_cell_lon": lon_deg,
                        "region_cell_lat": lat_deg,
                    }
                )
    rows.sort(key=lambda r: r["cell_id"])
    return rows


def _load_free_depths(cache_dir: Path, region_cells: set[tuple[int, int]]) -> list[float]:
    """Free-depth (not operator assigned) events inside the region, D3's fitting set."""
    rows: list[dict] = []
    for start, end in DEPTH_FIT_RANGES:
        path = cache_dir / f"cat_3.0_{start}_{end}.csv"
        with path.open(encoding="utf-8") as fh:
            rows += list(csv.DictReader(fh))

    depths: list[float] = []
    for r in rows:
        if r["depthtype"] == "operator assigned" or not r["depth"]:
            continue
        depth = float(r["depth"])
        if depth <= 0:
            continue
        cell = (to_decidegree(lon360(float(r["longitude"]))) // 10, to_decidegree(float(r["latitude"])) // 10)
        if cell in region_cells:
            depths.append(depth)
    return depths


def silverman_bandwidth(log_depths: list[float]) -> float:
    """Silverman's rule of thumb bandwidth, so no bandwidth is chosen by hand."""
    n = len(log_depths)
    mean = sum(log_depths) / n
    sd = math.sqrt(sum((x - mean) ** 2 for x in log_depths) / (n - 1))
    sorted_logs = sorted(log_depths)
    q1, q3 = sorted_logs[n // 4], sorted_logs[3 * n // 4]
    iqr = q3 - q1
    return 0.9 * min(sd, iqr / 1.34) * n ** (-1 / 5)


def _gaussian_kde(grid: list[float], samples: list[float], bandwidth: float) -> list[float]:
    n = len(samples)
    norm = n * bandwidth * math.sqrt(2 * math.pi)
    return [
        sum(math.exp(-0.5 * ((g - x) / bandwidth) ** 2) for x in samples) / norm
        for g in grid
    ]


def _interior_local_minimum(grid: list[float], density: list[float]) -> tuple[float, float] | None:
    best = None
    for i in range(3, len(grid) - 3):
        if density[i] < density[i - 1] and density[i] < density[i + 1]:
            if best is None or density[i] < best[1]:
                best = (grid[i], density[i])
    return best


def fit_depth_boundary(
    cache_dir: Path = paths.MEASUREMENTS_DIR,
    region_cells: set[tuple[int, int]] | None = None,
) -> dict:
    """Fit the depth boundary per D3: KDE on log10 depth, Silverman bandwidth,
    boundary at the interior local minimum. Records the full sensitivity curve.
    """
    if region_cells is None:
        region_cells = retained_region_cells(cache_dir)

    depths = _load_free_depths(cache_dir, region_cells)
    log_depths = sorted(math.log10(d) for d in depths)

    bandwidth = silverman_bandwidth(log_depths)
    density = _gaussian_kde(DEPTH_LOG10_GRID, log_depths, bandwidth)
    minimum = _interior_local_minimum(DEPTH_LOG10_GRID, density)
    if minimum is None:
        raise RegionRuleDivergedError(
            "no interior local minimum in the depth density; D3's rule requires one"
        )
    fitted_boundary_km = 10 ** minimum[0]

    sensitivity_curve = []
    for multiplier in SENSITIVITY_MULTIPLIERS:
        swept_bandwidth = bandwidth * multiplier
        swept_density = _gaussian_kde(DEPTH_LOG10_GRID, log_depths, swept_bandwidth)
        swept_minimum = _interior_local_minimum(DEPTH_LOG10_GRID, swept_density)
        sensitivity_curve.append(
            {
                "bandwidth_multiplier": multiplier,
                "bandwidth": swept_bandwidth,
                "boundary_km": (10 ** swept_minimum[0]) if swept_minimum else None,
            }
        )

    return {
        "n_free_depth_events": len(depths),
        "silverman_bandwidth": bandwidth,
        "fitted_boundary_km": fitted_boundary_km,
        "frozen_boundary_km": DEPTH_BOUNDARY_KM,
        "sensitivity_curve": sensitivity_curve,
    }


def build_and_write(
    cache_dir: Path = paths.MEASUREMENTS_DIR,
    region_dir: Path = paths.REGION_DIR,
) -> dict:
    """Regenerate the frozen grid and depth boundary, and write and hash them.

    Run once. The outputs are committed; every later component reads them
    through the read side of this module rather than ever calling this again.
    """
    region_cells = retained_region_cells(cache_dir)
    if len(region_cells) != EXPECTED_REGION_CELLS:
        raise RegionRuleDivergedError(
            f"D1's rule now retains {len(region_cells)} one degree cells, not the "
            f"frozen {EXPECTED_REGION_CELLS}. The rule is frozen; this is a finding "
            f"to report, not adjusted to force a match."
        )

    grid_rows = build_grid_rows(region_cells)
    if len(grid_rows) != EXPECTED_GRID_CELLS:
        raise RegionRuleDivergedError(
            f"the grid now has {len(grid_rows)} cells, not the frozen "
            f"{EXPECTED_GRID_CELLS}."
        )

    region_dir.mkdir(parents=True, exist_ok=True)
    grid_path = region_dir / "grid.parquet"
    storage.write_parquet_atomic(grid_rows, grid_path)

    digest = hashlib.sha256(grid_path.read_bytes()).hexdigest()
    (region_dir / "grid.sha256").write_text(digest + "\n", encoding="utf-8")

    boundary = fit_depth_boundary(cache_dir, region_cells)
    (region_dir / "boundary.json").write_text(
        json.dumps(boundary, indent=2) + "\n", encoding="utf-8"
    )

    return {
        "region_cells": len(region_cells),
        "grid_cells": len(grid_rows),
        "grid_hash": digest,
        "boundary": boundary,
    }


# --------------------------------------------------------------------------
# Read side: what every other component uses.
# --------------------------------------------------------------------------

_grid_cache: list[dict] | None = None
_grid_index_cache: dict[tuple[int, int], int] | None = None


def load_grid(*, force_reload: bool = False) -> list[dict]:
    """The frozen grid: one row per 0.1 degree cell, cached after first read.

    Each row carries cell_id, lon_deci, lat_deci and the parent 1 degree cell,
    all integers. Raises GridNotBuiltError if region/grid.parquet is missing,
    rather than silently returning an empty grid.
    """
    global _grid_cache
    if _grid_cache is None or force_reload:
        if not GRID_PATH.exists():
            raise GridNotBuiltError(
                f"{GRID_PATH} does not exist. Run eq.region.build_and_write() to "
                f"generate the frozen grid before reading it."
            )
        _grid_cache = storage.read_parquet(GRID_PATH)
    return _grid_cache


def _grid_index() -> dict[tuple[int, int], int]:
    global _grid_index_cache
    if _grid_index_cache is None:
        _grid_index_cache = {
            (row["lon_deci"], row["lat_deci"]): row["cell_id"] for row in load_grid()
        }
    return _grid_index_cache


def grid_hash() -> str:
    """The SHA-256 of the frozen grid, read from the committed file, not recomputed."""
    if not HASH_PATH.exists():
        raise GridNotBuiltError(
            f"{HASH_PATH} does not exist. Run eq.region.build_and_write() first."
        )
    return HASH_PATH.read_text(encoding="utf-8").strip()


def assert_grid_hash(expected: str) -> None:
    """Raise if the committed grid hash does not match what a caller expects.

    Every component that depends on the frozen grid calls this before running,
    per DECISIONS.md D1: a regeneration that changed the grid while still
    satisfying the rule fails this assertion rather than passing silently.
    """
    actual = grid_hash()
    if actual != expected:
        raise GridHashMismatchError(
            f"grid hash mismatch: expected {expected!r}, the committed "
            f"{HASH_PATH.name} is {actual!r}. Refusing to proceed against a grid "
            f"this caller was not built for."
        )


def cell_id_for(lon: float, lat: float) -> int | None:
    """The frozen grid cell a point falls in, or None if it is out of region.

    Longitude is normalised through lon360 first. Out-of-region points, per
    D1, are never silently dropped by a caller that checks for None; this
    function's only job is to say which cell, if any, a point belongs to.
    """
    lon_deci = to_decidegree(lon360(lon))
    lat_deci = to_decidegree(lat)
    return _grid_index().get((lon_deci, lat_deci))
