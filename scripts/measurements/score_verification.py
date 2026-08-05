"""Independent check of the scorer's end-to-end deliverable against live data.

tests/test_score.py's end-to-end test reads the committed fit-window fixture
for both fitting and observation, per the same reasoning test_baseline.py
records: data/ is gitignored, so a test that depended on it would silently do
nothing on a fresh clone. This script is where the corresponding check against
the wider, non-committed snapshot belongs, per that division of labour.

It also recomputes the filtering counts (out of region, above M8.5) directly
from eq.region and eq.expander rather than by calling eq.score, so a bug
shared between the module and its own test would not hide here.
"""
import datetime as dt
import sys

sys.path.insert(0, "src")
from eq import baseline, expander, paths, region, score, storage  # noqa: E402

FIXTURE = paths.REPO_ROOT / "tests" / "fixtures" / "catalogue-fit-window.parquet"
SNAPSHOT = paths.SNAPSHOT_DIR / "catalogue-2026-08-05.parquet"

WINDOW_START = dt.datetime(2026, 7, 20, tzinfo=dt.timezone.utc)
WINDOW_END = dt.datetime(2026, 7, 27, tzinfo=dt.timezone.utc)

print("=" * 72)
print("Fixture vs snapshot agreement for the scored window")
print("=" * 72)
fixture_rows = storage.read_parquet(FIXTURE)
snapshot_rows = storage.read_parquet(SNAPSHOT)

fixture_window = [e for e in fixture_rows if WINDOW_START <= e["origintime"] < WINDOW_END]
snapshot_window = [e for e in snapshot_rows if WINDOW_START <= e["origintime"] < WINDOW_END]
same_ids = sorted(e["publicid"] for e in fixture_window) == sorted(
    e["publicid"] for e in snapshot_window
)
print(f"  events in window, fixture:  {len(fixture_window)}")
print(f"  events in window, snapshot: {len(snapshot_window)}")
print(f"  identical publicid sets:    {same_ids}")

print()
print("=" * 72)
print(f"End to end score: shallow, {WINDOW_START.date()} to {WINDOW_END.date()}")
print("=" * 72)

fitted = baseline.fit(fixture_rows, "shallow")
separable = baseline.forecast(fitted, WINDOW_START, WINDOW_END)
dense = expander.expand(separable, expected_grid_hash=baseline.FROZEN_GRID_HASH)

for source_name, rows in (("fixture", fixture_rows), ("live snapshot", snapshot_rows)):
    result = score.score(dense, rows, WINDOW_START, WINDOW_END, stratum="shallow")
    print(f"\n  observation source: {source_name}")
    print(f"    fit_start:        {fitted.fit_start}")
    print(f"    fit_end:          {fitted.fit_end}")
    print(f"    b (shallow):      {fitted.b:.4f}")
    print(f"    expected count:   {result.expected_count:.4f}")
    print(f"    observed (used):  {result.n_events_used}")
    print(f"    out of region:    {result.n_out_of_region}")
    print(f"    above M8.5:       {result.n_above_mmax}")
    print(f"    below M3.0:       {result.n_below_mmin}")
    print(f"    N-test: statistic={result.n_test.observed_statistic}, "
          f"quantile={result.n_test.quantile}")
    print(f"    S-test: statistic={result.s_test.observed_statistic:.4f}, "
          f"quantile={result.s_test.quantile:.4f}")
    print(f"    M-test: statistic={result.m_test.observed_statistic:.4f}, "
          f"quantile={result.m_test.quantile:.4f}")
    print(f"    L-test: statistic={result.l_test.observed_statistic:.4f}, "
          f"quantile={result.l_test.quantile:.4f}")

print()
print("=" * 72)
print("Information gain: fitted baseline vs uniform, full fit period")
print("=" * 72)
fit_period_sep = baseline.forecast(fitted, fitted.fit_start, fitted.fit_end)
fit_period_dense = expander.expand(fit_period_sep, expected_grid_hash=baseline.FROZEN_GRID_HASH)
total = sum(fit_period_sep["rates"].values())
uniform_rate = total / len(fit_period_sep["cell_ids"])
uniform_sep = {
    "grid_hash": fit_period_sep["grid_hash"],
    "cell_ids": fit_period_sep["cell_ids"],
    "b": fit_period_sep["b"],
    "rates": {cid: uniform_rate for cid in fit_period_sep["cell_ids"]},
}
uniform_dense = expander.expand(uniform_sep, expected_grid_hash=baseline.FROZEN_GRID_HASH)

ig_fit_period = score.information_gain(
    fit_period_dense, uniform_dense, fixture_rows, fitted.fit_start, fitted.fit_end, stratum="shallow"
)
print(f"  events used in comparison: many thousands, full {fitted.fit_start} to {fitted.fit_end} span")
print(f"  information gain (fitted vs uniform), full fit period: {ig_fit_period:.4f}")

# The week's uniform benchmark must share the WEEK forecast's own total, not
# the fit period's: comparing forecasts with mismatched totals confounds the
# t-test's N1-N2 term with the spatial comparison this is meant to isolate.
# An earlier version of this script did exactly that and printed +742, an
# artifact of the mismatched totals rather than a real result.
week_total = sum(separable["rates"].values())
week_uniform_rate = week_total / len(separable["cell_ids"])
week_uniform_sep = {
    "grid_hash": separable["grid_hash"],
    "cell_ids": separable["cell_ids"],
    "b": separable["b"],
    "rates": {cid: week_uniform_rate for cid in separable["cell_ids"]},
}
week_uniform_dense = expander.expand(week_uniform_sep, expected_grid_hash=baseline.FROZEN_GRID_HASH)

ig_week = score.information_gain(
    dense, week_uniform_dense, fixture_rows, WINDOW_START, WINDOW_END, stratum="shallow"
)
print(f"  information gain (fitted vs uniform), single real week: {ig_week:.4f}")
print(
    "  (the single week uses only 8 events and a paired t-test needs many more\n"
    "  to reliably separate a real spatial advantage from sampling noise; the\n"
    "  full fit period is the number that answers 'is the model better than\n"
    "  uniform', the week is reported for honesty about what a single window can show)"
)

print()
print("=" * 72)
print("Self information gain (must be exactly 0.0)")
print("=" * 72)
ig_self = score.information_gain(
    dense, dense, fixture_rows, WINDOW_START, WINDOW_END, stratum="shallow"
)
print(f"  information gain (fitted vs itself): {ig_self!r}")
