"""Two checks on the frozen region, testing for selection-on-noise.

Check 1: the 41 cells were selected on measured Mc in 2021-2026. Recompute each
         cell's Mc on HELD-OUT years and report the worst. If selection was
         fitted to estimation noise, held-out Mc should come back higher.
Check 2: cross-tabulate measured Mc against event count. Cells near the 150
         event floor have the noisiest estimates and are the most likely to have
         been admitted by downward error. If near-ceiling cells are also
         low-count cells, that corner is where the region will fail.
"""
import csv
import math
import os
from collections import Counter, defaultdict

CACHE = os.path.dirname(os.path.abspath(__file__))
MCMAX = 2.6
MINN = 150
SELECT_YEARS = [2021, 2022, 2023, 2024, 2025]
HELDOUT_YEARS = [2005, 2010, 2015, 2020]


def lon360(v):
    v = float(v)
    return v + 360 if v < 0 else v


def load(years):
    rows = []
    for y in years:
        p = os.path.join(CACHE, f"cat_1.5_{y}_{y+1}.csv")
        if os.path.exists(p):
            with open(p, encoding="utf-8") as fh:
                rows += list(csv.DictReader(fh))
    return [
        (float(r["magnitude"]), lon360(r["longitude"]), float(r["latitude"]))
        for r in rows
        if r["magnitude"]
    ]


def mc(mags, minn=MINN):
    if len(mags) < minn:
        return None
    b = Counter(round(m, 1) for m in mags)
    return max(range(15, 50), key=lambda i: b.get(i / 10, 0)) / 10


def cellify(evs):
    d = defaultdict(list)
    for m, lo, la in evs:
        d[(math.floor(lo), math.floor(la))].append(m)
    return d


sel = cellify(load(SELECT_YEARS))
selected_mc = {k: mc(v) for k, v in sel.items()}
region = {k for k, v in selected_mc.items() if v is not None and v <= MCMAX}
print(f"frozen region: {len(region)} cells, selected on {SELECT_YEARS[0]}-{SELECT_YEARS[-1]+1}\n")

# ------------------------------------------------------------------ check 2
print("=" * 74)
print("CHECK 2: is the near-ceiling corner also the low-count corner?")
print("=" * 74)
print(f"{'cell (lon,lat)':<18} {'n':>7} {'Mc':>6}   {'risk corner?':<14}")
print("-" * 74)
rows = sorted(((selected_mc[k], len(sel[k]), k) for k in region), reverse=True)
risky = []
for v, n, k in rows:
    flag = ""
    if v >= 2.4 and n < 600:
        flag = "<- low n, high Mc"
        risky.append((v, n, k))
    print(f"  ({k[0]},{k[1]})".ljust(18) + f"{n:>7} {v:>6.1f}   {flag:<14}")
print(f"\n  cells in the risk corner (Mc >= 2.4 and n < 600): {len(risky)}")

lowest_n = sorted(rows, key=lambda x: x[1])[:8]
print("\n  the 8 lowest-count retained cells:")
for v, n, k in lowest_n:
    print(f"    ({k[0]},{k[1]})  n={n:6d}  Mc={v:.1f}")
corr_hi = [v for v, n, k in rows if n < 600]
corr_lo = [v for v, n, k in rows if n >= 600]
if corr_hi and corr_lo:
    print(f"\n  mean Mc, low-count cells  (n<600):  {sum(corr_hi)/len(corr_hi):.2f}  ({len(corr_hi)} cells)")
    print(f"  mean Mc, high-count cells (n>=600): {sum(corr_lo)/len(corr_lo):.2f}  ({len(corr_lo)} cells)")
    print("  (if low-count cells show LOWER mean Mc, that is the selection effect)")

# ------------------------------------------------------------------ check 1
print()
print("=" * 74)
print(f"CHECK 1: held-out per-cell Mc on {HELDOUT_YEARS}")
print("=" * 74)
held = cellify(load(HELDOUT_YEARS))
print(f"{'cell':<14} {'n(sel)':>8} {'Mc(sel)':>8} {'n(held)':>9} {'Mc(held)':>9} {'shift':>7}")
print("-" * 74)
worst = None
shifts = []
unmeasurable = 0
for v, n, k in rows:
    hv = mc(held.get(k, []))
    hn = len(held.get(k, []))
    if hv is None:
        unmeasurable += 1
        print(f"  ({k[0]},{k[1]})".ljust(14) + f"{n:>8} {v:>8.1f} {hn:>9} {'  n/a':>9} {'':>7}")
        continue
    shift = hv - v
    shifts.append(shift)
    mark = "  <-- ABOVE CEILING" if hv > MCMAX else ""
    print(
        f"  ({k[0]},{k[1]})".ljust(14)
        + f"{n:>8} {v:>8.1f} {hn:>9} {hv:>9.1f} {shift:>+7.1f}{mark}"
    )
    if worst is None or hv > worst[0]:
        worst = (hv, k)

print(f"\n  cells not measurable in held-out window: {unmeasurable} of {len(region)}")
if shifts:
    print(f"  mean shift held-out minus selection: {sum(shifts)/len(shifts):+.2f} magnitude units")
    print(f"  cells shifting upward: {sum(1 for s in shifts if s > 0)} of {len(shifts)}")
if worst:
    print(f"\n  WORST held-out cell Mc = M{worst[0]:.1f} at cell {worst[1]}")
    above = [1 for v, n, k in rows if (mc(held.get(k, [])) or 0) > MCMAX]
    print(f"  retained cells exceeding the {MCMAX} ceiling on held-out data: {len(above)}")
    print()
    if worst[0] <= MCMAX:
        print("  VERDICT: held-out worst cell is within the ceiling. Freeze M3.0 as designed.")
    else:
        print(f"  VERDICT: held-out worst cell EXCEEDS the ceiling by {worst[0]-MCMAX:.1f}.")
        print("  Per the agreed rule the fix is the inclusion ceiling, not the threshold.")
