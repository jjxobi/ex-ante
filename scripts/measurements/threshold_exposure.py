"""How is magnitude actually represented, and what sits on the M3.0 boundary?

Decisions about threshold inclusivity and about warehouse numeric type depend on
whether GeoNet reports magnitudes rounded to one decimal or at full solver
precision. Those two worlds call for opposite choices, so this measures it
rather than assuming.

Standard library only.
"""

import sys
from collections import Counter
from decimal import Decimal

sys.path.insert(0, "src")
from eq import paths, storage  # noqa: E402

rows = storage.read_parquet(paths.SNAPSHOT_DIR / "catalogue-2026-08-04.parquet")
mags = [r["magnitude"] for r in rows if r["magnitude"] is not None]
print(f"catalogue: {len(mags)} events with a magnitude\n")

print("=" * 74)
print("How many decimal places does GeoNet actually report?")
print("=" * 74)
places = Counter()
for m in mags:
    s = format(Decimal(repr(m)).normalize(), "f")
    places[len(s.split(".")[1]) if "." in s else 0] += 1
for p in sorted(places):
    pct = 100 * places[p] / len(mags)
    print(f"  {p:>2} decimal places: {places[p]:>7}  {pct:>5.1f}%  {'#' * int(pct / 2)}")

one_dp = sum(v for k, v in places.items() if k <= 1)
print(f"\n  reported at one decimal place or fewer: {one_dp} ({100*one_dp/len(mags):.2f}%)")
print(f"  reported at higher precision          : {len(mags)-one_dp} "
      f"({100*(len(mags)-one_dp)/len(mags):.2f}%)")

print()
print("=" * 74)
print("What sits exactly on, or very near, the M3.0 boundary?")
print("=" * 74)
exact = sum(1 for m in mags if m == 3.0)
print(f"  magnitude exactly == 3.0 (float equality): {exact}")
for eps, label in ((1e-12, "1e-12"), (1e-9, "1e-9"), (1e-6, "1e-6"), (1e-3, "0.001"), (0.005, "0.005")):
    n = sum(1 for m in mags if abs(m - 3.0) <= eps)
    print(f"  within +/- {label:<7}: {n}")

print("\n  the ten events closest to the M3.0 boundary from above:")
for m in sorted((m for m in mags if m >= 3.0))[:10]:
    print(f"    {m!r}")
print("\n  the five closest from below (these are outside the target set):")
for m in sorted((m for m in mags if m < 3.0), reverse=True)[:5]:
    print(f"    {m!r}")

print()
print("=" * 74)
print("What would rounding to 2 decimal places do to the target set?")
print("=" * 74)
in_now = sum(1 for m in mags if m >= 3.0)
in_rounded = sum(1 for m in mags if round(m, 2) >= 3.0)
print(f"  events with magnitude >= 3.0 as reported      : {in_now}")
print(f"  events with round(magnitude, 2) >= 3.0        : {in_rounded}")
print(f"  net change from rounding to 2 dp              : {in_rounded - in_now:+}")
promoted = [m for m in mags if m < 3.0 and round(m, 2) >= 3.0]
print(f"  events BELOW 3.0 promoted INTO the set by rounding: {len(promoted)}")
for m in sorted(promoted, reverse=True)[:5]:
    print(f"    {m!r} rounds to {round(m, 2)}")

print()
print("=" * 74)
print("Same question for one decimal place, the coarser option")
print("=" * 74)
in_1dp = sum(1 for m in mags if round(m, 1) >= 3.0)
promoted_1 = [m for m in mags if m < 3.0 and round(m, 1) >= 3.0]
print(f"  events with round(magnitude, 1) >= 3.0        : {in_1dp}")
print(f"  net change                                    : {in_1dp - in_now:+}")
print(f"  events promoted into the set                  : {len(promoted_1)}")
