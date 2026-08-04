"""Check fixed-depth contamination before choosing the stratum boundary."""
import csv
import os
from collections import Counter

CACHE = os.path.dirname(os.path.abspath(__file__))

rows = []
for f in sorted(os.listdir(CACHE)):
    if f.startswith("cat_3.5_"):
        with open(os.path.join(CACHE, f), encoding="utf-8") as fh:
            rows += list(csv.DictReader(fh))
print(f"M>=3.5 2005-2026: n={len(rows)}\n")

print("=== depthtype values ===")
for k, v in Counter(r["depthtype"] for r in rows).most_common():
    print(f"  {k or '(empty)':<20} {v:6d}  {100*v/len(rows):5.1f}%")

print("\n=== exact depths that repeat most (fixed-depth signature) ===")
for d, n in Counter(r["depth"] for r in rows).most_common(8):
    print(f"  {float(d):8.3f} km  x{n}")

free = [float(r["depth"]) for r in rows if r["depthtype"] != "operator assigned"]
assigned = [float(r["depth"]) for r in rows if r["depthtype"] == "operator assigned"]
print(f"\nfree-depth n={len(free)}   assigned n={len(assigned)}")

print("\n=== histogram, FREE DEPTHS ONLY, 10km bins to 300 ===")
for lo in range(0, 300, 10):
    n = sum(1 for d in free if lo <= d < lo + 10)
    print(f"  {lo:3d}-{lo+10:3d} km | {'#' * (n // 30):<45} {n}")
n = sum(1 for d in free if d >= 300)
print(f"  300+    km | {'#' * (n // 30):<45} {n}")

print("\n=== candidate boundaries: free-depth split ===")
for b in (40, 50, 60, 70, 75, 80):
    s = sum(1 for d in free if d <= b)
    dp = len(free) - s
    print(f"  {b:3d} km -> shallow {s:6d} ({100*s/len(free):4.1f}%)  deep {dp:6d} ({100*dp/len(free):4.1f}%)")

print("\n=== where do assigned depths sit? ===")
for lo in (0, 10, 20, 30, 40, 60, 100, 150):
    hi = {0: 10, 10: 20, 20: 30, 30: 40, 40: 60, 60: 100, 100: 150, 150: 10000}[lo]
    n = sum(1 for d in assigned if lo <= d < hi)
    print(f"  {lo}-{hi} km: {n}")
