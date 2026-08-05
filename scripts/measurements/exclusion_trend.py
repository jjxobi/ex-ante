"""Is the share of national seismicity excluded by D1 constant, or drifting?

D1 states the region cut removes 41 percent of the national M3.0 and above
event count. That figure was measured over the 2021 to 2026 reference window.
This checks whether it holds over the whole catalogue, because a frozen
decision whose headline number silently drifts is worse than one that states
its own range.

Standard library plus the project's own region module.
"""

import sys
from collections import Counter

sys.path.insert(0, "src")
from eq import paths, region, storage  # noqa: E402

rows = storage.read_parquet(paths.SNAPSHOT_DIR / "catalogue-2026-08-05.parquet")
print(f"catalogue: {len(rows):,} events, M3.0 and above\n")

in_region = 0
out_region = 0
by_year_in = Counter()
by_year_out = Counter()

for r in rows:
    cell = region.cell_id_for(r["longitude"], r["latitude"])
    year = r["origintime"].year
    if cell is None:
        out_region += 1
        by_year_out[year] += 1
    else:
        in_region += 1
        by_year_in[year] += 1

total = in_region + out_region
print(f"whole catalogue in region : {in_region:,} ({100*in_region/total:.2f}%)")
print(f"whole catalogue out       : {out_region:,} ({100*out_region/total:.2f}%)")
print()

print("=" * 62)
print("Excluded share by year. D1's headline is 41 percent.")
print("=" * 62)
print(f"{'year':<6} {'in':>8} {'out':>8} {'excluded %':>12}")
for year in sorted(set(by_year_in) | set(by_year_out)):
    i, o = by_year_in[year], by_year_out[year]
    if i + o == 0:
        continue
    print(f"{year:<6} {i:>8} {o:>8} {100*o/(i+o):>11.1f}%")

ref_in = sum(by_year_in[y] for y in range(2021, 2026))
ref_out = sum(by_year_out[y] for y in range(2021, 2026))
print()
print("=" * 62)
print("D1's own reference window, 2021 to 2026")
print("=" * 62)
print(f"  in region : {ref_in:,}")
print(f"  out       : {ref_out:,}")
print(f"  excluded  : {100*ref_out/(ref_in+ref_out):.1f}%")
print("\n  D1 quotes 41 percent, measured on this window.")
