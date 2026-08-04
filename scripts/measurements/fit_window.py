"""Find the earliest fitting-window start at which every retained cell is complete.

The region (D1) is frozen and does not move. The fitting window is a Phase 2
model choice and is the correct place to repair the era mismatch.

Answers: what is the earliest year Y such that, pooling [Y, 2026), every cell in
the frozen region has measured Mc <= 2.6?
"""
import csv
import math
import os
import time
import urllib.request
from collections import Counter, defaultdict

BBOX = "163.60840,-49.18170,182.98828,-32.28713"
CACHE = os.path.dirname(os.path.abspath(__file__))
MCMAX = 2.6
MINN = 150
YEARS = list(range(2005, 2026))


def fetch_year(y):
    name = os.path.join(CACHE, f"cat_1.5_{y}_{y+1}.csv")
    if not os.path.exists(name):
        url = (
            f"https://quakesearch.geonet.org.nz/csv?bbox={BBOX}&minmag=1.5"
            f"&startdate={y}-01-01T00:00:00&enddate={y+1}-01-01T00:00:00"
        )
        for attempt in range(6):
            try:
                with urllib.request.urlopen(url, timeout=600) as r:
                    data = r.read().decode("utf-8")
                break
            except Exception as exc:
                wait = 5 * 2**attempt
                print(f"    retry {attempt+1} ({exc.__class__.__name__}) {wait}s")
                time.sleep(wait)
        else:
            raise RuntimeError(f"gave up on {y}")
        with open(name, "w", encoding="utf-8", newline="") as f:
            f.write(data)
        time.sleep(3)
    with open(name, encoding="utf-8") as f:
        return list(csv.DictReader(f))


def lon360(v):
    v = float(v)
    return v + 360 if v < 0 else v


def mc(mags, minn=MINN):
    if len(mags) < minn:
        return None
    b = Counter(round(m, 1) for m in mags)
    return max(range(15, 50), key=lambda i: b.get(i / 10, 0)) / 10


print("Loading catalogue year by year (cached after first run)")
by_year = {}
for y in YEARS:
    rows = fetch_year(y)
    by_year[y] = [
        (float(r["magnitude"]), lon360(r["longitude"]), float(r["latitude"]), float(r["depth"]))
        for r in rows
        if r["magnitude"] and r["depth"]
    ]
    print(f"  {y}: {len(by_year[y])}")

# Reconstruct the frozen region exactly as D1 defines it: 2021-2026, Mc <= 2.6.
sel = defaultdict(list)
for y in range(2021, 2026):
    for m, lo, la, d in by_year[y]:
        sel[(math.floor(lo), math.floor(la))].append(m)
REGION = {k for k, v in sel.items() if (mc(v) or 99) <= MCMAX}
print(f"\nfrozen region reconstructed: {len(REGION)} cells\n")

print("=" * 74)
print("Per-cell Mc over [Y, 2026) for each candidate fitting-window start")
print("=" * 74)
print(f"{'Y':<6} {'measurable':>11} {'worst Mc':>9} {'cells > 2.6':>12} {'verdict':>10}")
print("-" * 74)
earliest = None
for Y in YEARS:
    pool = defaultdict(list)
    for y in range(Y, 2026):
        for m, lo, la, d in by_year[y]:
            k = (math.floor(lo), math.floor(la))
            if k in REGION:
                pool[k].append(m)
    vals = {k: mc(v) for k, v in pool.items()}
    meas = {k: v for k, v in vals.items() if v is not None}
    over = [k for k, v in meas.items() if v > MCMAX]
    worst = max(meas.values()) if meas else None
    ok = len(meas) == len(REGION) and not over
    if ok and earliest is None:
        earliest = Y
    print(
        f"{Y:<6} {len(meas):>7}/{len(REGION):<3} {worst:>9.1f} {len(over):>12} "
        f"{'OK' if ok else 'fails':>10}"
    )

print(f"\n  EARLIEST CLEAN FITTING-WINDOW START: {earliest}")
if earliest:
    print(f"  fitting window {earliest} to 2026 = {2026-earliest} years")

print()
print("=" * 74)
print("Complete-from year for each cell (earliest Y with Mc <= 2.6 over [Y,2026))")
print("=" * 74)
complete_from = {}
for k in REGION:
    for Y in YEARS:
        pool = []
        for y in range(Y, 2026):
            pool += [m for m, lo, la, d in by_year[y] if (math.floor(lo), math.floor(la)) == k]
        v = mc(pool)
        if v is not None and v <= MCMAX:
            complete_from[k] = Y
            break
    else:
        complete_from[k] = None

late = sorted(
    ((v, k) for k, v in complete_from.items() if v is not None), reverse=True
)[:10]
print("  the 10 cells that become complete latest:")
for v, k in late:
    print(f"    ({k[0]},{k[1]})  complete from {v}")
none = [k for k, v in complete_from.items() if v is None]
if none:
    print(f"  cells never clean over any window: {none}")

print("\n  the four cells identified by the held-out check:")
for k in [(178, -37), (172, -41), (177, -37), (179, -38)]:
    if k in complete_from:
        print(f"    ({k[0]},{k[1]})  complete from {complete_from[k]}")

if earliest:
    print()
    print("=" * 74)
    print(f"Event supply for a {earliest}-2026 fitting window, M>=3.0, inside the region")
    print("=" * 74)
    n_sh = n_dp = 0
    for y in range(earliest, 2026):
        for m, lo, la, d in by_year[y]:
            if m >= 3.0 and (math.floor(lo), math.floor(la)) in REGION:
                if d <= 41:
                    n_sh += 1
                else:
                    n_dp += 1
    yrs = 2026 - earliest
    print(f"  shallow: {n_sh:6d} events over {yrs} yr  ({n_sh/yrs:.0f}/yr)")
    print(f"  deep   : {n_dp:6d} events over {yrs} yr  ({n_dp/yrs:.0f}/yr)")
    print(f"  cells  : {len(REGION)} one-degree, {len(REGION)*100} grid cells at 0.1 deg")
    print(f"  mean events per grid cell, shallow: {n_sh/(len(REGION)*100):.2f}")
