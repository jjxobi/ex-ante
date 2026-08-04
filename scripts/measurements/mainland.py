"""Price the mainland-only region at candidate thresholds, to recover lost power."""
import csv
import os
from collections import Counter

CACHE = os.path.dirname(os.path.abspath(__file__))
rows = []
for f in ("cat_1.5_2023_2024.csv", "cat_1.5_2024_2025.csv", "cat_1.5_2025_2026.csv"):
    with open(os.path.join(CACHE, f), encoding="utf-8") as fh:
        rows += list(csv.DictReader(fh))


def lon360(v):
    v = float(v)
    return v + 360 if v < 0 else v


evs = [
    (float(r["magnitude"]), lon360(r["longitude"]), float(r["latitude"]), float(r["depth"]))
    for r in rows
    if r["magnitude"] and r["depth"]
]
mainland = [e for e in evs if not (e[1] >= 177 and e[2] >= -37)]
days = 3 * 365
print(f"mainland events M>=1.5, 2023-2026: {len(mainland)}")

b = Counter(round(m, 1) for m, _, _, _ in mainland)
print(f"mainland Mc (max curvature, pooled): M{max(range(15,50), key=lambda i: b.get(i/10,0))/10:.1f}\n")

print(f"{'thresh':<8} {'total/wk':>9} {'shallow/wk':>11} {'deep/wk':>9} {'shallow/day':>12} {'deep/day':>9}")
print("-" * 62)
for t in (2.75, 3.0, 3.25, 3.5):
    tot = sum(1 for m, _, _, _ in mainland if m >= t)
    sh = sum(1 for m, _, _, d in mainland if m >= t and d <= 45)
    dp = sum(1 for m, _, _, d in mainland if m >= t and d > 45)
    print(
        f"M>={t:<5} {tot/(days/7):>9.1f} {sh/(days/7):>11.1f} {dp/(days/7):>9.1f}"
        f" {sh/days:>12.2f} {dp/days:>9.2f}"
    )

print("\nmainland Mc by stratum (45 km boundary), pooled 2023-2026:")
for sname, skeep in (("shallow", lambda d: d <= 45), ("deep", lambda d: d > 45)):
    sub = [m for m, _, _, d in mainland if skeep(d)]
    bb = Counter(round(m, 1) for m in sub)
    v = max(range(15, 50), key=lambda i: bb.get(i / 10, 0)) / 10
    print(f"  {sname:<8} n={len(sub):6d}  Mc = M{v:.1f}")

print("\nmainland Mc by year (is it stationary?):")
for yr in (2005, 2010, 2015, 2020, 2025):
    p = os.path.join(CACHE, f"cat_1.5_{yr}_{yr+1}.csv")
    if not os.path.exists(p):
        continue
    with open(p, encoding="utf-8") as fh:
        yrows = list(csv.DictReader(fh))
    ye = [
        (float(r["magnitude"]), lon360(r["longitude"]), float(r["latitude"]))
        for r in yrows
        if r["magnitude"]
    ]
    ml = [m for m, lo, la in ye if not (lo >= 177 and la >= -37)]
    bb = Counter(round(m, 1) for m in ml)
    v = max(range(15, 50), key=lambda i: bb.get(i / 10, 0)) / 10
    n30 = sum(1 for m in ml if m >= 3.0)
    print(f"  {yr}: n={len(ml):6d}  Mc = M{v:.1f}   M>=3.0 = {n30:5d}  ({n30/52:.1f}/wk)")
