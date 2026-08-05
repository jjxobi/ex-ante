"""Does D1's rule, re-run on the raw catalogues, still reproduce the frozen grid?

This is the deep version of the check. It reads the cached measurement
catalogues, about 97 MB, re-applies D1's rule from scratch, and compares the
result against both the committed completeness table and the frozen grid.

It lives here rather than in the test suite because those catalogues are
gitignored and absent on any fresh clone, so a test depending on them fails in
continuous integration for a reason that has nothing to do with correctness.
That is exactly what happened, and the fix was to commit the small intermediate
(region/mc_by_cell.parquet, 2.4 KB) and keep the bulk dependency here.

The hermetic half of this check, that the frozen grid equals the retained cells
in that committed table, runs in tests/test_region.py on every machine.

If the cache is missing, run refetch_cache.py first. This script degrades to a
clear message and a non-zero exit rather than a confusing stack trace, because
absent bulk data is an expected state, not a fault.
"""

import csv
import math
import sys
from collections import Counter, defaultdict

sys.path.insert(0, "src")
from eq import paths, region, storage  # noqa: E402

REFERENCE_YEARS = range(2021, 2026)
MIN_EVENTS = 150
MC_CEILING = 2.6


def lon360(value) -> float:
    value = float(value)
    return value + 360 if value < 0 else value


def maximum_curvature_mc(magnitudes: list[float]) -> float | None:
    if len(magnitudes) < MIN_EVENTS:
        return None
    bins = Counter(round(m, 1) for m in magnitudes)
    return max(range(15, 50), key=lambda i: bins.get(i / 10, 0)) / 10


def main() -> int:
    cells: dict[tuple[int, int], list[float]] = defaultdict(list)
    total = 0
    missing = []
    for year in REFERENCE_YEARS:
        path = paths.MEASUREMENTS_DIR / f"cat_1.5_{year}_{year + 1}.csv"
        if not path.exists():
            missing.append(path.name)
            continue
        with path.open(encoding="utf-8") as handle:
            for row in csv.DictReader(handle):
                if not row["magnitude"]:
                    continue
                total += 1
                key = (
                    math.floor(lon360(row["longitude"])),
                    math.floor(float(row["latitude"])),
                )
                cells[key].append(float(row["magnitude"]))

    if missing:
        print("Cached catalogues absent, so the rule cannot be re-run:")
        for name in missing:
            print(f"  {name}")
        print("\nRun refetch_cache.py to rebuild them.")
        print("The hermetic half of this check still runs in tests/test_region.py.")
        return 2

    print(f"re-applied D1's rule to {total:,} events across {len(cells)} cells\n")

    recomputed = {key: maximum_curvature_mc(mags) for key, mags in cells.items()}
    retained = {
        key for key, mc in recomputed.items() if mc is not None and mc <= MC_CEILING
    }
    print(f"cells retained by re-running the rule : {len(retained)}")

    table = storage.read_parquet(paths.REPO_ROOT / "region" / "mc_by_cell.parquet")
    committed = {
        (row["region_cell_lon"], row["region_cell_lat"])
        for row in table
        if row["retained"]
    }
    print(f"cells retained in the committed table : {len(committed)}")

    grid_cells = {
        (row["region_cell_lon"], row["region_cell_lat"]) for row in region.load_grid()
    }
    print(f"cells present in the frozen grid      : {len(grid_cells)}")

    problems = 0
    if retained != committed:
        problems += 1
        print("\nMISMATCH: re-run rule versus committed table")
        print(f"  only in re-run : {sorted(retained - committed)}")
        print(f"  only in table  : {sorted(committed - retained)}")
    if committed != grid_cells:
        problems += 1
        print("\nMISMATCH: committed table versus frozen grid")
        print(f"  only in table : {sorted(committed - grid_cells)}")
        print(f"  only in grid  : {sorted(grid_cells - committed)}")

    for row in table:
        key = (row["region_cell_lon"], row["region_cell_lat"])
        if key in recomputed and recomputed[key] != row["mc"]:
            problems += 1
            print(
                f"\nMISMATCH on Mc for {key}: "
                f"re-run {recomputed[key]} versus committed {row['mc']}"
            )

    if problems:
        print(f"\n{problems} mismatch(es). The frozen grid and the rule disagree.")
        return 1

    print("\nAll three agree. The rule, the committed table and the grid match.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
