# Phase 2 preconditions

Things to settle in the spec BEFORE Phase 2 code exists. Each is one sentence to
write now and a day of debugging if it is missing.

The reason this file exists: every defect that reached code in Phase 1
originated in the plan rather than in the implementation, and they clustered in
one place, under-specified numerical and boundary semantics. That is the
actionable signal, so the highest value work before Phase 2 is pinning that
class down rather than starting on model code.

---

## 1. Numerical and boundary semantics to pin

These are the specific gaps. Each needs an explicit answer in the spec, not a
convention assumed at the keyboard.

**Threshold inclusivity.** Is an event of magnitude exactly 3.0 inside the target
set or outside it? Magnitudes are stored as floats, so state the comparison
explicitly and state how it interacts with float representation.

**Cell edge assignment.** Which side of a 0.1 degree cell edge does an event
lying exactly on the boundary fall? Both longitude and latitude, and both the
low and high edge. Follow D12's half-open convention unless there is a reason
not to, so cells tile without gaps or overlaps.

**Gutenberg-Richter estimator.** Which estimator, over which magnitude range,
with which binning. Aki maximum likelihood and a least-squares fit on the
cumulative distribution give different b values on the same data, and the fitted
b propagates into the magnitude distribution the M-test evaluates.

**Expander determinism.** Float precision and iteration order, because the
reproducibility test asserts byte-identical output. Dictionary iteration order,
floating point accumulation order, and the dtype written to parquet all have to
be pinned, or two correct runs produce two different files.

---

## 2. Write the expander's property tests first

The canonical refactor hazard already bit this project once. The unchunked
snapshot got its emptiness guarantee for free by delegating to a function that
had it; the chunked rewrite inlined the fetch loop and the guarantee vanished,
because it had been structural rather than written down. Nobody re-checked it,
because the review was scoped to chunking.

The Phase 2 expander has exactly that shape. Determinism and
separable-to-dense round-tripping will be properties of how it happens to be
written, not properties anyone has asserted. Write them as property tests
**before** the first implementation:

- Expanding the same separable forecast twice produces byte-identical output.
- Separable to dense to separable round-trips to the original values.
- Expansion is a pure function of committed bytes: no clock, no environment, no
  filesystem ordering.
- Total forecast rate is conserved across the expansion.

The failure mode if these are missing is identical to the one already seen: a
correctly named, correctly typed forecast file that is quietly wrong.

---

## 3. Turn D4a from a record into a rule: DONE

Settled and frozen. The measurement is in
`scripts/measurements/duplicate_tolerance.py` and the reasoning is in
`DECISIONS.md` section D4a.

The short version: a tolerance based rule does **not** work. Genuine doublets
overlap the known duplicate in every single dimension, getting closer in time
(0.019 s) and closer in space (0.000 km) than it does, so both separation gaps
are negative and no threshold separates the populations. Conjunctive exact
agreement on origintime, latitude, longitude, magnitude and depth does separate
them: one match in 61,191 events, with the nearest genuine pair agreeing on only
3 of the 5 fields.

Implemented as `assert_no_exact_duplicate_events`, proven to fail by removing
the exclusion and watching it go red, then restored. A companion
`report_near_duplicate_events` warns rather than fails, so the sample can grow
against the n=1 calibration limitation.

---

## 3b. Original notes on this item, kept for context

Documenting the `2018p914028` and `2018p914029` pair catches that pair. It does
not catch the next one, and duplicates feed straight into the event count the
N-test consumes, on both the fitting side and the evaluation side.

What is needed:

- A detection rule with **tolerances**, not exact equality. Identical to nine
  decimal places is easy to test and easy to evade; a real duplicate can differ
  in the last digit after a re-run.
- Tolerances on origin time, location and magnitude, tuned so the rule does not
  eat **genuine doublets**, which are real and not rare in aftershock sequences.
  Two distinct earthquakes seconds apart in the same place are physically
  ordinary; the rule must separate that from one earthquake recorded twice.
- The rule frozen in `DECISIONS.md` once chosen, since it changes the target
  set.
- The rule asserted in dbt, so a new duplicate surfaces as a test failure rather
  than as a slow unexplained drift in the published rates.

Suggested starting point for calibration: measure the distribution of nearest
neighbour separations in time, space and magnitude across the catalogue, and put
the tolerance where genuine doublets and true duplicates separate. If they do
not separate cleanly, say so publicly rather than picking a number.

---

## 4. Carried gaps from Phase 1

- The continuous integration workflow has never executed. It needs a remote and
  one manual run before it can be trusted.
- The UTC timezone pin lives in `dbt/profiles.yml`, not in the database file, so
  a client opening `data/eq.duckdb` directly gets local time. Consider pinning
  inside the database or moving derived dates upstream.
- Withdrawal is not modelled. Because every snapshot is unioned, an event GeoNet
  withdraws never leaves `stg_quakes`, though the diff format has a `withdrawn`
  change kind.
