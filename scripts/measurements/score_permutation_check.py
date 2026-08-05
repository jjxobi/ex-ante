"""Is the S-test rejecting because of a cell permutation, or a real misfit?

Decisive test: build a forecast that puts essentially all its rate in ONE known
cell, then place all the observed events in that SAME cell. A correct scorer
cannot reject that. A permuted one will, because the events will land where the
forecast says nothing should happen.

If that passes, the permutation hypothesis is dead and the rejections are a real
property of the model's spatial distribution.
"""
import datetime as dt
import sys

sys.path.insert(0, "src")
from eq import expander, paths, region, score, storage  # noqa: E402

grid = region.load_grid()
cell_ids = sorted(row["cell_id"] for row in grid)
by_id = {row["cell_id"]: row for row in grid}

# Pick a cell in the middle of the region and recover its centre coordinates.
target = cell_ids[len(cell_ids) // 2]
row = by_id[target]
lon = row["lon_deci"] / 10 + 0.05
lat = row["lat_deci"] / 10 + 0.05
print(f"target cell {target} at lon {lon:.2f}, lat {lat:.2f}")
print(f"round trip through cell_id_for: {region.cell_id_for(lon, lat)}")
assert region.cell_id_for(lon, lat) == target, "cell_id_for disagrees with the grid"

start = dt.datetime(2026, 1, 1, tzinfo=dt.timezone.utc)
end = start + dt.timedelta(days=7)

# A forecast almost entirely in the target cell.
rates = {cid: 1e-9 for cid in cell_ids}
rates[target] = 10.0
separable = {
    "grid_hash": region.grid_hash(),
    "cell_ids": cell_ids,
    "b": 1.0,
    "rates": rates,
}
dense = expander.expand(separable)
print(f"dense total: {sum(dense.values):.4f}")

# Ten events, all in the target cell, magnitudes drawn across the range.
events = [
    {
        "publicid": f"synthetic-{i}",
        "origintime": start + dt.timedelta(hours=6 * i),
        "longitude": lon,
        "latitude": lat,
        "magnitude": 3.0 + 0.3 * i,
        "depth": 10.0,
    }
    for i in range(10)
]

result = score.score(dense, events, start, end)
print()
print("=== forecast and events in the SAME cell ===")
print(f"  events used : {result.n_events_used}")
print(f"  expected    : {result.expected_count:.4f}")
print(f"  N quantile  : {result.n_test.quantile}")
print(f"  S quantile  : {result.s_test.quantile}")
print(f"  L quantile  : {result.l_test.quantile}")

s_q = result.s_test.quantile
s_q = s_q[-1] if isinstance(s_q, (tuple, list)) else s_q
print()
if s_q is None:
    print("  S quantile is None, inconclusive")
elif s_q > 0.05:
    print("  PASS: the scorer does NOT reject a forecast that is exactly right.")
    print("  The permutation hypothesis is dead. The rejections on real data")
    print("  are a property of the model, not of the scoring code.")
else:
    print("  FAIL: the scorer rejects a forecast that puts the rate exactly")
    print("  where the events are. That is a cell ordering permutation.")

# Control: same events, but the rate concentrated in a DIFFERENT cell.
other = cell_ids[len(cell_ids) // 4]
rates2 = {cid: 1e-9 for cid in cell_ids}
rates2[other] = 10.0
dense2 = expander.expand({**separable, "rates": rates2})
result2 = score.score(dense2, events, start, end)
s2 = result2.s_test.quantile
s2 = s2[-1] if isinstance(s2, (tuple, list)) else s2
print()
print("=== control: rate in a DIFFERENT cell from the events ===")
print(f"  S quantile  : {s2}")
if s2 is not None and s2 < 0.05:
    print("  Correctly rejects a forecast pointing at the wrong place.")
else:
    print("  WARNING: fails to reject an obviously wrong forecast.")
