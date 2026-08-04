"""Both selection checks failed. Disentangle the cause, then price the fix.

Per the agreed rule the lever is the inclusion criterion, not the magnitude
threshold, because tightening the region preserves the logic of the rule while
raising the threshold re-imports the problem the region cut removed.
"""
import csv
import math
import os
from collections import Counter, defaultdict

CACHE = os.path.dirname(os.path.abspath(__file__))
RECENT = [2021, 2022, 2023, 2024, 2025]
OLD = [2005, 2010, 2015, 2020]


def lon360(v):
    v = float(v)
    return v + 360 if v < 0 else v


def load(years, minmag="1.5"):
    rows = []
    for y in years:
        p = os.path.join(CACHE, f"cat_{minmag}_{y}_{y+1}.csv")
        if os.path.exists(p):
            with open(p, encoding="utf-8") as fh:
                rows += list(csv.DictReader(fh))
    return rows


def cellify(rows):
    d = defaultdict(list)
    for r in rows:
        if not r["magnitude"]:
            continue
        k = (math.floor(lon360(r["longitude"])), math.floor(float(r["latitude"])))
        d[k].append(float(r["magnitude"]))
    return d


def mc(mags, minn=150):
    if len(mags) < minn:
        return None
    b = Counter(round(m, 1) for m in mags)
    return max(range(15, 50), key=lambda i: b.get(i / 10, 0)) / 10


rec, old = cellify(load(RECENT)), cellify(load(OLD))
mc_rec = {k: mc(v) for k, v in rec.items()}
mc_old = {k: mc(v) for k, v in old.items()}

print("=" * 74)
print("Is the +0.21 shift selection bias, or just a worse network in 2005-2020?")
print("=" * 74)
both = [k for k in mc_rec if mc_rec[k] is not None and mc_old.get(k) is not None]
sel = [k for k in both if mc_rec[k] <= 2.6]
non = [k for k in both if mc_rec[k] > 2.6]
for label, ks in (("SELECTED (Mc_recent <= 2.6)", sel), ("NOT selected (Mc_recent > 2.6)", non)):
    if not ks:
        continue
    sh = [mc_old[k] - mc_rec[k] for k in ks]
    print(f"  {label:<32} n={len(ks):3d}  mean shift {sum(sh)/len(sh):+.2f}")
print("\n  A temporal effect alone moves both groups equally. Selection bias moves")
print("  the SELECTED group up by more, because it was chosen on low estimates.")
if sel and non:
    a = sum(mc_old[k] - mc_rec[k] for k in sel) / len(sel)
    b = sum(mc_old[k] - mc_rec[k] for k in non) / len(non)
    print(f"\n  differential (selected minus not selected): {a-b:+.2f} magnitude units")
    print(f"  -> roughly {a-b:+.2f} attributable to selection, {b:+.2f} to the era")

# ---------------------------------------------------------------- candidates
print()
print("=" * 74)
print("Candidate inclusion rules, all keeping the M3.0 threshold frozen")
print("=" * 74)

dep_rows = []
for a, b in ((2005, 2010), (2010, 2015), (2015, 2020), (2020, 2025), (2025, 2026)):
    p = os.path.join(CACHE, f"cat_3.0_{a}_{b}.csv")
    if os.path.exists(p):
        with open(p, encoding="utf-8") as fh:
            dep_rows += list(csv.DictReader(fh))

recent_evs = [
    (float(r["magnitude"]), lon360(r["longitude"]), float(r["latitude"]), float(r["depth"]))
    for r in load(RECENT)
    if r["magnitude"] and r["depth"]
]
days = 5 * 365


def price(label, keep):
    ks = {k for k in mc_rec if mc_rec[k] is not None and keep(k)}
    if not ks:
        print(f"  {label}: empty")
        return
    worst_r = max(mc_rec[k] for k in ks)
    measurable_old = [mc_old[k] for k in ks if mc_old.get(k) is not None]
    worst_o = max(measurable_old) if measurable_old else None
    over = sum(1 for k in ks if (mc_old.get(k) or 0) > 2.6)
    ev = [e for e in recent_evs if (math.floor(e[1]), math.floor(e[2])) in ks]
    sh = sum(1 for m, _, _, d in ev if m >= 3.0 and d <= 41)
    dp = sum(1 for m, _, _, d in ev if m >= 3.0 and d > 41)
    print(f"  {label}")
    print(
        f"      cells {len(ks):3d} | worst Mc recent {worst_r:.1f} | worst Mc held-out "
        f"{worst_o if worst_o is None else format(worst_o,'.1f')} | cells over ceiling on held-out: {over}"
    )
    print(
        f"      shallow {sh/(days/7):5.1f}/wk ({sh/days:4.2f}/day) | "
        f"deep {dp/(days/7):5.1f}/wk ({dp/days:4.2f}/day)"
    )


price("A. current frozen rule: Mc_recent <= 2.6, n >= 150", lambda k: mc_rec[k] <= 2.6)
price("B. tighter ceiling: Mc_recent <= 2.4", lambda k: mc_rec[k] <= 2.4)
price(
    "C. higher count floor: Mc_recent <= 2.6, n >= 400",
    lambda k: mc_rec[k] <= 2.6 and len(rec[k]) >= 400,
)
price(
    "D. BOTH WINDOWS: Mc <= 2.6 in recent AND held-out",
    lambda k: mc_rec[k] <= 2.6 and mc_old.get(k) is not None and mc_old[k] <= 2.6,
)
price(
    "E. both windows AND n >= 400 in each",
    lambda k: (
        mc_rec[k] <= 2.6
        and len(rec[k]) >= 400
        and mc_old.get(k) is not None
        and mc_old[k] <= 2.6
        and len(old[k]) >= 400
    ),
)

print("\nUnder rule D, the cells dropped from the current region:")
cur = {k for k in mc_rec if mc_rec[k] is not None and mc_rec[k] <= 2.6}
d_keep = {k for k in cur if mc_old.get(k) is not None and mc_old[k] <= 2.6}
for k in sorted(cur - d_keep):
    o = mc_old.get(k)
    print(
        f"    ({k[0]},{k[1]})  n_recent={len(rec[k]):5d}  Mc_recent={mc_rec[k]:.1f}  "
        f"Mc_heldout={'n/a' if o is None else format(o,'.1f')}"
    )
