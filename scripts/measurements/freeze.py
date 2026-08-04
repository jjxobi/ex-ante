"""Pre-committed freeze test: worst mainland sub-region Mc, plus mainland-only depth boundary.

Rule agreed BEFORE the measurement:
  worst mainland sub-region Mc <= 2.6  -> freeze M3.0
  2.6 < Mc <= 2.8                      -> freeze M3.25
  Mc > 2.8                             -> the region needs another cut
"""
import csv
import math
import os
import time
import urllib.request
from collections import Counter, defaultdict

BBOX = "163.60840,-49.18170,182.98828,-32.28713"
CACHE = os.path.dirname(os.path.abspath(__file__))


def fetch(minmag, start, end):
    name = os.path.join(CACHE, f"cat_{minmag}_{start}_{end}.csv")
    if not os.path.exists(name):
        url = (
            f"https://quakesearch.geonet.org.nz/csv?bbox={BBOX}&minmag={minmag}"
            f"&startdate={start}-01-01T00:00:00&enddate={end}-01-01T00:00:00"
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
            raise RuntimeError(f"gave up {start}-{end}")
        with open(name, "w", encoding="utf-8", newline="") as f:
            f.write(data)
        time.sleep(3)
    with open(name, encoding="utf-8") as f:
        return list(csv.DictReader(f))


def lon360(v):
    v = float(v)
    return v + 360 if v < 0 else v


def is_mainland(lo, la):
    return not (lo >= 177 and la >= -37)


def mc(mags, minn=150):
    if len(mags) < minn:
        return None
    b = Counter(round(m, 1) for m in mags)
    return max(range(15, 50), key=lambda i: b.get(i / 10, 0)) / 10


# ---------------------------------------------------------------------------
print("=" * 72)
print("PART 1: worst mainland sub-region Mc  (contiguous 2021-2026, M>=1.5)")
print("=" * 72)
rows = []
for a in range(2021, 2026):
    got = fetch(1.5, a, a + 1)
    print(f"  {a}: {len(got)}")
    rows += got
evs = [
    (float(r["magnitude"]), lon360(r["longitude"]), float(r["latitude"]), float(r["depth"]))
    for r in rows
    if r["magnitude"] and r["depth"]
]
ml = [e for e in evs if is_mainland(e[1], e[2])]
print(f"\nmainland events, 5 years: {len(ml)}")
print(f"mainland pooled Mc: M{mc([m for m, _, _, _ in ml]):.1f}")

named = {
    "Puysegur / offshore Fiordland": lambda lo, la: 164 <= lo < 168 and -49 <= la < -44.5,
    "Fiordland (incl. onshore)": lambda lo, la: 166 <= lo < 169 and -46.5 <= la < -44,
    "Offshore East Cape": lambda lo, la: 177.5 <= lo < 181 and -39.5 <= la < -37,
    "Offshore Hikurangi east": lambda lo, la: 177.5 <= lo < 180.5 and -42 <= la < -39.5,
    "Southern ocean / far SW": lambda lo, la: lo < 166,
    "North of East Cape, retained": lambda lo, la: 176 <= lo < 177 and la >= -37.5,
}
print("\nNamed sub-regions you flagged:")
worst_named = None
for label, keep in named.items():
    sub = [m for m, lo, la, d in ml if keep(lo, la)]
    v = mc(sub)
    n30 = sum(1 for m in sub if m >= 3.0)
    if v is None:
        print(f"  {label:<32} n={len(sub):6d}  (too few, no estimate)")
    else:
        print(f"  {label:<32} n={len(sub):6d}  Mc = M{v:.1f}   M>=3.0 = {n30}")
        if worst_named is None or v > worst_named[1]:
            worst_named = (label, v)

print("\nSystematic 2 degree sweep over mainland (cells with >=150 events):")
cells = defaultdict(list)
for m, lo, la, d in ml:
    cells[(int(lo // 2) * 2, int(la // 2) * 2)].append(m)
solved = []
for (lo, la), mags in cells.items():
    v = mc(mags)
    if v is not None:
        solved.append((v, lo, la, len(mags)))
solved.sort(reverse=True)
print(f"  cells estimated: {len(solved)} of {len(cells)}")
print("  worst 10 by Mc:")
for v, lo, la, n in solved[:10]:
    print(f"    lon {lo:6.1f}-{lo+2:5.1f}  lat {la:6.1f}-{la+2:5.1f}  n={n:6d}  Mc = M{v:.1f}")

worst = max([s[0] for s in solved] + ([worst_named[1]] if worst_named else []))
print(f"\n  WORST mainland sub-region Mc = M{worst:.1f}")
if worst <= 2.6:
    verdict, thresh = "freeze M3.0", 3.0
elif worst <= 2.8:
    verdict, thresh = "freeze M3.25", 3.25
else:
    verdict, thresh = "region needs another cut", None
print(f"  PRE-COMMITTED RULE SAYS: {verdict}")

# ---------------------------------------------------------------------------
print()
print("=" * 72)
print("PART 2: refit depth boundary on the MAINLAND-ONLY catalogue")
print("=" * 72)
drows = []
for a, b in ((2005, 2010), (2010, 2015), (2015, 2020), (2020, 2025), (2025, 2026)):
    got = fetch(3.0, a, b)
    print(f"  M>=3.0 {a}-{b}: {len(got)}")
    drows += got

free_all, free_ml = [], []
for r in drows:
    if r["depthtype"] == "operator assigned" or not r["depth"]:
        continue
    d = float(r["depth"])
    lo, la = lon360(r["longitude"]), float(r["latitude"])
    free_all.append(d)
    if is_mainland(lo, la):
        free_ml.append(d)
print(f"\nfree-depth events: national {len(free_all)}, mainland {len(free_ml)}")
print(f"max depth: national {max(free_all):.1f} km, mainland {max(free_ml):.1f} km")


def kde_min(depths, h=0.06):
    logs = [math.log10(d) for d in depths if d > 0]
    grid = [1.0 + i * 0.005 for i in range(201)]  # 10 km -> 100 km
    dens = [
        sum(math.exp(-0.5 * ((g - x) / h) ** 2) for x in logs) / (len(logs) * h * math.sqrt(2 * math.pi))
        for g in grid
    ]
    best = None
    for i in range(3, len(grid) - 3):
        if dens[i] < dens[i - 1] and dens[i] < dens[i + 1]:
            if best is None or dens[i] < best[1]:
                best = (grid[i], dens[i])
    return (10 ** best[0]) if best else None


for label, data in (("national (old)", free_all), ("MAINLAND (new)", free_ml)):
    print(f"\n  {label}: n={len(data)}")
    for h in (0.04, 0.05, 0.06, 0.08, 0.10):
        v = kde_min(data, h)
        print(f"    bandwidth {h:.2f} -> minimum at {v:.1f} km" if v else f"    bandwidth {h:.2f} -> none")

print("\n  mainland free-depth histogram, 10 km bins:")
for lo in range(0, 200, 10):
    n = sum(1 for d in free_ml if lo <= d < lo + 10)
    print(f"    {lo:3d}-{lo+10:3d} km | {'#' * (n // 25):<40} {n}")
print(f"    200+    km | {sum(1 for d in free_ml if d >= 200)}")

if thresh:
    print()
    print("=" * 72)
    print(f"PART 3: mainland rates at the frozen threshold M>={thresh}")
    print("=" * 72)
    days = 5 * 365
    for bnd in (40, 45, 50):
        sh = sum(1 for m, lo, la, d in ml if m >= thresh and d <= bnd)
        dp = sum(1 for m, lo, la, d in ml if m >= thresh and d > bnd)
        print(
            f"  boundary {bnd} km: shallow {sh/(days/7):5.1f}/wk ({sh/days:4.2f}/day), "
            f"deep {dp/(days/7):5.1f}/wk ({dp/days:4.2f}/day)"
        )
    for sname, skeep in (("shallow", lambda d: d <= 45), ("deep", lambda d: d > 45)):
        sub = [m for m, lo, la, d in ml if skeep(d)]
        print(f"  mainland {sname} Mc = M{mc(sub):.1f}")
