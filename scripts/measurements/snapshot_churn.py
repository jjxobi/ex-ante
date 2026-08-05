"""The first real snapshot diff. Measures one day of actual GeoNet churn.

This is the first time the revision-diff path has run against two genuinely
different snapshots, and the first observation of whether withdrawals happen at
all, which was previously unmeasured at n=0.
"""

import datetime as dt
import sys

sys.path.insert(0, "src")
from eq import paths, revisions, storage  # noqa: E402

prev = paths.SNAPSHOT_DIR / "catalogue-2026-08-04.parquet"
curr = paths.SNAPSHOT_DIR / "catalogue-2026-08-05.parquet"

a = storage.read_parquet(prev)
b = storage.read_parquet(curr)
print(f"2026-08-04 snapshot: {len(a):,} events")
print(f"2026-08-05 snapshot: {len(b):,} events")
print(f"net change         : {len(b) - len(a):+,}")

rows = revisions.diff_catalogues(a, b, dt.date(2026, 8, 5))
print(f"\ndiff rows: {len(rows)}")

from collections import Counter  # noqa: E402

kinds = Counter(r["change_kind"] for r in rows)
for kind, n in kinds.most_common():
    print(f"  {kind:<12} {n}")

fields = Counter(r["field"] for r in rows if r["change_kind"] == "revised")
if fields:
    print("\nrevised fields:")
    for f, n in fields.most_common():
        print(f"  {f:<18} {n}")

withdrawn = [r for r in rows if r["change_kind"] == "withdrawn"]
print(f"\nWITHDRAWALS OBSERVED: {len(withdrawn)}")
for r in withdrawn[:20]:
    print(f"  {r['publicid']}")

if withdrawn:
    ids = {r["publicid"] for r in withdrawn}
    gone = [e for e in a if e["publicid"] in ids]
    print("\ndetail on withdrawn events:")
    for e in gone[:20]:
        print(
            f"  {e['publicid']}  {e['origintime']}  M{e['magnitude']:.2f}  "
            f"depth {e['depth']:.1f}  status={e['evaluationstatus']!r}  "
            f"mode={e['evaluationmode']!r}"
        )

new = [r for r in rows if r["change_kind"] == "new"]
print(f"\nnew events: {len(new)}")

print("\n" + "=" * 70)
print("Withdrawal rate, for the guard threshold")
print("=" * 70)
print(f"  withdrawn in one day        : {len(withdrawn)}")
print(f"  as a share of the catalogue : {100*len(withdrawn)/len(a):.4f}%")
print(f"  net size change             : {len(b) - len(a):+,} events")
print("  a partial ingest would present as a large withdrawal count, which is")
print("  why the two must not be conflated.")
