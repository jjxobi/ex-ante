"""Compare data supply under fit-window truncation versus per-cell exposure."""
import csv
import math
import os
from collections import Counter, defaultdict

C = os.path.dirname(os.path.abspath(__file__))


def lon360(v):
    v = float(v)
    return v + 360 if v < 0 else v


def mc(m, n=150):
    if len(m) < n:
        return None
    b = Counter(round(x, 1) for x in m)
    return max(range(15, 50), key=lambda i: b.get(i / 10, 0)) / 10


by = {}
for y in range(2005, 2026):
    with open(os.path.join(C, f"cat_1.5_{y}_{y+1}.csv"), encoding="utf-8") as f:
        by[y] = [
            (float(r["magnitude"]), lon360(r["longitude"]), float(r["latitude"]), float(r["depth"]))
            for r in csv.DictReader(f)
            if r["magnitude"] and r["depth"]
        ]

sel = defaultdict(list)
for y in range(2021, 2026):
    for m, lo, la, d in by[y]:
        sel[(math.floor(lo), math.floor(la))].append(m)
REG = {k for k, v in sel.items() if (mc(v) or 99) <= 2.6}

complete_from = {}
for k in REG:
    for Y in range(2005, 2026):
        pool = [
            m for y in range(Y, 2026) for m, lo, la, d in by[y] if (math.floor(lo), math.floor(la)) == k
        ]
        v = mc(pool)
        if v is not None and v <= 2.6:
            complete_from[k] = Y
            break

tr_s = tr_d = ex_s = ex_d = 0
for y in range(2005, 2026):
    for m, lo, la, d in by[y]:
        k = (math.floor(lo), math.floor(la))
        if k not in REG or m < 3.0:
            continue
        if y >= 2019:
            tr_s, tr_d = (tr_s + 1, tr_d) if d <= 41 else (tr_s, tr_d + 1)
        if y >= complete_from[k]:
            ex_s, ex_d = (ex_s + 1, ex_d) if d <= 41 else (ex_s, ex_d + 1)

expo = sum(2026 - v for v in complete_from.values())
print(f"TRUNCATION to 2019 : shallow {tr_s}, deep {tr_d}, total {tr_s + tr_d}")
print(f"PER-CELL EXPOSURE  : shallow {ex_s}, deep {ex_d}, total {ex_s + ex_d}")
print(f"exposure gain      : {(ex_s + ex_d) / (tr_s + tr_d):.2f}x more events")
print(f"cell-years         : exposure {expo:.0f} vs truncation {len(REG) * 7}")
print("\ncells with exposure under 15 years (the only ones needing correction):")
for k, v in sorted(complete_from.items()):
    if 2026 - v < 15:
        print(f"    ({k[0]},{k[1]})  complete from {v}  exposure {2026 - v} yr")
