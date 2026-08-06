"""Selects the adaptive model's ceiling and per-stratum k, per DECISIONS.md D13.4b.

D13.4b owes the adaptive kernel two new bounds beyond the floor it inherits
unchanged from the fixed kernel (8.4 km, one grid cell width east to west):
a ceiling, because an isolated event's k-th nearest neighbour can sit a long
way off and would otherwise smear its kernel across a large part of the
region (41.6 percent of occupied deep cells hold exactly one event), and a
per-stratum k, because deep is 1.85 times sparser than shallow and the same k
produces a systematically broader kernel there.

The two are entangled: the leave-one-out likelihood that selects k depends on
the ceiling already being fixed, and the likelihood that would select a
ceiling depends on which k is in use. This script resolves that by profiling
k out of the ceiling decision rather than guessing at one:

    Stage 1, the ceiling. For each candidate ceiling, and independently for
    each stratum, the best achievable leave-one-out log-likelihood over the
    full k grid (K_CANDIDATES) is recorded (max over k, "profiled out"). The
    two strata's profiled curves are summed to a single joint score per
    ceiling, since D13.4b's ceiling is one shared bound, not a per-stratum
    one (D13.4c and this project's later acceptance criteria both refer to
    "the ceiling", singular). The ceiling maximising that joint score is
    frozen. K_PROFILE_CANDIDATES is the SAME grid stage 2 uses, deliberately:
    an earlier version of this script profiled over a coarser 5-point grid
    (4, 8, 16, 32, 64) and both strata's profiled-best k landed on 4, the
    grid's own lower edge, at every ceiling. Stage 2's finer grid then found
    k=2 beats k=4 for shallow, which meant the coarse grid had excluded the
    actual winner throughout stage 1 and the ceiling it selected could not be
    trusted. Searching the identical grid in both stages closes that gap.

    Stage 2, k. With the ceiling now fixed, each stratum sweeps K_CANDIDATES
    on its own leave-one-out likelihood and freezes its own k independently,
    exactly as the fixed kernel's bandwidth was selected per stratum.

Never on scored windows. holdout_start matches
scripts/measurements/kernel_bandwidth.py and
scripts/measurements/score_quantile_calibration.py's WINDOW_DAYS * N_WINDOWS
exactly (duplicated rather than imported, for the same reason those two
scripts duplicate it between themselves: each is a top-level program, not a
library module). Every event at or after that boundary is excluded from both
stages' likelihood sums before a single candidate is scored, via
eq.adaptive.loo_log_likelihood_for_params's own holdout_start parameter,
structurally rather than by convention.

Run once. The output (region/adaptive_bandwidth.json) is committed; every
later eq.adaptive.fit() call reads it rather than ever calling this again,
the same build/read split eq.region and eq.baseline use for their own frozen
artifacts.
"""
import datetime as dt
import json
import sys
import time

sys.path.insert(0, "src")
from eq import adaptive, paths, storage  # noqa: E402

FIXTURE = paths.REPO_ROOT / "tests" / "fixtures" / "catalogue-fit-window.parquet"

# Matches score_quantile_calibration.py's WINDOW_DAYS and N_WINDOWS, and
# kernel_bandwidth.py's own copy of the same two constants.
WINDOW_DAYS = 7
N_WINDOWS = 26

# Stage 1 profiles k out of the ceiling decision. This was first run with a
# coarser 5-point grid (4, 8, 16, 32, 64, D13.4b's own worked table plus 64)
# and both strata's profiled-best k landed on 4, the grid's own lower edge,
# every single time regardless of ceiling. Stage 2's finer grid then found
# k=2 beats k=4 for shallow, meaning the coarse profile grid had been
# excluding the actual winner throughout stage 1: an interior-vs-boundary
# question about k cannot be answered on a grid that omits the candidate
# that wins. K_PROFILE_CANDIDATES is therefore set equal to K_CANDIDATES
# below, so both stages search literally the same k values and the ceiling
# selected in stage 1 cannot be an artifact of a coarser, mismatched grid.
K_PROFILE_CANDIDATES = None  # set to K_CANDIDATES below, after it is defined

# Spans from the floor (one grid cell width) up to roughly half the region's
# own diagonal extent (the collection region spans about 19.4 degrees of
# longitude and 16.9 of latitude, on the order of 1,600 to 1,900 km corner to
# corner), so a ceiling that wants to sit high has room to show it rather
# than being an artifact of a search range that stopped too early, the same
# discipline KERNEL_BANDWIDTH_CANDIDATES_KM and D3's Silverman sensitivity
# band both follow.
CEILING_CANDIDATES_KM = (
    8.4, 15.0, 20.0, 25.0, 30.0, 40.0, 55.0, 75.0, 100.0, 130.0, 175.0, 230.0,
    300.0, 400.0, 550.0, 750.0, 1000.0,
)

# k's own candidate grid, used by both stages (see the note on
# K_PROFILE_CANDIDATES above). Starts at 1, the smallest k can ever be, so a
# selection that wants to go lower still has nowhere left to go and a
# boundary finding here is a real one rather than a search-range artifact.
# Spans almost two orders of magnitude, matching KERNEL_BANDWIDTH_CANDIDATES_KM's
# and the ceiling grid's own span-the-optimum discipline.
K_CANDIDATES = (1, 2, 4, 6, 8, 12, 16, 24, 32, 48, 64, 96)
K_PROFILE_CANDIDATES = K_CANDIDATES


def _stage_one_ceiling(catalogue: list[dict], holdout_start: dt.datetime) -> dict:
    print("=" * 72)
    print("Stage 1: ceiling, profiling k out per D13.4b's shared-ceiling rule")
    print("=" * 72)

    per_stratum_curve: dict[str, dict[float, dict]] = {"shallow": {}, "deep": {}}
    for ceiling_km in CEILING_CANDIDATES_KM:
        for stratum in adaptive.STRATA:
            best = None
            for k in K_PROFILE_CANDIDATES:
                result = adaptive.loo_log_likelihood_for_params(
                    catalogue, stratum, k, ceiling_km, holdout_start=holdout_start
                )
                if best is None or result["loo_log_likelihood"] > best["loo_log_likelihood"]:
                    best = result
            per_stratum_curve[stratum][ceiling_km] = best
        joint = (
            per_stratum_curve["shallow"][ceiling_km]["loo_log_likelihood"]
            + per_stratum_curve["deep"][ceiling_km]["loo_log_likelihood"]
        )
        print(
            f"  ceiling {ceiling_km:>7.1f} km   "
            f"shallow best k={per_stratum_curve['shallow'][ceiling_km]['k']:<3}"
            f" LL={per_stratum_curve['shallow'][ceiling_km]['loo_log_likelihood']:>12.2f}   "
            f"deep best k={per_stratum_curve['deep'][ceiling_km]['k']:<3}"
            f" LL={per_stratum_curve['deep'][ceiling_km]['loo_log_likelihood']:>12.2f}   "
            f"joint={joint:>14.2f}"
        )

    curve = []
    joint_scores = {}
    for ceiling_km in CEILING_CANDIDATES_KM:
        joint = (
            per_stratum_curve["shallow"][ceiling_km]["loo_log_likelihood"]
            + per_stratum_curve["deep"][ceiling_km]["loo_log_likelihood"]
        )
        joint_scores[ceiling_km] = joint
        curve.append({
            "ceiling_km": ceiling_km,
            "shallow_best_k": per_stratum_curve["shallow"][ceiling_km]["k"],
            "shallow_loo_log_likelihood": per_stratum_curve["shallow"][ceiling_km]["loo_log_likelihood"],
            "deep_best_k": per_stratum_curve["deep"][ceiling_km]["k"],
            "deep_loo_log_likelihood": per_stratum_curve["deep"][ceiling_km]["loo_log_likelihood"],
            "joint_loo_log_likelihood": joint,
        })

    selected_ceiling = max(joint_scores, key=joint_scores.get)
    is_boundary = selected_ceiling in (min(CEILING_CANDIDATES_KM), max(CEILING_CANDIDATES_KM))
    values = [joint_scores[c] for c in CEILING_CANDIDATES_KM]
    monotonic_increasing = all(values[i] <= values[i + 1] for i in range(len(values) - 1))
    monotonic_decreasing = all(values[i] >= values[i + 1] for i in range(len(values) - 1))

    print()
    print(f"  selected ceiling: {selected_ceiling} km, "
          f"{'BOUNDARY solution' if is_boundary else 'interior optimum'}")
    print(f"  monotonic increasing across the whole range: {monotonic_increasing}")
    print(f"  monotonic decreasing across the whole range: {monotonic_decreasing}")
    print()

    return {
        "rule": (
            "for each candidate ceiling, the best leave-one-out Poisson "
            "log-likelihood achievable over a representative k grid is "
            "recorded per stratum (k profiled out), the two strata's "
            "profiled log-likelihoods are summed to one joint score per "
            "ceiling, and the ceiling maximising that joint score is "
            "selected; this ceiling is shared by both strata, unlike k"
        ),
        "k_profile_candidates": list(K_PROFILE_CANDIDATES),
        "candidates_km": list(CEILING_CANDIDATES_KM),
        "curve": curve,
        "selected_ceiling_km": selected_ceiling,
        "is_boundary_solution": is_boundary,
        "monotonic_increasing_across_range": monotonic_increasing,
        "monotonic_decreasing_across_range": monotonic_decreasing,
    }


def _stage_two_k(catalogue: list[dict], holdout_start: dt.datetime, ceiling_km: float) -> dict:
    print("=" * 72)
    print(f"Stage 2: k per stratum, at the frozen ceiling of {ceiling_km} km")
    print("=" * 72)

    strata = {}
    for stratum in adaptive.STRATA:
        curve = []
        best = None
        for k in K_CANDIDATES:
            result = adaptive.loo_log_likelihood_for_params(
                catalogue, stratum, k, ceiling_km, holdout_start=holdout_start
            )
            curve.append(result)
            if best is None or result["loo_log_likelihood"] > best["loo_log_likelihood"]:
                best = result

        selected_k = best["k"]
        is_boundary = selected_k in (min(K_CANDIDATES), max(K_CANDIDATES))
        values = [p["loo_log_likelihood"] for p in curve]
        print(f"\n  {stratum}: n_events={curve[0]['n_events']:,}")
        for p in curve:
            marker = "   <== selected" if p["k"] == selected_k else ""
            print(
                f"    k={p['k']:<4} LL={p['loo_log_likelihood']:>12.2f}   "
                f"sigma [{p['sigma_km_min']:.2f}, {p['sigma_km_max']:.2f}] km{marker}"
            )
        print(f"  selected k={selected_k}, "
              f"{'BOUNDARY solution' if is_boundary else 'interior optimum'}")

        strata[stratum] = {
            "n_events": curve[0]["n_events"],
            "k_candidates": list(K_CANDIDATES),
            "curve": [
                {
                    "k": p["k"],
                    "loo_log_likelihood": p["loo_log_likelihood"],
                    "sigma_km_min": p["sigma_km_min"],
                    "sigma_km_max": p["sigma_km_max"],
                }
                for p in curve
            ],
            "selected_k": selected_k,
            "is_boundary_solution": is_boundary,
            "monotonic_declining_from_selected": all(
                values[i] >= values[i + 1] for i in range(len(values) - 1)
            ),
        }
    print()
    return strata


def main() -> int:
    t_start = time.time()
    catalogue = storage.read_parquet(FIXTURE)
    latest = max(e["origintime"] for e in catalogue)
    holdout_start = latest - dt.timedelta(days=WINDOW_DAYS * N_WINDOWS)

    print(f"fixture latest event : {latest}")
    print(f"fit_start             : {adaptive.FIT_START_DEFAULT}")
    print(f"holdout_start         : {holdout_start}  ({N_WINDOWS} x {WINDOW_DAYS}-day windows excluded)")
    print(f"floor (frozen, D13.4b): {adaptive.SIGMA_FLOOR_KM} km")
    print()

    ceiling_selection = _stage_one_ceiling(catalogue, holdout_start)
    strata = _stage_two_k(catalogue, holdout_start, ceiling_selection["selected_ceiling_km"])

    out = {
        "method": (
            "leave-one-out Poisson log-likelihood cross validation on "
            "fit-period events only, per DECISIONS.md D13.4b. The ceiling "
            "is selected first with k profiled out (stage 1, shared across "
            "strata); k is then selected per stratum at that frozen "
            "ceiling (stage 2)."
        ),
        "floor_km": adaptive.SIGMA_FLOOR_KM,
        "floor_rationale": (
            "one grid cell width east to west, the smaller of the two cell "
            "dimensions at New Zealand latitudes and therefore binding for "
            "an isotropic kernel; inherited unchanged from the fixed "
            "kernel's own D13.4a derivation, since it is a property of the "
            "grid, not of the kernel form"
        ),
        "holdout_start": holdout_start.isoformat(),
        "window_days": WINDOW_DAYS,
        "n_windows_held_out": N_WINDOWS,
        "ceiling_selection": ceiling_selection,
        "strata": strata,
    }
    adaptive.ADAPTIVE_PARAMS_PATH.parent.mkdir(parents=True, exist_ok=True)
    adaptive.ADAPTIVE_PARAMS_PATH.write_text(json.dumps(out, indent=2) + "\n", encoding="utf-8")
    print(f"wrote {adaptive.ADAPTIVE_PARAMS_PATH}")
    print(f"total time: {time.time() - t_start:.1f}s")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
