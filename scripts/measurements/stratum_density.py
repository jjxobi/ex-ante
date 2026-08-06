"""How much sparser is the deep stratum, and what does that mean for k?

D13.4b owes an adaptive bandwidth model whose scale comes from the distance to
the k-th nearest neighbour. Choosing one k against the shallow stratum's density
and assuming it transfers is the same mistake as assuming a fixed bandwidth
transferred between strata, which is why the fixed one was fitted per stratum.

The question is not only whether k is optimal for each. It is whether k
neighbours EXIST. In a sparse stratum, exhaustion is not a corner case to
handle; it may be the normal condition.
"""

import datetime as dt
import math
import sys
from collections import Counter

sys.path.insert(0, "src")
from eq import paths, region, storage  # noqa: E402

FIT_START = dt.datetime(2019, 1, 1, tzinfo=dt.timezone.utc)
KM_PER_DEGREE = 111.32
CANDIDATE_K = (4, 8, 16, 32, 64)


def main() -> int:
    catalogue = storage.read_parquet(
        paths.REPO_ROOT / "tests" / "fixtures" / "catalogue-fit-window.parquet"
    )
    per_stratum = {"shallow": [], "deep": []}
    for event in catalogue:
        if event["origintime"] < FIT_START or event["magnitude"] < 3.0:
            continue
        cell = region.cell_id_for(event["longitude"], event["latitude"])
        if cell is None:
            continue
        per_stratum[region.stratum_for(event["depth"])].append((cell, event))

    print("=" * 68)
    print("Stratum size and how it spreads over the frozen grid")
    print("=" * 68)
    occupancy = {}
    for stratum, events in per_stratum.items():
        counts = Counter(cell for cell, _ in events)
        occupancy[stratum] = counts
        occupied = len(counts)
        print(f"\n{stratum}:")
        print(f"  events                  : {len(events):,}")
        print(f"  cells with any event    : {occupied:,} of 4,100")
        print(f"  mean events per occupied: {len(events)/occupied:.2f}")
        print(f"  median events per cell  : {sorted(counts.values())[occupied//2]}")
        singletons = sum(1 for n in counts.values() if n == 1)
        print(f"  cells holding exactly 1 : {singletons:,} ({100*singletons/occupied:.1f}%)")

    shallow_n = len(per_stratum["shallow"])
    deep_n = len(per_stratum["deep"])
    print()
    print(f"deep is {shallow_n/deep_n:.2f}x smaller than shallow")

    print()
    print("=" * 68)
    print("Would k neighbours exist? Per event, within its own stratum")
    print("=" * 68)
    print(f"{'k':>4}  {'shallow: events with < k others':>34}  {'deep':>28}")
    for k in CANDIDATE_K:
        row = f"{k:>4}  "
        for stratum in ("shallow", "deep"):
            total = len(per_stratum[stratum])
            short = 0 if total > k else total
            row += f"{('none' if short == 0 else str(short)):>34}  " if stratum == "shallow" else ""
        print(f"{k:>4}  shallow total {shallow_n:,}, deep total {deep_n:,}: "
              f"k is {'feasible' if k < deep_n else 'INFEASIBLE'} in both")

    print()
    print("=" * 68)
    print("The real question: local density, not global count")
    print("=" * 68)
    print("  A k-th nearest neighbour scale is set by how far you must travel to")
    print("  find k events. In a sparse stratum that distance is larger, so the")
    print("  same k produces a systematically broader kernel there.")
    print()
    for stratum, events in per_stratum.items():
        counts = occupancy[stratum]
        # Approximate mean nearest-neighbour spacing from occupied cell density.
        occupied = len(counts)
        # Region is 4,100 cells of about 8.4 by 11.13 km.
        area_km2 = 4100 * 8.4 * 11.13
        density = len(events) / area_km2
        spacing = 1.0 / math.sqrt(density) if density else float("inf")
        print(f"  {stratum:<8} density {density:.4f} events per km2, "
              f"typical spacing {spacing:.1f} km")
        for k in CANDIDATE_K:
            radius = math.sqrt(k / (math.pi * density)) if density else float("inf")
            floor_bound = " (below the 8.4 km floor)" if radius < 8.4 else ""
            print(f"      k={k:<3} implies a radius of about {radius:>6.1f} km{floor_bound}")
        print()

    print("=" * 68)
    print("What this means for D13.4b")
    print("=" * 68)
    print("  k must be selected per stratum, not once against the larger one.")
    print("  A k tuned on shallow density gives a broader kernel on deep, and")
    print("  the 8.4 km floor will bind at different k in each.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
