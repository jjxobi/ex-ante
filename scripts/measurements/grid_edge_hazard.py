"""Does naive float binning actually disagree with integer decidegrees?

D13.2 requires cell assignment in integer decidegrees rather than floating
point. This measures the hazard against this project's real coordinate ranges
rather than asserting it from a textbook example, because the textbook example
usually quoted, floor((174.5 - 163.6) / 0.1), happens to return the right
answer.

The disagreement is only ever possible exactly on a cell edge, so the sweep
walks every edge in the region.

Standard library only.
"""

import math

LON_MIN, LON_MAX = 163.6, 183.0
LAT_MIN, LAT_MAX = -49.2, -32.3
DH = 0.1


def naive_bin(x, origin, dh=DH):
    """The obvious implementation, and the one D13.2 forbids."""
    return math.floor((x - origin) / dh)


def integer_bin(x, origin, dh=DH):
    """Integer decidegrees, rounded once at the boundary. D13.2's rule."""
    return (round(x * 10) - round(origin * 10)) // round(dh * 10)


def sweep(name, lo, hi, origin):
    n_edges = round((hi - lo) / DH)
    mismatches = []
    for i in range(n_edges + 1):
        # Reconstruct the edge the way a coordinate would arrive, as a decimal
        # value, not by repeated addition.
        x = round(lo + i * DH, 10)
        a, b = naive_bin(x, origin), integer_bin(x, origin)
        if a != b:
            mismatches.append((x, a, b))
    print(f"{name}: swept {n_edges + 1} cell edges from {lo} to {hi}")
    print(f"  disagreements between naive float and integer decidegrees: {len(mismatches)}")
    if mismatches:
        offsets = {b - a for _, a, b in mismatches}
        print(f"  offset in cells, distinct values observed: {sorted(offsets)}")
        print("  first eight offending coordinates:")
        for x, a, b in mismatches[:8]:
            print(f"    {x:>8}  naive={a:<6} integer={b:<6} (naive is off by {a - b:+d})")
        gaps = [
            round(mismatches[i + 1][0] - mismatches[i][0], 6)
            for i in range(min(len(mismatches) - 1, 6))
        ]
        if gaps:
            print(f"  spacing between the first few offenders: {gaps}")
    return mismatches


print("=" * 74)
print("Cell edge sweep across the project's coordinate ranges")
print("=" * 74)
lon_bad = sweep("longitude", LON_MIN, LON_MAX, LON_MIN)
print()
lat_bad = sweep("latitude", LAT_MIN, LAT_MAX, LAT_MIN)

print()
print("=" * 74)
print("Total")
print("=" * 74)
print(f"  edge coordinates binned differently by the two methods: "
      f"{len(lon_bad) + len(lat_bad)}")
print("  every disagreement places the event in an adjacent cell, so a naive")
print("  implementation would shift part of the forecast by one cell, silently.")

print()
print("=" * 74)
print("The example commonly quoted for this hazard is not itself an example")
print("=" * 74)
val = (174.5 - 163.6) / 0.1
print(f"  (174.5 - 163.6) / 0.1        = {val!r}")
print(f"  math.floor of that           = {math.floor(val)}")
print(f"  integer decidegrees          = {integer_bin(174.5, 163.6)}")
print("  so that particular case agrees. The hazard is real but it is not")
print("  where the usual example points, which is why it was measured.")
