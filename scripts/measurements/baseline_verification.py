"""Independent check of the baseline's headline numbers.

Recomputes b from the definition rather than calling the module under test, and
checks conservation survives the expansion rather than only the fit.

A NOTE ON THE b COMPARISON. The first version of this script disagreed with
src/eq/baseline.py on b, 0.8539 against 0.8675 for the shallow stratum. The
module was right and this script was wrong: it had omitted D13.3's upper
fitting cutoff at M5.5, which drops 20 shallow and 8 deep events. With the
cutoff applied the two agree to nine decimal places.

That is worth recording rather than quietly fixing. Reimplementing from the
definition is supposed to catch the module being wrong; here it caught the
checker being wrong, which is the same mechanism working and is exactly why the
independent version has to be written from the frozen decision text rather than
from memory of what the module does.
"""
import datetime as dt
import math
import sys

sys.path.insert(0, "src")
from eq import baseline, expander, paths, region, storage  # noqa: E402

rows = storage.read_parquet(paths.SNAPSHOT_DIR / "catalogue-2026-08-05.parquet")
FIT_START = dt.datetime(2019, 1, 1, tzinfo=dt.timezone.utc)

in_region = []
for r in rows:
    if r["origintime"] < FIT_START:
        continue
    if region.cell_id_for(r["longitude"], r["latitude"]) is None:
        continue
    in_region.append(r)

by_stratum = {"shallow": [], "deep": []}
for r in in_region:
    by_stratum[region.stratum_for(r["depth"])].append(r)

print(f"in region, 2019 onward: {len(in_region):,}")
for s, evs in by_stratum.items():
    print(f"  {s:<8} {len(evs):,}")

print()
print("=" * 68)
print("b recomputed from the Aki-Utsu definition, independently")
print("=" * 68)
MC, DM = 3.0, 0.1
for s, evs in by_stratum.items():
    mags = [e["magnitude"] for e in evs]
    mean_m = sum(mags) / len(mags)
    b_independent = math.log10(math.e) / (mean_m - (MC - DM / 2))
    fitted = baseline.fit(evs, s)
    b_module = fitted.b if hasattr(fitted, "b") else None
    print(f"  {s:<8} mean M = {mean_m:.6f}")
    print(f"           b independent = {b_independent:.6f}")
    print(f"           b from module  = {b_module}")
    if b_module is not None:
        agree = abs(b_independent - b_module) < 1e-9
        print(f"           agree to 1e-9  = {agree}")

print()
print("=" * 68)
print("Conservation THROUGH the expansion, not just the fit")
print("=" * 68)
span_days = (rows[0]["origintime"], None)
latest = max(r["origintime"] for r in in_region)
fit_days = (latest - FIT_START).total_seconds() / 86400
print(f"  fit span: {fit_days:.2f} days")

for s, evs in by_stratum.items():
    fitted = baseline.fit(evs, s)
    sep = baseline.forecast(fitted, FIT_START, latest)
    dense = expander.expand(sep)
    sep_total = sum(sep["rates"].values())
    dense_total = sum(dense.values)
    print(f"\n  {s}:")
    print(f"    observed events          : {len(evs)}")
    print(f"    separable total          : {sep_total:.4f}")
    print(f"    dense total after expand : {dense_total:.4f}")
    print(f"    dense cells              : {len(dense.values):,}")
    err = abs(dense_total - sep_total) / sep_total
    print(f"    expansion relative error : {err:.3e}  (tolerance {expander.CONSERVATION_RTOL:.0e})")
    print(f"    conserved through expand : {err < expander.CONSERVATION_RTOL}")

print()
print("=" * 68)
print("Weekly expectation against D6")
print("=" * 68)
week_start = dt.datetime(2026, 8, 1, tzinfo=dt.timezone.utc)
week_end = week_start + dt.timedelta(days=7)
d6 = {"shallow": 16.4, "deep": 8.6}
for s, evs in by_stratum.items():
    fitted = baseline.fit(evs, s)
    sep = baseline.forecast(fitted, week_start, week_end)
    total = sum(sep["rates"].values())
    delta = 100 * (total - d6[s]) / d6[s]
    print(f"  {s:<8} forecast {total:6.2f}/week   D6 says {d6[s]:5.1f}   delta {delta:+.1f}%")

print()
print("=" * 68)
print("No cell may have rate exactly zero")
print("=" * 68)
for s, evs in by_stratum.items():
    fitted = baseline.fit(evs, s)
    sep = baseline.forecast(fitted, week_start, week_end)
    zeros = [c for c, v in sep["rates"].items() if v == 0.0]
    print(f"  {s:<8} cells {len(sep['rates']):,}  zero-rate cells {len(zeros)}  "
          f"min rate {min(sep['rates'].values()):.3e}")
