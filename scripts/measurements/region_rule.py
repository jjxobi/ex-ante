"""Define the collection region by a completeness rule, not hand-drawn boxes.

Rule under test:
  a 1 degree cell is IN the collection region iff it has >= MINN events in the
  reference window AND its measured Mc <= MCMAX. Cells that cannot be measured
  are excluded, which is the conservative direction.
"""
import csv
import math
import os
from collections import Counter, defaultdict

CACHE = os.path.dirname(os.path.abspath(__file__))
MCMAX = 2.6
MINN = 150

rows = []
for a in range(2021, 2026):
    with open(os.path.join(CACHE, f"cat_1.5_{a}_{a+1}.csv"), encoding="utf-8") as fh:
        rows += list(csv.DictReader(fh))


def lon360(v):
    v = float(v)
    return v + 360 if v < 0 else v


def mc(mags, minn=MINN):
    if len(mags) < minn:
        return None
    b = Counter(round(m, 1) for m in mags)
    return max(range(15, 50), key=lambda i: b.get(i / 10, 0)) / 10


evs = [
    (float(r["magnitude"]), lon360(r["longitude"]), float(r["latitude"]), float(r["depth"]))
    for r in rows
    if r["magnitude"] and r["depth"]
]
print(f"reference window 2021-2026, M>=1.5: {len(evs)} events\n")

cells = defaultdict(list)
for m, lo, la, d in evs:
    cells[(math.floor(lo), math.floor(la))].append(m)

measured = {k: mc(v) for k, v in cells.items()}
keep = {k for k, v in measured.items() if v is not None and v <= MCMAX}
unmeasured = [k for k, v in measured.items() if v is None]
failed = {k: v for k, v in measured.items() if v is not None and v > MCMAX}

print(f"1 degree cells with any events      : {len(cells)}")
print(f"  measurable (>= {MINN} events)       : {len(cells) - len(unmeasured)}")
print(f"  measurable and Mc <= {MCMAX}         : {len(keep)}   <- the collection region")
print(f"  measurable but Mc > {MCMAX} (cut)    : {len(failed)}")
print(f"  not measurable (cut, conservative) : {len(unmeasured)}")

print(f"\ncells failing the Mc <= {MCMAX} test:")
for (lo, la), v in sorted(failed.items(), key=lambda x: -x[1]):
    n = len(cells[(lo, la)])
    print(f"    lon {lo}-{lo+1}  lat {la}-{la+1}   n={n:6d}  Mc = M{v:.1f}")

print(f"\nretained cells, worst Mc: M{max(measured[k] for k in keep):.1f}")
print(f"retained cells, Mc histogram:")
for v, n in sorted(Counter(measured[k] for k in keep).items()):
    print(f"    M{v:.1f}: {n:3d} cells  {'#' * n}")

days = 5 * 365
print(f"\nEvent rates inside the rule-defined region:")
print(f"{'thresh':<9} {'total/wk':>9} {'shallow/wk':>11} {'deep/wk':>9} {'shallow/day':>12} {'deep/day':>9}")
print("-" * 62)
inreg = [e for e in evs if (math.floor(e[1]), math.floor(e[2])) in keep]
for t in (2.75, 3.0, 3.25, 3.5):
    tot = sum(1 for m, _, _, _ in inreg if m >= t)
    sh = sum(1 for m, _, _, d in inreg if m >= t and d <= 45)
    dp = sum(1 for m, _, _, d in inreg if m >= t and d > 45)
    print(
        f"M>={t:<6} {tot/(days/7):>9.1f} {sh/(days/7):>11.1f} {dp/(days/7):>9.1f}"
        f" {sh/days:>12.2f} {dp/days:>9.2f}"
    )

tot_all = sum(1 for m, _, _, _ in evs if m >= 3.0)
tot_in = sum(1 for m, _, _, _ in inreg if m >= 3.0)
print(f"\nshare of national M>=3.0 retained: {100*tot_in/tot_all:.1f}%")

print("\nper-stratum Mc inside the region (45 km boundary):")
for sname, skeep in (("shallow", lambda d: d <= 45), ("deep", lambda d: d > 45)):
    sub = [m for m, _, _, d in inreg if skeep(d)]
    print(f"  {sname:<8} n={len(sub):6d}  Mc = M{mc(sub):.1f}")

print("\nregion stability: worst retained-cell Mc measured on older windows")
for yr in (2005, 2010, 2015, 2020):
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
    sub = [m for m, lo, la in ye if (math.floor(lo), math.floor(la)) in keep]
    v = mc(sub)
    n30 = sum(1 for m in sub if m >= 3.0)
    print(f"  {yr}: n={len(sub):6d}  pooled Mc = M{v:.1f}   M>=3.0 = {n30:5d} ({n30/52:.1f}/wk)")

# ---------------------------------------------------------------- boundary
print()
print("=" * 72)
print("Depth boundary with bandwidth fixed by Silverman's rule (no free choice)")
print("=" * 72)
drows = []
for a, b in ((2005, 2010), (2010, 2015), (2015, 2020), (2020, 2025), (2025, 2026)):
    with open(os.path.join(CACHE, f"cat_3.0_{a}_{b}.csv"), encoding="utf-8") as fh:
        drows += list(csv.DictReader(fh))

free = []
for r in drows:
    if r["depthtype"] == "operator assigned" or not r["depth"]:
        continue
    d = float(r["depth"])
    lo, la = lon360(r["longitude"]), float(r["latitude"])
    if d > 0 and (math.floor(lo), math.floor(la)) in keep:
        free.append(d)
print(f"free-depth events inside the region: {len(free)}")

logs = sorted(math.log10(d) for d in free)
n = len(logs)
mean = sum(logs) / n
sd = math.sqrt(sum((x - mean) ** 2 for x in logs) / (n - 1))
q1, q3 = logs[n // 4], logs[3 * n // 4]
iqr = q3 - q1
h = 0.9 * min(sd, iqr / 1.34) * n ** (-1 / 5)
print(f"  sd(log10 depth) = {sd:.4f}, IQR = {iqr:.4f}")
print(f"  Silverman bandwidth h = {h:.4f}  (fixed by rule, not chosen)")

grid = [1.0 + i * 0.002 for i in range(501)]
dens = [
    sum(math.exp(-0.5 * ((g - x) / h) ** 2) for x in logs) / (n * h * math.sqrt(2 * math.pi))
    for g in grid
]
best = None
for i in range(3, len(grid) - 3):
    if dens[i] < dens[i - 1] and dens[i] < dens[i + 1]:
        if best is None or dens[i] < best[1]:
            best = (grid[i], dens[i])
print(f"  -> boundary = {10**best[0]:.1f} km" if best else "  -> no interior minimum")

print("\n  sensitivity check, boundary vs bandwidth multiplier:")
for mult in (0.7, 0.85, 1.0, 1.2, 1.5):
    hh = h * mult
    dd = [
        sum(math.exp(-0.5 * ((g - x) / hh) ** 2) for x in logs) / (n * hh * math.sqrt(2 * math.pi))
        for g in grid
    ]
    bb = None
    for i in range(3, len(grid) - 3):
        if dd[i] < dd[i - 1] and dd[i] < dd[i + 1]:
            if bb is None or dd[i] < bb[1]:
                bb = (grid[i], dd[i])
    print(f"    h x {mult:<4} = {hh:.4f} -> {10**bb[0]:.1f} km" if bb else f"    h x {mult} -> none")
