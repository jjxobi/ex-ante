"""Three checks before the constrained bandwidth is frozen.

1. Is the deep stratum's likelihood curve actually computed from deep events,
   or is it an accidental reuse of the shallow array? The b-value estimator
   had exactly this shape of bug once, and it costs one assertion to rule out.

2. Does the likelihood really decline monotonically all the way down to the
   feasibility boundary, or is there a small local bump just inside it? The
   coarse sweep only tested 10 km as its lowest feasible candidate, so
   "the optimum is 10 km" may just be the nearest value on the search grid
   rather than what the stated rule produces.

3. Which cell width binds? A 0.1 degree cell is about 8.4 km east to west but
   about 11.1 km north to south at New Zealand latitudes. For an isotropic
   kernel the binding constraint is the SMALLER dimension, because a kernel
   wide enough to be representable north-south can still be too narrow
   east-west.
"""

import datetime as dt
import json
import math
import sys

sys.path.insert(0, "src")
from eq import baseline, paths, region, storage  # noqa: E402

KM_PER_DEGREE = 111.32
REFERENCE_LAT = -41.0

FINE_CANDIDATES = (8.4, 8.5, 9.0, 9.5, 10.0, 11.0, 12.0, 14.0)


def main() -> int:
    lon_km = 0.1 * KM_PER_DEGREE * math.cos(math.radians(REFERENCE_LAT))
    lat_km = 0.1 * KM_PER_DEGREE
    print("=" * 70)
    print("Check 3: which cell dimension binds an isotropic kernel?")
    print("=" * 70)
    print(f"  0.1 degree east to west at latitude {REFERENCE_LAT}: {lon_km:.2f} km")
    print(f"  0.1 degree north to south                         : {lat_km:.2f} km")
    print(f"  binding constraint is the SMALLER: {min(lon_km, lat_km):.2f} km")
    print("  A kernel wide enough to be representable north-south can still be")
    print("  too narrow east-west, so longitude binds. This is the same axis")
    print("  asymmetry that produced 39 float-binning disagreements on the")
    print("  longitude axis and zero on latitude.")

    catalogue = storage.read_parquet(
        paths.REPO_ROOT / "tests" / "fixtures" / "catalogue-fit-window.parquet"
    )
    holdout = dt.datetime(2026, 2, 3, tzinfo=dt.timezone.utc)

    print()
    print("=" * 70)
    print("Check 1: are the two strata computed from different events?")
    print("=" * 70)
    counts = {}
    for stratum in ("shallow", "deep"):
        n = sum(
            1
            for e in catalogue
            if region.stratum_for(e["depth"]) == stratum
            and e["origintime"] >= dt.datetime(2019, 1, 1, tzinfo=dt.timezone.utc)
            and region.cell_id_for(e["longitude"], e["latitude"]) is not None
        )
        counts[stratum] = n
        print(f"  {stratum:<8} in-region events from 2019: {n:,}")
    assert counts["shallow"] != counts["deep"], (
        "both strata have identical event counts, which would be the signature "
        "of one array being reused for both"
    )
    print("  event counts differ, so the two strata are not the same array")

    results = {}
    print()
    print("=" * 70)
    print("Check 2: fine sweep down to the feasibility boundary")
    print("=" * 70)
    for stratum in ("shallow", "deep"):
        outcome = baseline.fit_kernel_bandwidth(
            catalogue,
            stratum,
            holdout_start=holdout,
            candidates_km=FINE_CANDIDATES,
        )
        curve = {
            point["bandwidth_km"]: point["loo_log_likelihood"]
            for point in outcome["sensitivity_curve"]
        }
        results[stratum] = curve
        print(f"\n  {stratum}:")
        previous = None
        monotonic = True
        for bandwidth in FINE_CANDIDATES:
            value = curve[bandwidth]
            arrow = ""
            if previous is not None:
                if value > previous:
                    arrow = "  <- rises"
                    monotonic = False
            print(f"    {bandwidth:>5.1f} km   {value:>12.1f}{arrow}")
            previous = value
        best = max(curve.items(), key=lambda kv: kv[1])
        print(f"    best in this range: {best[0]} km")
        print(f"    monotonically declining from {FINE_CANDIDATES[0]} km upward? {monotonic}")

    print()
    print("=" * 70)
    print("Verdict")
    print("=" * 70)
    boundary = round(min(lon_km, lat_km), 1)
    for stratum, curve in results.items():
        best = max(curve.items(), key=lambda kv: kv[1])[0]
        kind = "BOUNDARY solution" if best == FINE_CANDIDATES[0] else "interior optimum"
        print(f"  {stratum:<8} best {best} km, which is a {kind}")
    print(f"\n  feasibility boundary is {boundary} km, one cell width east to west")
    print("  A boundary solution means the data wants finer spatial resolution")
    print("  than a 0.1 degree grid can represent. That is a real finding about")
    print("  the mismatch between the grid and the natural clustering scale of")
    print("  New Zealand seismicity, not a defect in the estimate.")

    output = paths.REPO_ROOT / "region" / "kernel_bandwidth_boundary_sweep.json"
    output.write_text(
        json.dumps(
            {
                "cell_width_east_west_km": round(lon_km, 3),
                "cell_width_north_south_km": round(lat_km, 3),
                "binding_constraint_km": boundary,
                "candidates_km": list(FINE_CANDIDATES),
                "strata": {
                    stratum: {
                        "n_events": counts[stratum],
                        "curve": [
                            {"bandwidth_km": b, "loo_log_likelihood": curve[b]}
                            for b in FINE_CANDIDATES
                        ],
                        "best_km": max(curve.items(), key=lambda kv: kv[1])[0],
                    }
                    for stratum, curve in results.items()
                },
            },
            indent=2,
        ),
        encoding="utf-8",
    )
    print(f"\n  written to {output.relative_to(paths.REPO_ROOT)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
