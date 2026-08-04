"""Is Kermadec completeness stable over time? Decides whether incompleteness cancels."""
import csv
import os
from collections import Counter

CACHE = os.path.dirname(os.path.abspath(__file__))


def lon360(v):
    v = float(v)
    return v + 360 if v < 0 else v


def mc(mags):
    if len(mags) < 150:
        return None
    b = Counter(round(m, 1) for m in mags)
    return max(range(15, 50), key=lambda i: b.get(i / 10, 0)) / 10


print("Mc and M>=3.5 rate by year and region\n")
print(f"{'year':<6} {'region':<16} {'n':>7} {'Mc':>6} {'M>=3.5':>8} {'b-slope proxy':>14}")
print("-" * 62)

for yr in (2005, 2010, 2015, 2020, 2025):
    f = os.path.join(CACHE, f"cat_1.5_{yr}_{yr+1}.csv")
    if not os.path.exists(f):
        continue
    with open(f, encoding="utf-8") as fh:
        rows = list(csv.DictReader(fh))
    evs = [
        (float(r["magnitude"]), lon360(r["longitude"]), float(r["latitude"]))
        for r in rows
        if r["magnitude"]
    ]
    for label, keep in (
        ("mainland", lambda lo, la: not (lo >= 177 and la >= -37)),
        ("kermadec", lambda lo, la: lo >= 177 and la >= -37),
        ("far kermadec", lambda lo, la: lo >= 179 and la >= -35),
    ):
        sub = [m for m, lo, la in evs if keep(lo, la)]
        v = mc(sub)
        n35 = sum(1 for m in sub if m >= 3.5)
        n45 = sum(1 for m in sub if m >= 4.5)
        ratio = f"{n35/n45:.2f}" if n45 else "n/a"
        print(
            f"{yr:<6} {label:<16} {len(sub):>7} {('M'+format(v,'.1f')) if v else '   n/a':>6}"
            f" {n35:>8} {ratio:>14}"
        )
    print()

print("Interpretation guide:")
print("  A stable Mc means incompleteness cancels between fit and evaluation.")
print("  The M>=3.5 / M>=4.5 ratio is a completeness-sensitive proxy: under a")
print("  stable Gutenberg-Richter b near 1 it should sit near 10, and falls")
print("  toward 1 as the catalogue loses the smaller events.")
