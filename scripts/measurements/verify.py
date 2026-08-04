"""Verify review challenges 4, 5 and 6 against real data before amending the spec."""
import csv
import datetime as dt
import os
import time
import urllib.request
from collections import Counter

BBOX = "163.60840,-49.18170,182.98828,-32.28713"
CACHE = os.path.dirname(os.path.abspath(__file__))


def fetch(minmag, start, end, tag=""):
    name = os.path.join(CACHE, f"cat{tag}_{minmag}_{start}_{end}.csv")
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
                print(f"    retry {attempt+1} ({exc.__class__.__name__}), {wait}s")
                time.sleep(wait)
        else:
            raise RuntimeError(f"gave up on {start}-{end}")
        with open(name, "w", encoding="utf-8", newline="") as f:
            f.write(data)
        time.sleep(3)
    with open(name, encoding="utf-8") as f:
        return list(csv.DictReader(f))


# ---------------------------------------------------------------- challenge 6
print("=" * 70)
print("CHALLENGE 6: does the within-variance criterion return a sane boundary?")
print("=" * 70)

rows = []
for f in sorted(os.listdir(CACHE)):
    if f.startswith("cat_3.5_"):
        with open(os.path.join(CACHE, f), encoding="utf-8") as fh:
            rows += list(csv.DictReader(fh))
free = sorted(float(r["depth"]) for r in rows if r["depthtype"] != "operator assigned")
print(f"free-depth events: {len(free)}  (min {free[0]:.1f}, max {free[-1]:.1f})")


def variance(xs):
    if len(xs) < 2:
        return 0.0
    m = sum(xs) / len(xs)
    return sum((x - m) ** 2 for x in xs) / len(xs)


print("\n(a) Otsu / within-stratum variance, as written in the spec (30-90 km):")
best = None
curve = []
for b in range(30, 91):
    lo = [d for d in free if d <= b]
    hi = [d for d in free if d > b]
    if not lo or not hi:
        continue
    w = (len(lo) * variance(lo) + len(hi) * variance(hi)) / len(free)
    curve.append((b, w))
    if best is None or w < best[1]:
        best = (b, w)
for b, w in curve:
    if b % 5 == 0:
        print(f"    {b:3d} km  within-var {w:12.1f}")
print(f"  -> minimum at {best[0]} km")
print(f"  -> at range edges: 30km={curve[0][1]:.1f}, 90km={curve[-1][1]:.1f}")
mono = all(curve[i][1] <= curve[i + 1][1] for i in range(len(curve) - 1))
print(f"  -> monotonically increasing across the whole range? {mono}")

print("\n(b) unconstrained over 5-300 km (is 30-90 hiding the real optimum?):")
b2 = None
for b in range(5, 301):
    lo = [d for d in free if d <= b]
    hi = [d for d in free if d > b]
    if not lo or not hi:
        continue
    w = (len(lo) * variance(lo) + len(hi) * variance(hi)) / len(free)
    if b2 is None or w < b2[1]:
        b2 = (b, w)
print(f"  -> unconstrained minimum at {b2[0]} km")

print("\n(c) KDE-style minimum of the smoothed log-depth density (the alternative):")
import math

logs = [math.log10(d) for d in free if d > 0]
h = 0.06
grid = [1.0 + i * 0.01 for i in range(101)]  # log10 depth 1.0 -> 2.0 = 10 -> 100 km
dens = []
for g in grid:
    s = sum(math.exp(-0.5 * ((g - x) / h) ** 2) for x in logs)
    dens.append(s / (len(logs) * h * math.sqrt(2 * math.pi)))
mind, mindg = None, None
for i in range(5, len(grid) - 5):
    if dens[i] < dens[i - 1] and dens[i] < dens[i + 1]:
        if mind is None or dens[i] < mind:
            mind, mindg = dens[i], grid[i]
if mindg:
    print(f"  -> local density minimum at {10**mindg:.1f} km")
else:
    print("  -> no interior local minimum found in 10-100 km")

# ---------------------------------------------------------------- challenge 5
print()
print("=" * 70)
print("CHALLENGE 5: is revision lag a decay curve or a monthly review batch?")
print("=" * 70)

cat25 = fetch(3.0, 2025, 2026)
print(f"2025 M>=3.0 events: {len(cat25)}")
mods = []
for r in cat25:
    o = dt.datetime.fromisoformat(r["origintime"].replace("Z", "+00:00"))
    m = dt.datetime.fromisoformat(r["modificationtime"].replace("Z", "+00:00"))
    mods.append((o, m, (m - o).total_seconds() / 86400))

print("\n(a) modification DATE histogram (do revisions land on specific days?):")
by_date = Counter(m.date() for _, m, _ in mods)
top = by_date.most_common(12)
print("  busiest modification dates:")
for d, n in top:
    print(f"    {d}  {n:4d}  {'#' * (n // 3)}")
print(f"  distinct modification dates: {len(by_date)} over 2025 origins")

print("\n(b) modification day-of-month distribution:")
dom = Counter(m.day for _, m, _ in mods)
for d in range(1, 32):
    n = dom.get(d, 0)
    print(f"    day {d:2d}  {n:4d}  {'#' * (n // 4)}")

print("\n(c) lag distribution shape, 2-day bins (decay or pile against a wall?):")
lags = sorted(l for _, _, l in mods)
for lo in range(0, 46, 2):
    n = sum(1 for l in lags if lo <= l < lo + 2)
    print(f"    {lo:3d}-{lo+2:3d} d  {n:4d}  {'#' * (n // 5)}")
print(f"    45+   d  {sum(1 for l in lags if l >= 45):4d}")
for p in (50, 75, 90, 95, 99, 99.9):
    print(f"    p{p}: {lags[min(int(len(lags)*p/100), len(lags)-1)]:.2f} d")

# ---------------------------------------------------------------- challenge 4
print()
print("=" * 70)
print("CHALLENGE 4: is Mc stationary across the 2005-2025 fitting window?")
print("=" * 70)
for yr in (2005, 2010, 2015, 2020, 2025):
    c = fetch(1.5, yr, yr + 1)
    mags = [float(r["magnitude"]) for r in c if r["magnitude"]]
    if not mags:
        print(f"  {yr}: no data")
        continue
    bins = Counter(round(m, 1) for m in mags)
    modal = max(range(15, 45), key=lambda i: bins.get(i / 10, 0))
    n35 = sum(1 for m in mags if m >= 3.5)
    print(f"  {yr}: n={len(mags):6d}  modal bin (Mc) = M{modal/10:.1f}  M>=3.5 count = {n35}")
