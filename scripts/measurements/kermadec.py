"""Challenge 4 follow-up: the Kermadec cells are the named completeness exposure."""
import csv
import os
from collections import Counter

CACHE = os.path.dirname(os.path.abspath(__file__))


def load(prefix):
    rows = []
    for f in sorted(os.listdir(CACHE)):
        if f.startswith(prefix):
            with open(os.path.join(CACHE, f), encoding="utf-8") as fh:
                rows += list(csv.DictReader(fh))
    return rows


def lon360(v):
    v = float(v)
    return v + 360 if v < 0 else v


def mc_of(mags, label):
    if len(mags) < 200:
        print(f"  {label:<34} n={len(mags):6d}  (too few for a stable estimate)")
        return
    bins = Counter(round(m, 1) for m in mags)
    modal = max(range(15, 45), key=lambda i: bins.get(i / 10, 0))
    n35 = sum(1 for m in mags if m >= 3.5)
    print(f"  {label:<34} n={len(mags):6d}  Mc = M{modal/10:.1f}   M>=3.5 = {n35}")


rows = load("cat_1.5_")
print(f"pooled 2023-2026 low-magnitude catalogue: n={len(rows)}\n")

evs = [
    (float(r["magnitude"]), lon360(r["longitude"]), float(r["latitude"]), float(r["depth"]))
    for r in rows
    if r["magnitude"] and r["depth"]
]

regions = {
    "Kermadec (lon>=177, lat>=-37)": lambda lo, la: lo >= 177 and la >= -37,
    "Far Kermadec (lon>=179, lat>=-35)": lambda lo, la: lo >= 179 and la >= -35,
    "North Island (172-179, -42..-34)": lambda lo, la: 172 <= lo < 179 and -42 <= la < -34,
    "South Island (166-175, -47..-40)": lambda lo, la: 166 <= lo < 175 and -47 <= la < -40,
    "Offshore south/west (lon<168)": lambda lo, la: lo < 168,
}

print("Mc by region, pooled 2023-2026:")
for label, keep in regions.items():
    mc_of([m for m, lo, la, d in evs if keep(lo, la)], label)

print("\nSame regions split by stratum at a 45 km boundary:")
for label, keep in regions.items():
    for sname, skeep in (("shallow", lambda d: d <= 45), ("deep", lambda d: d > 45)):
        mc_of(
            [m for m, lo, la, d in evs if keep(lo, la) and skeep(d)],
            f"{label.split(' (')[0]} / {sname}",
        )

print("\nShare of the M>=3.5 target set that lives in the Kermadec exposure:")
tot = sum(1 for m, lo, la, d in evs if m >= 3.5)
ker = sum(1 for m, lo, la, d in evs if m >= 3.5 and lo >= 177 and la >= -37)
far = sum(1 for m, lo, la, d in evs if m >= 3.5 and lo >= 179 and la >= -35)
print(f"  total M>=3.5      : {tot}")
print(f"  Kermadec          : {ker}  ({100*ker/tot:.1f}%)")
print(f"  Far Kermadec      : {far}  ({100*far/tot:.1f}%)")
