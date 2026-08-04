# Frozen Decisions

This file is the constitution of the project. Every value here was fixed before
the first forecast was published, and none of them change.

Changing any one of them breaks comparability with every forecast already
scored. If one ever has to change, the correct response is to declare a new
experiment with a new identifier and start the public record over, leaving the
old record intact and clearly marked as closed.

Frozen on: 2026-08-04
Experiment ID: `nz-01`
Reference window for all measurements: 2021-01-01 to 2026-01-01 unless stated
Source: GeoNet Quake Search, bounding box `163.60840,-49.18170,182.98828,-32.28713`

Every number below is regenerable by a script in `scripts/measurements/`.

---

## D1. Collection region

**Rule.** A 1 degree cell is inside the collection region if and only if it
contains at least 150 events in the reference window **and** its measured
magnitude of completeness is 2.6 or lower. Cells that cannot be measured are
excluded, which is the conservative direction.

**Result.** 41 cells of 145 with any seismicity. The forecast grid is every
0.1 degree cell inside those 41 cells, giving 4,100 grid cells.

**What this excludes and why it matters.** 11 measurable cells failed the
completeness test. Every one of them lies in the northeast, along the Kermadec
arc and its extension offshore of East Cape:

| Cell (lon, lat) | Events | Measured Mc |
|---|---|---|
| 180-181, -33 to -32 | 157 | M4.3 |
| 181-182, -34 to -33 | 354 | M4.1 |
| 181-182, -35 to -34 | 169 | M3.9 |
| 179-180, -35 to -34 | 283 | M3.8 |
| 180-181, -34 to -33 | 269 | M3.7 |
| 179-180, -34 to -33 | 210 | M3.5 |
| 179-180, -36 to -35 | 265 | M3.3 |
| 178-179, -36 to -35 | 679 | M3.1 |
| 179-180, -33 to -32 | 152 | M3.1 |
| 180-181, -38 to -37 | 1,151 | M3.0 |
| 179-180, -37 to -36 | 221 | M2.9 |

**This cut removes 41% of the national M3.0 and above event count.** It is the
single most consequential decision in the project and it is deliberately
prominent rather than buried. The catalogue is not complete in those cells, the
incompleteness is not stable over time (Kermadec Mc measured at 3.2, 3.4, 4.3,
2.7 and 3.1 in 2005, 2010, 2015, 2020 and 2025), and forecasting a magnitude
threshold below the completeness of the region you are forecasting produces
numbers that mean nothing.

**Honest caveat.** The 93 cells that could not be measured are excluded for lack
of data, not for proven incompleteness. That makes the region partly
activity-defined as well as completeness-defined. The cost is small, because the
excluded unmeasurable cells contain very few target events, but it is a real
property of the rule and not a rounding detail.

**Longitude convention.** Continuous on [163.6, 183.0]. No wrapping at the
antimeridian, because New Zealand seismicity extends past longitude 180 and
wrapping would put a discontinuity through active crust.

**Out-of-region events.** Counted and reported separately, never silently
dropped.

**Frozen means hashed.** `region/grid.parquet` is generated once and its
SHA-256 is recorded here. Every component asserts the hash before running.

---

## D2. Magnitude threshold: M 3.0 and above

**Measured completeness inside the collection region**, by stratum, reference
window:

| Stratum | Events | Measured Mc |
|---|---|---|
| Shallow | 52,887 | M1.8 |
| Deep | 24,016 | M2.2 |

**Stationarity.** Pooled Mc inside the region, measured on older windows: M2.2
(2005), M2.3 (2010), M2.2 (2015), M1.9 (2020). Stable, and moving in the safe
direction. M3.0 clears the worst historical value by 0.7 magnitude units and the
worst retained cell by 0.4.

**Worst retained cell Mc is 2.6.** The 0.4 unit gap to M3.0 absorbs the known
tendency of the maximum-curvature estimator to underestimate Mc by 0.1 to 0.3.

**Why not higher.** The binding constraint is the deep stratum, where the
project's pre-committed ETAS prediction lives. At M3.5 the deep stratum yields
about 2.7 events per week, which cannot distinguish ETAS from a constant rate.
M3.0 takes it to about 8.6 per week and makes that comparison real.

**Why raising the threshold does not reduce boundary churn.** Under
Gutenberg-Richter the fraction of the target set within 0.2 magnitude units of
the threshold is fixed near 1 minus 10^-0.2, about 37%, wherever the threshold
sits, while magnitude uncertainty is roughly constant in absolute terms. Raising
the threshold costs power and buys no protection. Instead, every scored window
publishes the count of events within 0.2 units of the threshold so readers can
see the exposure.

---

## D3. Depth strata: two, boundary at 41 km

**Rule.** Kernel density estimate on log10 depth, using free depths only, over
events inside the collection region. Bandwidth fixed by Silverman's rule so that
no bandwidth is chosen by hand. The boundary is the interior local minimum of
the density.

**Result.** Silverman bandwidth h = 0.0607, giving a minimum at **41.1 km**.
Frozen at **41 km**.

Sensitivity, for the record: 38.7 km at 0.7x bandwidth, 40.0 at 0.85x, 41.1 at
1.0x, 42.9 at 1.2x, 45.3 at 1.5x.

**Shallow** is depth 41 km or less. **Deep** is deeper than 41 km.

### A rejected criterion, recorded deliberately

The first proposed rule was minimisation of total within-stratum depth variance,
which is Otsu's method. **It was implemented, run, and rejected on evidence.**

On this distribution the within-stratum variance decreases monotonically across
the entire 30 to 90 km search range and lands on whichever range edge it is
given: 90 km when bounded at 90, and 127 km when allowed to run to 300. It never
finds an interior minimum, because with a depth tail extending past 700 km the
criterion is balancing tail variance rather than locating the crust to slab
transition.

Had this rule been frozen as originally written, the boundary would have been an
artifact of an arbitrarily chosen search range. It is recorded here so that the
rejection is part of the public record, and so the criterion is not proposed
again later as an improvement.

### Registration

Shallow and deep are **separately registered and separately scored forecasts**.
They are never blended into a single number. This makes the low-power problem
explicit rather than hidden inside a mixed rate, and it turns the project into a
test of whether an ETAS model calibrated on crustal seismicity transfers to
subduction seismicity.

### Known simplification

A flat horizontal plane is a crude model of real geometry. The Hikurangi
subduction interface lies roughly 15 to 25 km beneath the east coast of the
North Island and deepens westward past 50 km, so any horizontal boundary
misassigns events near the margin. Accepted for this experiment and disclosed.

### Pre-committed expectation

ETAS is expected to underperform in the deep stratum, possibly barely beating
the time-invariant baseline, because deep intraslab events produce far fewer
aftershocks than crustal ones. **This result will be published either way.**

---

## D4. Depth data quality

**42% of events have operator-assigned depths, not measured ones.** GeoNet fixes
depth to a convention when a solution is poorly constrained. The signature over
2005 to 2026:

| Fixed depth | Occurrences |
|---|---|
| 33 km | 3,886 |
| 12 km | 2,718 |
| 5 km | 1,962 |
| 100 km | 456 |
| 10 km | 214 |

The `depthtype` field is carried through every pipeline layer and never dropped.
A dbt test asserts on its distribution so a change in GeoNet practice is caught
rather than silently absorbed. Boundary fitting uses free depths only.

**Why stratum assignment survives this.** 92% of assigned depths fall below
40 km, and the two large piles sit at 33 km and 100 km. For any boundary between
those values the 33 km pile lands in shallow and the 100 km pile in deep, so
assignment is insensitive to the exact boundary.

---

## D5. Spatial grid

Cells of 0.1 degree by 0.1 degree. 4,100 cells, being every 0.1 degree cell
inside the 41 retained 1 degree cells of D1.

---

## D6. Forecast horizons

Daily and weekly, both produced and both scored. **Weekly leads** on the public
page.

Expected counts inside the region at M3.0:

| Stratum | Per day | Per week |
|---|---|---|
| Shallow | 2.34 | 16.4 |
| Deep | 1.22 | 8.6 |

**Daily and weekly are not independent evidence.** They cover overlapping
periods and are scored against overlapping event sets. For a time-invariant
baseline every daily forecast is the identical rate field, so 365 daily windows
test one fixed field 365 times rather than providing 365 independent tests.
Effective sample size tracks event count, not window count. The public page
states this rather than quoting "365 plus 52 scored windows" as if it were
417 independent results.

---

## D7. Catalogue revision policy

**Score of record at T+45**, meaning 45 days after the forecast window closes.
**A provisional score is published at T+7, labelled unstable**, along with the
drift between the two.

**The evaluation catalogue is snapshotted and committed alongside every score.**
Scoring reads that committed file and never a live API. This is what makes
reproducibility real rather than aspirational: the remaining drift does not
matter, because anyone re-running against the exact committed bytes reproduces
the number exactly.

**Why 45 and not 30.** Revision lag was measured on all 1,908 M3.0 and above
events in 2025. It is not a decay curve. It is a rolling review queue with a
hard wall: the lag histogram rises to a peak at 24 to 26 days, then falls to
**zero events between 32 and 45 days**, with p99 at 30.08 days and p99.9 at
30.65. T+30 sits exactly on that wall. T+45 sits in the empty band beyond it and
costs only latency that the T+7 provisional score already covers.

Measured lag percentiles: p50 19.85 d, p75 24.91 d, p90 28.13 d, p95 29.41 d,
p99 30.08 d, p99.9 30.65 d.

An honest caveat on what was measured: `modificationtime` records the most
recent touch of a record, so this measures record churn rather than magnitude
churn specifically.

---

## D8. Declustering: fit undeclustered

**The baseline is fitted on the full, undeclustered catalogue.**

Smoothed-seismicity models conventionally fit on a declustered catalogue so that
aftershock sequences do not inflate the background rate. That convention exists
for models whose target is background seismicity. This project's target is
**every catalogue event above threshold, aftershocks included**, so the fit must
match the target. Fitting declustered while scoring against the full catalogue
would systematically under-forecast during sequences.

The consequence, stated plainly: the resulting rate is not a "background" rate
and will not be described as one.

**Interaction with ETAS.** ETAS models triggering explicitly and conventionally
uses a declustered background term. When ETAS is registered it may therefore use
a declustered background *inside* the model, while still being scored against
the same undeclustered target. That is a property of the model, not a change to
this decision.

---

## D9. Forecast representation

Separable: a rate per (cell, stratum) plus the magnitude distribution as fitted
Gutenberg-Richter parameters. A deterministic expander inflates this to a dense
pyCSEP gridded forecast at scoring time.

**Why.** A dense forecast at full resolution is millions of values per forecast,
which puts gigabytes a year into git and breaks the clone-and-reproduce
requirement that committing to git exists to serve. Separable is about 8,200
rates per forecast.

**Honest caveat.** Because the magnitude distribution is shared across space,
the M-test evaluates the global Gutenberg-Richter fit rather than per-cell
magnitude structure. Most operational ETAS forecasts are separable this way, so
this is conventional rather than a dodge, but it is stated rather than glossed.

Reproducibility survives because expansion is a pure function of committed bytes
with no external input.

---

## D10. Timestamp integrity

The project's central claim is that a forecast existed before the window it
describes. **Git commit timestamps alone do not establish this**, because both
author and committer dates are settable by the committer. Three independent
anchors are therefore recorded for every forecast:

1. **GitHub Actions run ID** in the forecast manifest. Run start times are
   server-side timestamped by GitHub and retrievable through their API, so the
   claim becomes "run N executed at time T according to GitHub" rather than
   "the commit metadata says so".
2. **OpenTimestamps proof**, a Bitcoin-anchored attestation over the forecast
   file. A few bytes per forecast, verifiable by anyone, indefinitely, with no
   trust in the author or in GitHub.
3. **The commit itself**, which remains useful as a cheap first check.

**Publication lead time.** Forecasts are published at **T minus 2 hours** for a
window opening at T. GitHub Actions cron is routinely delayed by several
minutes, and the refusal boundary is the window start rather than the run time,
so ordinary scheduler jitter costs nothing.

**Commit authorship.** Scheduled workflow commits are authored
`github-actions[bot]`. Human commits are authored
`Jesse O'Brien <jesse@jesse-obrien.com>`. These are deliberately distinguishable
so that any human touch in the forecast history is visible to a reader.

---

## D11. Never backfill

If the scheduler misses a window, that window is **permanently unforecast**. It
is recorded as a gap and rendered as a gap on the scoreboard.

A missing forecast is honest. A forecast committed after the window it describes
is indistinguishable from fraud to a reader, regardless of intent. The publish
step refuses to write a forecast whose window has already started.
