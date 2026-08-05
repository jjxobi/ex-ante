"""Are the consistency test quantiles uniform, or is the scorer broken?

A single week's S-test quantile of 0.0020 has two very different explanations.
It is either small sample noise, which is expected and harmless, or it is a
silent cell permutation between this project's grid and the one handed to
pyCSEP, which would leave every total correct and every spatial result wrong.
That is this project's recurring failure mode, so it gets checked rather than
assumed.

The decisive property: under a forecast that is actually right, the quantile of
a consistency test is uniform on [0, 1]. One extreme value is unremarkable.
Twenty consecutive extreme values is a bug.

This scores many independent past windows and looks at the distribution.
"""

import datetime as dt
import statistics
import sys
from collections import Counter

sys.path.insert(0, "src")
from eq import baseline, expander, paths, region, score, storage  # noqa: E402

WINDOW_DAYS = 7
N_WINDOWS = 26
STRATUM = "shallow"


def main() -> int:
    catalogue = storage.read_parquet(paths.SNAPSHOT_DIR / "catalogue-2026-08-05.parquet")
    fixture = storage.read_parquet(
        paths.REPO_ROOT / "tests" / "fixtures" / "catalogue-fit-window.parquet"
    )
    fitted = baseline.fit(fixture, STRATUM)
    print(f"fitted {STRATUM} stratum on {len(fixture):,} fixture events\n")

    latest = max(e["origintime"] for e in catalogue)
    end = dt.datetime(latest.year, latest.month, latest.day, tzinfo=dt.timezone.utc)

    rows = []
    for i in range(N_WINDOWS):
        w_end = end - dt.timedelta(days=WINDOW_DAYS * i)
        w_start = w_end - dt.timedelta(days=WINDOW_DAYS)
        observed = [
            e
            for e in catalogue
            if w_start <= e["origintime"] < w_end
            and region.stratum_for(e["depth"]) == STRATUM
        ]
        separable = baseline.forecast(fitted, w_start, w_end)
        dense = expander.expand(separable)
        result = score.score(dense, observed, w_start, w_end)
        rows.append((w_start.date(), result))

    print(f"{'window start':<14} {'n used':>7} {'N q':>8} {'S q':>8} {'M q':>8} {'L q':>8}")
    print("-" * 60)
    for start, r in rows:
        print(
            f"{str(start):<14} {r.n_events_used:>7} "
            f"{_q(r, 'number'):>8} {_q(r, 'spatial'):>8} "
            f"{_q(r, 'magnitude'):>8} {_q(r, 'likelihood'):>8}"
        )

    print()
    print("=" * 60)
    print("Distribution of the spatial test quantile")
    print("=" * 60)
    spatials = [_qf(r, "spatial") for _, r in rows if _qf(r, "spatial") is not None]
    if not spatials:
        print("  no spatial quantiles produced")
        return 1
    below_01 = sum(1 for q in spatials if q < 0.01)
    below_05 = sum(1 for q in spatials if q < 0.05)
    print(f"  windows scored     : {len(spatials)}")
    print(f"  median quantile    : {statistics.median(spatials):.4f}")
    print(f"  below 0.01         : {below_01}  (expected about {0.01*len(spatials):.1f})")
    print(f"  below 0.05         : {below_05}  (expected about {0.05*len(spatials):.1f})")
    print()
    if below_05 > 0.5 * len(spatials):
        print("  VERDICT: the spatial test rejects on most windows. That is not")
        print("  small sample noise. Suspect a cell ordering permutation between")
        print("  the frozen grid and the forecast handed to pyCSEP.")
        return 1
    print("  VERDICT: quantiles are spread rather than uniformly extreme, which")
    print("  is consistent with small sample noise rather than a permutation.")
    return 0


FIELD = {
    "number": "n_test",
    "spatial": "s_test",
    "magnitude": "m_test",
    "likelihood": "l_test",
}


def _qf(result, name):
    q = getattr(getattr(result, FIELD[name]), "quantile", None)
    if isinstance(q, (tuple, list)):
        q = q[-1]
    return q


def _q(result, name):
    q = _qf(result, name)
    return "n/a" if q is None else f"{q:.4f}"


if __name__ == "__main__":
    raise SystemExit(main())
