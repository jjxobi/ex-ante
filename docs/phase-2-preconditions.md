# Phase 2 preconditions

Things to settle in the spec BEFORE Phase 2 code exists. Each is one sentence to
write now and a day of debugging if it is missing.

The reason this file exists: every defect that reached code in Phase 1
originated in the plan rather than in the implementation, and they clustered in
one place, under-specified numerical and boundary semantics. That is the
actionable signal, so the highest value work before Phase 2 is pinning that
class down rather than starting on model code.

---

## 1. Numerical and boundary semantics: DONE, frozen as D13

All four settled and frozen in `DECISIONS.md` section D13. Threshold
inclusivity and magnitude representation were decided by measurement rather than
convention; see `scripts/measurements/threshold_exposure.py`. One item is
deliberately left open as a Phase 2 assertion rather than an assumption:
pyCSEP's CartesianGrid2D origin convention must be verified by a test, not
taken on inference.

---

## 1b. Original notes on this item, kept for context

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

- **CI verifies less than a green run suggests.** First execution was clean,
  27 of 27, which confirms the pipeline runs end to end on a fresh Linux runner:
  `dbt` resolves on PATH, `python -m eq.cli` works without `PYTHONPATH`, the GNU
  date arithmetic is right, and the freshness test passes on a rolling window.
  But CI builds a 30 day slice of about 200 events, so **4 of the 10 data
  contract tests are inert there**, passing because they have nothing to look at:
  `assert_depthtype_share` (needs 2,000 M3.5+ events, the slice has 92),
  `assert_no_exact_duplicate_events` and `assert_no_duplicate_origins` (the only
  known duplicate is from 2018), and `assert_withdrawals_within_bound` (one
  snapshot, so no previous to diff). The full catalogue contracts are currently
  only verified by running the build locally. Worth a scheduled weekly job
  against the full catalogue once Phase 4 brings a scheduler, so the contracts
  are exercised somewhere automated rather than only on a developer machine.
- The continuous integration workflow has now executed successfully and the
  repository has a remote, so this item is closed. Rule 2 still needs the
  Actions run ID and OpenTimestamps anchoring wired into the manifest before it
  is stronger than commit metadata.
- The UTC timezone pin lives in `dbt/profiles.yml`, not in the database file, so
  a client opening `data/eq.duckdb` directly gets local time. Consider pinning
  inside the database or moving derived dates upstream.
- Withdrawal: RESOLVED. Measured at roughly one per day on the first real
  snapshot diff, including a `confirmed` and `manual` event, so it was not
  confined to unreviewed automatic solutions. The staging model now reads only
  the newest snapshot, with a withdrawal count guard because a partial pull and
  a genuine withdrawal are indistinguishable from outside. Frozen as D4b.
