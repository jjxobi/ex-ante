# Ex Ante: Frozen Decisions

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

**This cut removes 41% of the national M3.0 and above event count over the
reference window**, and that qualifier matters. It is the single most
consequential decision in the project and it is deliberately prominent rather
than buried.

The 41% figure is exact for 2021 to 2026, the window D1 is defined on. It is
**not** a stable property of the catalogue. Measured across the whole 2005 to
2026 snapshot the excluded share is **26.1%**, and by year it ranges from
**9.8% to 50.9%**:

| Period | Excluded share |
|---|---|
| Whole catalogue, 2005 to 2026 | 26.1% |
| Reference window, 2021 to 2026 | 41.0% |
| Lowest year (2011) | 9.8% |
| Highest year (2026 to date) | 50.9% |

Two things drive the drift, in opposite directions. GeoNet's detection in the
Kermadec region has improved, so the excluded area contributes a growing share
of recorded events over time. Against that, a large mainland sequence inflates
the in-region denominator and pushes the excluded share down sharply: the two
lowest years are 2011 at 9.8% and 2016 at 15.0%, which are the Canterbury and
Kaikoura sequences.

So the honest statement is that the cut removes between a tenth and a half of
national recorded seismicity depending on the year, and 41% over the window this
decision was measured on. Regenerate with
`scripts/measurements/exclusion_trend.py`. The catalogue is not complete in those cells, the
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

SHA-256: `14b2e0b854b5ae89771ad3346204e801f1f32580fd9a09481b9b6f6fe9cd4e44`

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
data, so nothing is dropped here. Whether to exclude duplicates from the target
set is a Phase 2 decision, to be made when the target set is defined.

### The detection rule, and why it is exact rather than tolerance based

Documenting one pair catches that pair. It does not catch the next one. The
rule below is frozen so that new duplicates surface as a build failure rather
than as a slow unexplained drift in the published rates.

**The obvious approach fails, and the failure is measured.** A tolerance box,
flagging pairs closer than some threshold in time, location and magnitude, does
not work, because genuine doublets are physically ordinary in aftershock
sequences and overlap duplicates in every single dimension. Measured across all
11,961 pairs within 300 seconds of each other:

| | Duplicate neighbourhood | Genuine pairs |
|---|---|---|
| Closest in time | 0.000 s | **0.019 s** |
| Closest in space | 0.0 m | **0.000 km** |

Genuine pairs get closer in time and closer in space than the duplicate does.
Both separation gaps are negative, so no threshold in any single dimension, or
any box built from them, separates the two populations.

**Conjunctive exact agreement does separate them.** Requiring two distinct
publicids to agree **exactly** on all five of origintime, latitude, longitude,
magnitude and depth yields:

- exactly **one** matching pair in 61,191 events, the documented one
- the nearest genuine pair agrees on only **3 of the 5** fields, and differs in
  origin time by 53 seconds

The margin is therefore wide, and it is wide for a physical reason: a duplicate
is one solution recorded twice, so it agrees to full float precision everywhere,
while two distinct earthquakes essentially never do.

Matching on location alone would be wrong. Five genuine pairs agree exactly on
latitude, longitude and depth while differing in origin time by 50 to 220
seconds. Those share an operator-assigned hypocentre, which is common; it is the
conjunction with origin time that discriminates.

**Tolerances are the wrong tool for this mechanism, not merely a tool that
failed to work here.** A duplicate is one solution ingested twice, so it agrees
to full float precision *by construction*. Two records differing in the last
decimal place are not a duplicate at all: they are a re-solution of the same
event, which is revision, and revision is governed by D7. Reaching for a
tolerance conflates two different mechanisms. Exactness is not a conservative
approximation of the right rule; it is the right rule.

**Cross-reference to D4.** The five genuine pairs that agree exactly on
latitude, longitude and depth while differing by minutes in origin time are the
operator-assigned depth phenomenon of D4 appearing in a second guise. There, a
poorly constrained solution is pinned to a conventional depth such as 33 km;
here, a whole hypocentre is pinned and reused. Both are cases where exact
agreement in a field signals an assigned value rather than a measured one, which
is precisely why no single field can carry the discrimination alone.

**The frozen rule.** Two records are duplicates if they have different
`publicid` and identical `origintime`, `latitude`, `longitude`, `magnitude` and
`depth`. Enforced by `assert_no_exact_duplicate_events`, which excludes the one
documented pair by publicid so a genuinely new duplicate fails the build. That
exclusion is deliberately narrow and must never be widened to silence a future
failure.

**Honest limitation.** This rule is calibrated on a single known duplicate.
With n=1 there is no way to know whether GeoNet could emit a near-duplicate that
differs in the last decimal place, which an exact rule would miss. The rule is
therefore deliberately conservative in the safe direction: it will never eat a
genuine doublet, and it may miss an inexact duplicate. To let the sample grow,
`report_near_duplicate_events` warns rather than fails on pairs agreeing on four
of the five fields, so candidates surface for human judgement without crying
wolf. If a second duplicate is ever found, this rule gets revisited with two
data points instead of one.

---

## D4b. Withdrawn events

**GeoNet removes events from the catalogue, and it does so to reviewed events.**
Measured on the first real snapshot diff, 2026-08-04 against 2026-08-05:

| Change kind | Count in one day |
|---|---|
| Revised | 74 |
| New | 2 |
| **Withdrawn** | **1** |

Revised fields in that day: magnitude 20, latitude 20, longitude 19, depth 13,
evaluationstatus 2.

The withdrawn event was `2026p432331`, M3.20, depth 182.6 km, origin time
2026-06-09. It carried `evaluationstatus = 'confirmed'` and
`evaluationmode = 'manual'`, so it was **not** an unreviewed automatic solution.
A human-reviewed event was removed, presumably reclassified as a blast, as noise
or as a bad solution.

**Why this is larger than D4a, by two orders of magnitude.** A withdrawn event
left in the catalogue inflates the observed event count exactly as a duplicate
does, with the same consequence for the N-test. But D4a is a single pair in
61,191 events and is not recurring, whereas withdrawal runs at roughly one per
day, which is on the order of 365 phantom events a year.

**Delta ingest is structurally blind to it.** An append-only delta cannot observe
a deletion, by construction. Detection is only possible by diffing consecutive
full snapshots, which means the snapshot-diff path is load bearing for
correctness and not merely the source of the revision curve, as it was
originally described.

**The current warehouse cannot act on it.** `stg_quakes` unions every snapshot
file, so an event present in any snapshot remains in the view permanently. The
diff format already carries a `withdrawn` change kind and the diff detects it
correctly, but nothing downstream consumes that signal.

### The decision

**The staging model reads only the newest snapshot, not the union of all of
them.** Withdrawal then takes effect automatically, because an event absent from
the newest snapshot is absent from the view. The revision history lives in the
committed diffs, which is where it belongs; the warehouse holds current truth.

**A withdrawal count guard is required, because withdrawal and ingest failure
look identical.** An event present yesterday and absent today is either a
genuine withdrawal or a partial pull, and conflating them is the failure mode: a
truncated ingest would silently present as hundreds of withdrawals and quietly
delete real events from the catalogue.

The guard: a new snapshot whose withdrawal count against its predecessor exceeds
a threshold is rejected loudly rather than accepted. The observed rate is one per
day. The threshold is set at **20 per day**, provisionally, and is explicitly
undercalibrated: it rests on a single day of observation. It gets revisited once
the daily diffs have accumulated enough history to characterise the distribution,
which is one of the things those diffs are for.

This complements rather than replaces the existing protections. Ingest already
raises on any chunk that parses to zero records, so a wholly failed chunk cannot
reach the warehouse at all; the guard covers the case of a chunk that returns a
plausible but incomplete result.

### An implementation trap, recorded before it bites

"Newest snapshot" cannot be implemented as the lexically greatest filename.
Continuous integration writes `catalogue-ci.parquet`, and `c` sorts after `2`,
so a naive maximum would select the small CI slice over the real catalogue and
silently reduce the warehouse to a thirty day window. The selection must filter
to date-shaped names, or the CI artifact must be renamed to a dated form.

### The frozen evaluation catalogue keeps its phantoms

An event withdrawn after a window's T+45 snapshot was taken means the frozen
evaluation catalogue contains an event that no longer exists, and the score of
record was computed against it.

**That score is not recomputed.** D7 freezes the evaluation catalogue precisely
so that a published number cannot move after the fact, and a frozen catalogue
that gets silently corrected is not frozen. The alternative would mean every
published score is provisional forever, which destroys the property the freeze
exists to create.

Instead, the withdrawal is visible in the committed diffs, so a reader can
establish exactly which scored windows contain a since-withdrawn event and by how
much it moved the count. The honest position is that a score of record is the
score against the catalogue as it stood at T+45, stated as such, rather than a
claim about ground truth that is revised indefinitely.

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

---

## D13. Numerical and boundary semantics

Every defect that reached code in Phase 1 originated in the plan rather than the
implementation, and they clustered here: implicit numeric and boundary
conventions that produce a plausible answer on one machine. These are pinned
before Phase 2 code exists.

### D13.1 Magnitude threshold is inclusive, and magnitude is never rounded

**The target set is `magnitude >= 3.0`, inclusive.** Gutenberg-Richter is
defined as N(>= M), Mc is conventionally an inclusive floor, and pyCSEP's
magnitude bins are lower-inclusive. An exclusive threshold would sit at odds
with every convention this project claims kinship with. 57 events in the
catalogue carry a magnitude of exactly 3.0, so this is a populated boundary, not
a theoretical one.

**Magnitude is stored as a 64 bit float and compared exactly. It is never
rounded, and never stored as a fixed-precision decimal.** This was measured
rather than assumed, and the measurement overturned the obvious choice.

GeoNet does **not** report magnitudes to one decimal place. Across 404,866
events, only 0.76 percent carry one decimal or fewer; 40 percent carry three
decimals and 39 percent carry nine, with tails to sixteen. Magnitudes arrive at
full solver precision.

Rounding to a fixed decimal would therefore not be a representation choice, it
would be a silent alteration of the target set, promoting events from below the
threshold into it:

| Rounding | Events promoted into the target set | As a share of it |
|---|---|---|
| 1 decimal place | 5,283 | 8.824% |
| **2 decimal places** | **509** | **0.850%** |
| 3 decimal places | 24 | 0.040% |

A `DECIMAL(4,2)` column would inflate the event count by 0.85 percent. Event
count is exactly what the N-test consumes, so that is a systematic upward bias
roughly five hundred times larger than the single duplicate D4a exists to guard
against.

**There is no float hazard on this comparison, and the reason is specific.**
3.0 is exactly representable in IEEE 754 binary64, so `float("3.0") == 3.0` is
guaranteed true, confirmed by the 57 events that compare exactly equal after
parsing. The hazard of a value arriving as 2.9999999999999996 arises from
*arithmetic*, and this pipeline performs no arithmetic on magnitude: it parses a
decimal literal and compares it. Where arithmetic does occur, on the spatial
grid, the hazard is real and is handled in D13.2.

### D13.2 Cell assignment is lower-inclusive and computed in integers

**A point belongs to cell `[x, x + 0.1)`**, lower-inclusive and upper-exclusive,
in both longitude and latitude. This matches `numpy.digitize`, pyCSEP's grid
convention, and D12's window convention, so cells tile without gap or overlap.

**Assignment is computed in integer decidegrees, never in floating point.**
Unlike magnitude, this involves real arithmetic on values that are not exactly
representable: neither 163.6 nor 0.1 has an exact binary64 representation.

The hazard was measured against this project's actual coordinate ranges rather
than asserted, because the example usually quoted for it is not in fact an
example. `(174.5 - 163.6) / 0.1` evaluates to 109.00000000000006, which floors
to 109, the correct answer. An earlier draft of this section claimed it floored
to 108.999999999999986. That claim was wrong and is corrected here rather than
quietly deleted.

The real result, from `scripts/measurements/grid_edge_hazard.py`, sweeping every
cell edge in the region:

| Axis | Edges swept | Disagreements between naive float and integer |
|---|---|---|
| Longitude, 163.6 to 183.0 | 195 | **39** |
| Latitude, -49.2 to -32.3 | 171 | **0** |

Every disagreement is an offset of exactly one cell, and they recur on a regular
0.5 degree beat beginning at 163.7, 164.2, 164.7. A naive implementation would
therefore place part of the forecast in the wrong cell, silently.

The zero on the latitude axis is the more instructive number. The same
arithmetic, over a comparable range, produces no disagreement at all. Whether
the hazard bites depends on the origin and the coordinate, so it cannot be
reasoned about case by case; it has to be eliminated structurally. This is the
same species of defect as the timezone fault found in Phase 1.

The rule: multiply coordinates by 10 and round to an integer **once**, at the
system boundary where data enters, then bin with integer arithmetic thereafter.
Cell identifiers are integers. Cell bounds are never stored as floats.

**pyCSEP's conventions, confirmed from source rather than inferred.**

- **Origin is the lower-left corner.** `CartesianGrid2D` sets
  `bounds = column_stack((orgs, orgs + dh))`, and `to_dict` serialises
  `poly.origin` as the cell coordinate. Midpoints are derived from origins, not
  the other way round. This matches the convention assumed here, so no offset
  correction is needed.
- **Binning is lower-inclusive and upper-exclusive**, stated in the `bin1d_vec`
  docstring. This matches D13.2 and D12 exactly.
- **pyCSEP reaches that result by epsilon nudging, not integer arithmetic.**
  It computes `floor((p - a0 + p_tol + a0_tol) / (h - h_tol))`, inflating the
  numerator and shrinking the denominator so a point on an edge lands on the
  integer rather than a hair below. At this project's coordinates that tolerance
  is about 3.6e-14 degrees, roughly four nanometres, which is far below GeoNet's
  reported precision. It therefore cannot over-correct on real data, and the
  integer path here and pyCSEP's float path will agree.

That pyCSEP carries dedicated tolerance machinery for exactly this problem is
independent evidence that the class of bug is live rather than theoretical.

**Two preconditions that come with using it.** `bin1d_vec` infers spacing as
`bins[1] - bins[0]` and assumes it holds throughout, so a variable grid would
break silently. This project's grid is uniform, so the assumption holds, but it
must not be relaxed. Its docstring also warns that the default tolerance assumes
the points have not been through floating point operations since loading. The
integer path here satisfies that, so the default tolerance is correct for this
project.

**The round-trip test has a specific target.** Phase 2 asserts agreement between
pyCSEP's `get_index_of` and this project's integer binning **on points placed
exactly on cell edges**, since that is the only place the two mechanisms can
diverge. A test using cell interiors would pass while proving nothing.

### D13.3 Gutenberg-Richter estimation

**Estimator: Aki-Utsu maximum likelihood, with the binning correction of half a
magnitude bin.** Closed form and therefore deterministic, standard in the
literature, and what a seismologist expects to see. Least squares on the
cumulative frequency-magnitude distribution is **rejected**: it is known to be
biased, because the cumulative distribution's points are not independent.

**Weichert (1980) maximum likelihood is the designated successor**, not a
replacement. It is built for catalogues with varying completeness periods, so if
the per-cell exposure treatment described in the spec's fitting-window section is
adopted, Weichert is the natural upgrade. It registers as a separate model
scored on identical windows, exactly as ETAS does, rather than silently
replacing the baseline's estimator.

**b is estimated per stratum: one value for shallow, one for deep.** Not global,
because crustal and intraslab populations commonly differ in b and the two
strata are already scored separately. Not per cell, because the separable
forecast representation of D9 shares the magnitude distribution across space by
design, so a per-cell b is neither representable nor wanted.

**Fitting range: from the stratum's measured Mc upward, with an upper cutoff at
M5.5.** The upper cutoff exists because counts above it are too sparse on a
twenty year catalogue to constrain the fit.

**Forecast magnitude distribution: truncated Gutenberg-Richter, bins 0.1 wide,
Mmax frozen at 8.5.** Mmax barely affects expected counts over a daily window,
but it defines the bin structure the M-test consumes, so it must be frozen
rather than left implicit. 8.5 is defensible for a subduction margin.

### D13.4 Events outside the forecast range

**An event above the top magnitude bin is counted and reported separately. It is
never silently dropped, and it never crashes the scorer.**

This is a real failure mode, not a hypothetical one. pyCSEP's `bin1d_vec`
returns -1 for any value above the last bin edge, and `get_index_of` raises
`ValueError` on -1. With Mmax frozen at 8.5 in D13.3 and `right_continuous` left
at its default of `False`, a single M8.5 or greater earthquake would not merely
fall outside the forecast, it would **crash the scoring run**. On a subduction
margin, over a project designed to accumulate evidence for years, that is a
matter of time rather than of chance.

**The decision: keep the top bin closed, and handle out-of-range events
explicitly.** The alternative, setting `right_continuous=True` so the top bin
absorbs the tail, is rejected. A truncated Gutenberg-Richter assigns that bin a
near-zero probability, so an event landing there would be folded into a bin
whose forecast probability was never meaningful, and the M-test would punish it
for a number nobody intended to publish.

Explicit handling is also **consistent with the rule already made for space**.
D1 requires events outside the collection region to be counted and reported
separately rather than dropped, precisely because silently discarding observed
events would flatter the forecast. The magnitude tail gets the same treatment,
for the same reason. A once-in-decades M8.5 or greater event stays visible as
its own line on the scoreboard.

### D13.5 Expander determinism

**Determinism is bit-exact. Conservation is not.** These are different claims
and conflating them produces a test that fails at 3am for no reason.

- **Determinism**: the same input bytes produce the same output bytes, always,
  with no tolerance whatsoever.
- **Conservation**: rate totals agree within a **relative tolerance of 1e-12**.

That tolerance is measured, not chosen. Across b from 0.60 to 1.40 and every
combination of Mmin in {2.5, 3.0, 3.5, 4.0} and Mmax in {7.5, 8.0, 8.5, 9.0},
the worst deviation of the summed bin probabilities from 1.0 is **1.110e-16**,
which is exactly half of float64 machine epsilon, or one unit in the last place.
That is the floor; it cannot be improved. 1e-12 therefore carries roughly 9,000
times headroom on a per-cell check. For the global total over 4,100 cells and 55
bins, pairwise summation bounds the accumulated error near 4e-15, leaving about
250 times headroom. Both are comfortable, and neither is close.

**A zero-rate cell still appears in the dense output, filled with zeros.** The
dense array is always exactly `n_cells * n_magnitude_bins`, unconditionally, with
no exceptions for empty cells. A cell that vanishes from the output is the Phase
1 chunking failure in another dimension: a correctly shaped result that quietly
lost rows.

**Normalisation uses the closed-form truncated Gutenberg-Richter expression, not
division by a computed sum.** Measured, the two are numerically indistinguishable
on non-empty cells, both bottoming out at 1.110e-16. They differ entirely on the
empty case: closed form multiplied by a zero rate yields clean zeros with no NaN,
while dividing weights by their own sum raises `ZeroDivisionError` when that sum
is zero. The closed form removes the degenerate branch rather than guarding it.

The reproducibility claim asserts byte-identical output, which requires five
further things pinned rather than one.

1. **Iteration order is explicit**: sorted by cell identifier, then by magnitude
   bin. Never dictionary order, never set order, never filesystem order.
2. **dtype is pinned to float64 explicitly**, never inherited from a platform
   default.
3. **Reduction order is fixed.** Floating point addition is not associative, so
   every sum over cells or bins has a specified order. Normalising bins to the
   cell rate is a reduction, and it is the one most likely to change silently
   across a library upgrade.
4. **The hash covers canonical array bytes, not the file.** Specifically
   `numpy.ascontiguousarray(arr, dtype="<f8").tobytes()`, little-endian
   explicit. Parquet embeds writer version and compression metadata, so hashing
   the file would break the reproducibility test on a pyarrow upgrade while the
   numbers were untouched, which would discredit the test rather than the data.
5. **Dependencies are lockfile pinned** for numpy, pyarrow and pyCSEP, because
   determinism is a claim about a dependency set as much as about this code.

**The property tests are written before the implementation.** Two matter most:
that expansion is a pure function of committed bytes, and that separable to
dense round-trips exactly. Both would otherwise be properties of how the code
happens to be written, which is exactly how the Phase 1 chunking rewrite lost
the emptiness guarantee that delegation had been providing for free.
