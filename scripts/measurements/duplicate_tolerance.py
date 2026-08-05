"""Calibrate the duplicate-detection rule for D4a.

Documenting the 2018p914028 and 2018p914029 pair catches that pair. It does not
catch the next one, and duplicates feed straight into the event count the N-test
consumes, on both the fitting side and the evaluation side.

The hard part is that genuine doublets are real and not rare: two distinct
earthquakes seconds apart in the same place are physically ordinary in an
aftershock sequence. A rule tuned only to catch duplicates would eat those.

So this measures the joint distribution of separations between close pairs, in
time, space and magnitude, and asks whether true duplicates separate cleanly
from genuine doublets. If they do not, that is the finding, and it gets
published rather than papered over with a chosen number.

Standard library only, so a reader can check this without building anything.
"""

import math
import sys
from collections import Counter

sys.path.insert(0, "src")
from eq import paths, storage  # noqa: E402

WINDOW_SECONDS = 300.0
EARTH_RADIUS_KM = 6371.0


def haversine_km(lat1, lon1, lat2, lon2):
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp = math.radians(lat2 - lat1)
    dl = math.radians(lon2 - lon1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * EARTH_RADIUS_KM * math.asin(math.sqrt(a))


def main():
    rows = storage.read_parquet(paths.SNAPSHOT_DIR / "catalogue-2026-08-04.parquet")
    events = sorted(
        (
            (r["origintime"], r["publicid"], r["latitude"], r["longitude"], r["magnitude"], r["depth"])
            for r in rows
            if r["origintime"] is not None
        ),
        key=lambda e: e[0],
    )
    print(f"catalogue: {len(events)} events\n")

    # Windowed forward scan. Any true duplicate is adjacent in time, so a
    # 300 second look-ahead is generous and keeps this O(n) in practice.
    pairs = []
    for i, a in enumerate(events):
        for j in range(i + 1, len(events)):
            b = events[j]
            dt = (b[0] - a[0]).total_seconds()
            if dt > WINDOW_SECONDS:
                break
            pairs.append(
                (
                    dt,
                    haversine_km(a[2], a[3], b[2], b[3]),
                    abs(a[4] - b[4]),
                    abs(a[5] - b[5]),
                    a[1],
                    b[1],
                )
            )
    print(f"pairs within {WINDOW_SECONDS:.0f} s of each other: {len(pairs)}\n")

    print("=" * 74)
    print("Where do close pairs sit? Counts by time and distance separation")
    print("=" * 74)
    t_bins = [(0, 1), (1, 5), (5, 15), (15, 60), (60, 300)]
    d_bins = [(0, 0.5), (0.5, 2), (2, 10), (10, 50), (50, 10000)]
    print(f"{'dt (s)':<12}" + "".join(f"{f'{lo}-{hi} km':>14}" for lo, hi in d_bins))
    for tlo, thi in t_bins:
        row = f"{f'{tlo}-{thi}':<12}"
        for dlo, dhi in d_bins:
            n = sum(1 for p in pairs if tlo <= p[0] < thi and dlo <= p[1] < dhi)
            row += f"{n:>14}"
        print(row)

    print()
    print("=" * 74)
    print("The candidate duplicate corner: dt < 1 s AND distance < 0.5 km")
    print("=" * 74)
    corner = [p for p in pairs if p[0] < 1.0 and p[1] < 0.5]
    print(f"pairs in the corner: {len(corner)}")
    for dt, dist, dm, dd, ida, idb in sorted(corner):
        print(
            f"  {ida} / {idb}  dt={dt:.3f}s  dist={dist*1000:.1f}m  "
            f"dmag={dm:.6f}  ddepth={dd:.3f}km"
        )

    print()
    print("=" * 74)
    print("Nearest genuine neighbours: the tightest pairs NOT in that corner")
    print("=" * 74)
    rest = sorted(p for p in pairs if not (p[0] < 1.0 and p[1] < 0.5))
    print("tightest 12 by time separation:")
    for dt, dist, dm, dd, ida, idb in rest[:12]:
        print(
            f"  {ida} / {idb}  dt={dt:.3f}s  dist={dist:.2f}km  "
            f"dmag={dm:.3f}  ddepth={dd:.2f}km"
        )

    print()
    print("=" * 74)
    print("Do the two populations separate?")
    print("=" * 74)
    if corner:
        print(f"  duplicate corner: n={len(corner)}")
        print(f"    max dt        : {max(p[0] for p in corner):.3f} s")
        print(f"    max distance  : {max(p[1] for p in corner)*1000:.1f} m")
        print(f"    max dmag      : {max(p[2] for p in corner):.6f}")
        print(f"    max ddepth    : {max(p[3] for p in corner):.6f} km")
    if rest:
        print(f"  everything else: n={len(rest)}")
        print(f"    min dt        : {min(p[0] for p in rest):.3f} s")
        print(f"    min distance  : {min(p[1] for p in rest):.3f} km")
        gap_t = min(p[0] for p in rest) - (max(p[0] for p in corner) if corner else 0)
        gap_d = min(p[1] for p in rest) - (max(p[1] for p in corner) if corner else 0)
        print(f"\n  separation gap in time    : {gap_t:.3f} s")
        print(f"  separation gap in distance: {gap_d:.3f} km")

    print()
    print("=" * 74)
    print("Doublet frequency, for context on what a loose rule would eat")
    print("=" * 74)
    for thi in (5, 15, 60, 300):
        n = sum(1 for p in pairs if p[0] < thi and not (p[0] < 1.0 and p[1] < 0.5))
        print(f"  genuine pairs within {thi:>3} s of each other: {n}")
    c = Counter(round(p[1]) for p in pairs if p[0] < 60)
    print(f"\n  distance spread of sub-60s pairs (km, rounded): "
          f"{sorted(c)[:8]} ... max {max(c)}")


if __name__ == "__main__":
    main()
