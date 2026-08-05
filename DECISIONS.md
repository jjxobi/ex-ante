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

**One rule, one scale.** The region is defined by the 1 degree completeness rule
alone. The earlier draft of this project also carried a 0.1 degree activity rule
(keep a cell only if it contained at least one M3.0 event in 2005 to 2025), and
that rule was **deliberately dropped**, not merged. Intersecting the two would
punch holes inside qualified ground, forcing an event that lands in a
completeness-qualified cell to be counted out of region purely because that
0.1 degree cell happened to be quiet for twenty years. The activity rule existed
only to avoid carrying tens of thousands of empty ocean cells across the full
bounding box, and the completeness rule already solves that: 4,100 cells is
small enough that no second filter is needed.

**Hashing.** The SHA-256 covers `region/grid.parquet`, the final materialised
4,100 cell grid, not the rule or its inputs. A future regeneration that changed
the grid while still appearing to satisfy the rule would therefore fail the hash
assertion rather than pass silently.

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

**Honest caveat 1: unmeasurable cells.** The 93 cells that could not be measured
are excluded for lack of data, not for proven incompleteness. That makes the
region partly activity-defined as well as completeness-defined. The cost is
small, because the excluded unmeasurable cells contain very few target events,
but it is a real property of the rule and not a rounding detail.

**Honest caveat 2: selection on estimation noise.** Selecting cells on measured
Mc preferentially retains cells whose estimate happened to come out low. Across
145 noisy per-cell estimates the retained set is therefore enriched for downward
error, so the true worst-cell Mc is higher than the measured worst-cell Mc. This
compounds with the known tendency of the maximum-curvature estimator to
underestimate Mc by 0.1 to 0.3. It is the same effect as picking funds on last
year's returns, and the 0.4 unit margin between the 2.6 ceiling and the M3.0
threshold is thinner than it looks.

The effect was measured rather than left as a worry. Retained cells shifted
+0.21 magnitude units between the selection window and a held-out window;
non-selected cells shifted +0.18. **The differential attributable to selection
is therefore about +0.04**, with the remaining +0.18 attributable to the era,
since the network was genuinely worse before 2020. The selection effect is real
but small. Caveat on the caveat: only 4 non-selected cells were measurable in
both windows, so that differential is itself noisy.

### Specification defect in this decision, found and recorded

**This rule as first written was wrong, and the error is recorded rather than
quietly corrected.**

D1 originally specified that the completeness criterion applies over the 2021 to
2026 reference window. It should have said: over **any period used for fitting
or for scoring**. Qualifying a region on recent data and then fitting a model on
two decades of older data means the model is fitted on ground that was not
complete when that data was recorded.

Running the held-out check exposed four retained cells that are complete now but
were not complete over the full historical window:

| Cell (lon, lat) | Mc 2021-2026 | Mc held-out | Complete from |
|---|---|---|---|
| (178, -37) | 2.6 | 3.0 | **2019** |
| (179, -38) | 2.6 | 2.7 | 2017 |
| (177, -37) | 2.6 | 2.9 | 2006 |
| (172, -41) | 2.2 | 2.9 | 2005 |

The remaining 37 cells are complete from 2005.

**The region was not changed in response.** D1 is frozen, and unfreezing a
frozen decision days after freezing it is exactly what the integrity rules exist
to prevent. The fitting window is not a frozen decision, it is a model choice,
and that is where the repair belongs. See the design spec for the fitting-window
treatment.

**This is enforced by a test, not by a note.** The test suite asserts that every
retained cell has Mc <= 2.6 over the actual period used for fitting, and fails
loudly if either the region or the fitting window moves. This class of defect
recurs precisely when someone later extends the fit backwards in search of more
data.

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

**The refit did not disturb this.** The frozen boundary of 41 km sits between
the 33 km and 100 km piles, so the robustness argument above holds unchanged
after the boundary was recomputed on the restricted region. It would also have
held at any value the bandwidth sensitivity band produced, since that band runs
38.7 to 45.3 km.

---

## D4a. Duplicate records in the GeoNet catalogue

**One duplicate pair found in 61,191 events.** Records `2018p914028` and
`2018p914029` are identical in every scientific field to nine decimal
places: origintime 2018-12-05T04:34:31.164Z, latitude -40.03751793,
longitude 174.3691163, magnitude 3.086434784, depth 111.875, magnitudetype
M. They differ only in publicid and in modificationtime, fourteen minutes
apart. Both are evaluationmode "automatic" with an empty evaluationstatus,
meaning neither was ever reviewed. This is one earthquake ingested twice by
GeoNet's automatic system and never merged, not two earthquakes and not a
parsing fault in this pipeline. No other origintime is shared anywhere else
in the catalogue.

**Why it matters.** A duplicate inflates the observed event count for
whatever spatial and temporal bin it falls in, and observed event count is
exactly what the N-test consumes in Phase 3. Left uncorrected, a duplicate
biases that test toward over-counting, however slightly.

**Phase 1 keeps both records.** This project does not quietly alter source
data, so nothing is dropped here. A dbt test, `assert_no_duplicate_origins`,
detects duplicate (origintime, latitude, longitude) groups and excludes this
single documented pair by publicid so a genuinely new duplicate still fails
the build. Whether to exclude duplicates from the target set is a Phase 2
decision, to be made when the target set is defined.

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

---

## D12. Forecast window boundaries

**Every window boundary is UTC.** Daily windows run midnight to midnight UTC.
Weekly windows run Monday 00:00:00 UTC to the following Monday 00:00:00 UTC.

**Why not local time.** New Zealand observes daylight saving. Local-time daily
windows would therefore be 23 hours long once a year and 25 hours long once a
year, silently changing the exposure period against which a rate is scored. Two
windows a year would be wrong by about 4 percent, with nothing to flag it, and
the error would be invisible in the outputs. UTC removes the discontinuity
entirely and matches CSEP convention.

This is the same class of defect as the `origin_date` timezone fault found while
building Phase 1, where 52 percent of the catalogue landed on a different
calendar date depending on the machine. Fixing the ingest side does not fix the
window side; both have to be pinned.

**Windows are half-open: `[start, end)`.** An event whose origin time equals the
window start belongs to that window. An event whose origin time equals the
window end belongs to the NEXT window. Consecutive windows therefore tile the
timeline exactly, with no gap and no overlap, so no event is double counted and
none is dropped.

This convention is not arbitrary. GeoNet's own Quake Search date parameters were
verified to behave the same way: four adjacent five year queries covering 2005
to 2025 returned 57,961 rows whose union was exactly 57,961 distinct event ids,
with zero events appearing in two adjacent chunks. Matching the upstream
convention removes a whole class of off-by-one at ingest boundaries.

**The comparison is instant based, never string based.** Origin times are stored
as instants. Boundary tests compare instants, never rendered timestamps, because
rendering is timezone dependent.
