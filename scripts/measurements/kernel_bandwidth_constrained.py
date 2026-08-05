"""Select the smoothing bandwidth under the feasibility constraint, and freeze it.

THE CONSTRAINT. The search is restricted to bandwidths at or above one grid cell
width. A smoothing kernel narrower than the grid it is discretised onto cannot
represent anything: the forecast is a rate per cell, so sub-cell structure has
nowhere to live. Below that width the leave-one-out likelihood stops measuring
predictive fit of a spatial density and starts measuring how a needle-shaped
kernel happens to sit relative to grid boundaries.

That is not a hand-picked constraint, it is the premise of the criterion. A rule
followed past the point where its premise holds is not rigour; it is the same
failure as the tolerance box rejected in D4a.

WHICH CELL WIDTH. A 0.1 degree cell is about 8.40 km east to west at New Zealand
latitudes and about 11.13 km north to south. For an isotropic Gaussian the
binding constraint is the SMALLER dimension, because a kernel wide enough to be
representable north to south can still be too narrow east to west. So 8.40 km
binds. Recorded explicitly, because someone re-deriving cell width from latitude
would get 11.13 km and have no way to tell whether 8.40 was a decision or an
error.

This is the same axis asymmetry that produced 39 float-binning disagreements on
the longitude axis and none on latitude.
"""

import datetime as dt
import json
import math
import sys

sys.path.insert(0, "src")
from eq import baseline, paths, storage  # noqa: E402

KM_PER_DEGREE = 111.32
REFERENCE_LAT = -41.0
CELL_EAST_WEST_KM = round(0.1 * KM_PER_DEGREE * math.cos(math.radians(REFERENCE_LAT)), 1)
CELL_NORTH_SOUTH_KM = round(0.1 * KM_PER_DEGREE, 2)

# Constrained to the feasible region, spanning it generously so the result is a
# genuine optimum over the whole representable range rather than over a stub.
CANDIDATES_KM = (
    CELL_EAST_WEST_KM, 9.0, 10.0, 12.0, 15.0, 20.0, 25.0, 30.0, 40.0, 55.0,
    75.0, 100.0, 130.0,
)

HOLDOUT_START = dt.datetime(2026, 2, 3, tzinfo=dt.timezone.utc)


def main() -> int:
    catalogue = storage.read_parquet(
        paths.REPO_ROOT / "tests" / "fixtures" / "catalogue-fit-window.parquet"
    )
    print(f"cell width east to west   : {CELL_EAST_WEST_KM} km  (binding)")
    print(f"cell width north to south : {CELL_NORTH_SOUTH_KM} km")
    print(f"candidates                : {CANDIDATES_KM}\n")

    strata = {}
    for stratum in ("shallow", "deep"):
        outcome = baseline.fit_kernel_bandwidth(
            catalogue,
            stratum,
            holdout_start=HOLDOUT_START,
            candidates_km=CANDIDATES_KM,
        )
        curve = [
            (p["bandwidth_km"], p["loo_log_likelihood"])
            for p in outcome["sensitivity_curve"]
        ]
        best_km, best_ll = max(curve, key=lambda kv: kv[1])
        is_boundary = best_km == CELL_EAST_WEST_KM
        monotonic = all(curve[i][1] >= curve[i + 1][1] for i in range(len(curve) - 1))

        print(f"{stratum}: n_events={outcome['n_events']:,}")
        for km, ll in curve:
            print(f"    {km:>6.1f} km   {ll:>12.1f}")
        print(f"  selected {best_km} km, "
              f"{'BOUNDARY solution' if is_boundary else 'interior optimum'}")
        print(f"  monotonically declining across the feasible region: {monotonic}\n")

        strata[stratum] = {
            "stratum": stratum,
            "n_events": outcome["n_events"],
            "selected_bandwidth_km": best_km,
            "selected_log_likelihood": best_ll,
            "is_boundary_solution": is_boundary,
            "monotonic_across_feasible_region": monotonic,
            "sensitivity_curve": [
                {"bandwidth_km": km, "loo_log_likelihood": ll} for km, ll in curve
            ],
        }

    payload = {
        "method": (
            "leave-one-out Poisson log-likelihood cross validation on fit-period "
            "events only, constrained to bandwidths at or above one grid cell "
            "width, per DECISIONS.md D13.4a"
        ),
        "constraint_km": CELL_EAST_WEST_KM,
        "constraint_rationale": (
            "a kernel narrower than the discretisation grid cannot represent "
            "sub-cell structure, so the likelihood below this width measures "
            "grid alignment rather than spatial scale"
        ),
        "cell_width_east_west_km": CELL_EAST_WEST_KM,
        "cell_width_north_south_km": CELL_NORTH_SOUTH_KM,
        "binding_dimension": "east to west, the smaller of the two",
        "rejected_unconstrained_optimum_km": {"shallow": 5.333, "deep": 7.083},
        "rejected_because": (
            "both sit below one cell width, in the region where the likelihood "
            "oscillates rather than declining smoothly"
        ),
        "hand_picked_bandwidth_km": 30.0,
        "holdout_start": HOLDOUT_START.isoformat(),
        "n_windows_held_out": 26,
        "window_days": 7,
        "candidates_km": list(CANDIDATES_KM),
        "strata": strata,
    }
    out = paths.REPO_ROOT / "region" / "kernel_bandwidth.json"
    out.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    print(f"written to {out.relative_to(paths.REPO_ROOT)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
