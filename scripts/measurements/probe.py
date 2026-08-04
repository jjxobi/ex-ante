"""Exploratory measurement for the frozen-decision brainstorm. Not project code."""
import csv
import os
import time
import urllib.request

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
                print(f"  retry {attempt+1} after {exc.__class__.__name__}, sleeping {wait}s")
                time.sleep(wait)
        else:
            raise RuntimeError(f"giving up on {start}-{end}")
        with open(name, "w", encoding="utf-8", newline="") as f:
            f.write(data)
        time.sleep(3)
    with open(name, encoding="utf-8") as f:
        return list(csv.DictReader(f))


def rows(minmag, spans):
    out = []
    for a, b in spans:
        got = fetch(minmag, a, b)
        print(f"  fetched {minmag} {a}-{b}: {len(got)}")
        out += got
    return out


print("=== depth histogram, M>=3.5, 2005-2025 ===")
deep_cat = rows(3.5, [(2005, 2010), (2010, 2015), (2015, 2020), (2020, 2025), (2025, 2026)])
depths = [float(r["depth"]) for r in deep_cat if r["depth"]]
print(f"  n = {len(depths)}")
for lo in range(0, 120, 5):
    n = sum(1 for d in depths if lo <= d < lo + 5)
    print(f"  {lo:3d}-{lo+5:3d} km | {'#' * (n // 40):<40} {n}")
n = sum(1 for d in depths if d >= 120)
print(f"  120+   km | {'#' * (n // 40):<40} {n}")

print()
print("=== per-stratum FMD, 2023-2026, M>=1.5 ===")
fmd_cat = rows(1.5, [(2023, 2024), (2024, 2025), (2025, 2026)])
evs = [(float(r["magnitude"]), float(r["depth"])) for r in fmd_cat if r["depth"] and r["magnitude"]]
print(f"  n = {len(evs)}")

for label, keep in (("SHALLOW <=40km", lambda d: d <= 40), ("DEEP >40km", lambda d: d > 40)):
    sub = [m for m, d in evs if keep(d)]
    print(f"\n  --- {label}: n={len(sub)} ---")
    print("   bin   count   (maximum-curvature Mc = modal bin)")
    best_n, best_b = -1, None
    for i in range(10, 50):
        lo = i / 10
        c = sum(1 for m in sub if lo <= m < lo + 0.1)
        if c > best_n:
            best_n, best_b = c, lo
        if lo <= 4.0:
            print(f"   {lo:.1f}  {c:6d}  {'#' * (c // 20)}")
    print(f"   -> modal bin (Mc estimate, maximum curvature) = M{best_b:.1f}")
    for t in (3.0, 3.5, 4.0):
        c = sum(1 for m in sub if m >= t)
        print(f"   M>={t}: {c} over 3yr = {c/(3*365):.2f}/day, {c/(3*52):.1f}/week")
