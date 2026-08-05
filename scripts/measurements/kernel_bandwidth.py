"""Selects the baseline's spatial smoothing kernel bandwidth by leave-one-out
Poisson log-likelihood cross validation, per DECISIONS.md D13.4a.

D3 already rejected a hand-picked bandwidth for the depth boundary and fixed
it with Silverman's rule, recording a sensitivity curve alongside the frozen
value. The baseline's Gaussian smoothing kernel used a hand-picked 30 km
correlation length ("roughly the scale of a single New Zealand crustal fault
zone"), which is the same defect in a different parameter, and D13.4a applies
the same principle to it: optimise on the fit period only, record a
sensitivity curve in boundary.json's own style, and freeze the result rather
than leave it to judgement.

Never on scored windows. scripts/measurements/score_quantile_calibration.py
evaluates the WINDOW_DAYS * N_WINDOWS most recent days before the fixture's
own latest event; those constants are duplicated here (and checked against
that script's own values below) rather than imported, since that script is a
top-level program, not a library module. Every event at or after that
boundary is excluded from eq.baseline.fit_kernel_bandwidth's likelihood sum
before a single candidate bandwidth is scored, so no scored window's events
can move the selection structurally, not merely by discipline: see
fit_kernel_bandwidth's holdout_start parameter. eq.baseline.fit still uses
those events once a bandwidth is frozen, because D13.4a's decision is about
the bandwidth choice alone; the rate level, the fit period, and the 32.7%
over-prediction they produce are untouched by this script, deliberately.

Run once. The output (region/kernel_bandwidth.json) is committed; every
later fit() call reads it rather than ever calling this again, the same
build/read split eq.region uses for the grid and the depth boundary.
"""
import datetime as dt
import json
import sys

sys.path.insert(0, "src")
from eq import baseline, paths, storage  # noqa: E402

FIXTURE = paths.REPO_ROOT / "tests" / "fixtures" / "catalogue-fit-window.parquet"

# Matches scripts/measurements/score_quantile_calibration.py's WINDOW_DAYS
# and N_WINDOWS exactly. Duplicated rather than imported (see module
# docstring); if that script's constants ever move, this one has to move
# with it or the holdout boundary computed below stops covering exactly the
# windows being scored, which would silently reopen the leakage this script
# exists to close.
WINDOW_DAYS = 7
N_WINDOWS = 26


def main() -> int:
    catalogue = storage.read_parquet(FIXTURE)
    latest = max(e["origintime"] for e in catalogue)
    holdout_start = latest - dt.timedelta(days=WINDOW_DAYS * N_WINDOWS)

    print("=" * 72)
    print("Kernel bandwidth selection: leave-one-out Poisson log-likelihood")
    print("=" * 72)
    print(f"  fixture latest event : {latest}")
    print(f"  fit_start             : {baseline.FIT_START_DEFAULT}")
    print(f"  holdout_start         : {holdout_start}  (scored-window boundary,")
    print(f"                           {N_WINDOWS} x {WINDOW_DAYS}-day windows excluded)")
    print(f"  hand-picked bandwidth being replaced: "
          f"{baseline.HAND_PICKED_KERNEL_SIGMA_KM} km")
    print()

    strata_results = {}
    for stratum in baseline.STRATA:
        result = baseline.fit_kernel_bandwidth(
            catalogue, stratum, holdout_start=holdout_start
        )
        strata_results[stratum] = result
        print(f"--- {stratum} ---")
        print(f"  events used for selection (fit period, scored windows excluded): "
              f"{result['n_events']}")
        coarse_best = result["coarse_best_bandwidth_km"]
        for point in result["sensitivity_curve"]:
            marker = "   <== coarse optimum" if point["bandwidth_km"] == coarse_best else ""
            print(
                f"    {point['bandwidth_km']:7.1f} km   "
                f"LOO log-likelihood {point['loo_log_likelihood']:14.3f}{marker}"
            )
        lo, hi = result["refined_search_range_km"]
        print(f"  refined in [{lo}, {hi}] km around the coarse optimum")
        print(f"  selected bandwidth: {result['selected_bandwidth_km']:.3f} km "
              f"(log-likelihood {result['selected_log_likelihood']:.3f})")
        print()

    out = {
        "method": (
            "leave-one-out Poisson log-likelihood cross validation, per "
            "DECISIONS.md D13.4a"
        ),
        "candidates_km": list(baseline.KERNEL_BANDWIDTH_CANDIDATES_KM),
        "hand_picked_bandwidth_km": baseline.HAND_PICKED_KERNEL_SIGMA_KM,
        "holdout_start": holdout_start.isoformat(),
        "window_days": WINDOW_DAYS,
        "n_windows_held_out": N_WINDOWS,
        "strata": strata_results,
    }
    baseline.KERNEL_BANDWIDTH_PATH.parent.mkdir(parents=True, exist_ok=True)
    baseline.KERNEL_BANDWIDTH_PATH.write_text(
        json.dumps(out, indent=2) + "\n", encoding="utf-8"
    )
    print(f"wrote {baseline.KERNEL_BANDWIDTH_PATH}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
