"""Map completeness spatially and cost out candidate collection regions."""
import csv
import os
from collections import Counter, defaultdict

CACHE = os.path.dirname(os.path.abspath(__file__))

# Pool only the contiguous recent span so the Mc estimate has one vintage.
WANT = ["cat_1.5_2023_2024.csv", "cat_1.5_2024_2025.csv", "cat_1.5_2025_2026.csv"]
rows = []
for f in WANT:
    p = os.path.join(CACHE, f)
    if os.path.exists(p):
        with open(p, encoding="utf-8") as fh:
            rows += list(csv.DictReader(fh))
print(f"catalogue: {len(rows)} events, 2023-01-01 to 2026-01-01, M>=1.5\n")


def lon360(v):
    v = float(v)
    return v + 360 if v < 0 else v


evs = [
    (float(r["magnitude"]), lon360(r["longitude"]), float(r["latitude"]))
    for r in rows
    if r["magnitude"]
]


def mc(mags):
    """Maximum-curvature Mc: modal bin of the non-cumulative FMD."""
    if len(mags) < 150:
        return None
    b = Counter(round(m, 1) for m in mags)
    return max(range(15, 50), key=lambda i: b.get(i / 10, 0)) / 10


# --- Mc on a 1 degree grid -------------------------------------------------
grid = defaultdict(list)
for m, lo, la in evs:
    grid[(int(lo), int(la // 1))].append(m)

print("Mc on a 1 degree grid (cells with >=150 events):")
solved = {k: mc(v) for k, v in grid.items() if mc(v) is not None}
print(f"  cells with a usable Mc estimate: {len(solved)} of {len(grid)}")
hist = Counter(solved.values())
for v in sorted(hist):
    print(f"    Mc {v:.1f}: {hist[v]:3d} cells  {'#' * hist[v]}")

# --- cost out candidate regions --------------------------------------------
print("\nCandidate collection regions, priced in M>=3.5 events per day:")
tot35 = sum(1 for m, lo, la in evs if m >= 3.5)
days = 3 * 365


def price(label, keep):
    n = sum(1 for m, lo, la in evs if m >= 3.5 and keep(lo, la))
    print(
        f"  {label:<44} {n:5d} ev  {n/days:5.2f}/day  {n/(days/7):5.1f}/wk"
        f"  {100*n/tot35:5.1f}% of target set"
    )
    return n


price("A. full bbox, as specified now", lambda lo, la: True)
price("B. exclude lon>=177 & lat>=-37 (Kermadec)", lambda lo, la: not (lo >= 177 and la >= -37))
price("C. exclude lon>=179 & lat>=-35 (far Kermadec)", lambda lo, la: not (lo >= 179 and la >= -35))
price("D. lon<=180 only", lambda lo, la: lo <= 180)
price("E. lat<=-34 only", lambda lo, la: la <= -34)

MCMAX = 3.0
ok = {k for k, v in solved.items() if v <= MCMAX}
print(f"\n  cells with measured Mc <= {MCMAX}: {len(ok)} of {len(solved)}")
n = price(
    f"F. only cells with measured Mc <= {MCMAX}",
    lambda lo, la: (int(lo), int(la // 1)) in ok,
)

MCMAX2 = 2.5
ok2 = {k for k, v in solved.items() if v <= MCMAX2}
print(f"  cells with measured Mc <= {MCMAX2}: {len(ok2)} of {len(solved)}")
price(
    f"G. only cells with measured Mc <= {MCMAX2}",
    lambda lo, la: (int(lo), int(la // 1)) in ok2,
)

print("\nFor option F, split by stratum at 45 km:")
rows2 = [
    (float(r["magnitude"]), lon360(r["longitude"]), float(r["latitude"]), float(r["depth"]))
    for r in rows
    if r["magnitude"] and r["depth"]
]
for sname, skeep in (("shallow", lambda d: d <= 45), ("deep", lambda d: d > 45)):
    n = sum(
        1
        for m, lo, la, d in rows2
        if m >= 3.5 and skeep(d) and (int(lo), int(la // 1)) in ok
    )
    print(f"  {sname:<8} {n:5d} ev  {n/days:5.2f}/day  {n/(days/7):5.1f}/wk")
