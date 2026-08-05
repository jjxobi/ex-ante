# NZ Seismicity Forecast: Design Specification

Date: 2026-08-04
Status: approved, revised after review, not yet implemented
Scope: Phases 1 to 4 (catalogue pipeline, baseline forecast, evaluation harness, unattended live operation)

Frozen values live in [`DECISIONS.md`](../../DECISIONS.md), which is
authoritative. This document explains the reasoning, the architecture, and what
gets built.

---

## 1. What this project is

A publicly scored earthquake rate forecast for New Zealand.

Every day and every week, the system publishes a probabilistic forecast of how
many earthquakes of magnitude 3.0 or greater will occur in each cell of a fixed
spatial grid. The forecast is committed and independently timestamped **before**
the window it describes begins. After the window closes and the catalogue has
settled, the forecast is scored in public using the same statistical tests
seismologists use to evaluate forecast models.

### Why this is different from a backtest

A backtest is unfalsifiable. You fit a model to historical data, tune it until
the number looks impressive, and publish the number. Nobody can check whether
you tuned until it worked, because the outcome was already known when you built
the model.

This project inverts that. The forecast exists, independently timestamped,
before the earthquakes happen. A reader can clone the repository and verify for
themselves that the prediction predates the outcome. There is nowhere to hide.

### Why it gets better with time

Almost every portfolio project is worth the same on the day it ships as it is a
year later. This one is worth more every day it keeps running, because the
evidence base grows. Fourteen scored forecasts prove very little. Four hundred
prove something real.

---

## 2. What this project explicitly does not do

Stated on the public page, in the README, and here, because overclaiming is the
fastest way to destroy the project's credibility.

**This does not predict earthquakes.** Predicting the time, place and magnitude
of an individual earthquake is not currently possible, and no part of this
system attempts it.

**This forecasts rates, not events.** The output is an expected number of
earthquakes per grid cell per window. "0.03 events expected in this cell
tomorrow" is not a statement that an earthquake will or will not happen there.

**This is not an early warning system.** GeoNet operates New Zealand's official
hazard monitoring. Nothing here should be used for any safety decision.

**This does not cover all of New Zealand.** 41% of national seismicity above
threshold is deliberately excluded, because the catalogue is not complete there.
See section 3.

**This is not comparable to published CSEP results for New Zealand.** The
reference pyCSEP New Zealand case study evaluates five-year forecasts at Mw 4.95
and above, depth 40 km and shallower. Different threshold, different depth
treatment, much shorter windows. The numbers are not interchangeable.

---

## 3. The exclusion, which is the most important thing on this page

The original design forecast M3.5 and above across the full New Zealand region.
Measuring completeness region by region showed that would have been invalid.

The Kermadec arc and its offshore extension have a magnitude of completeness
between M2.9 and M4.3 depending on the cell. **More than a quarter of the
original target set sat below the completeness of the ground it stood on.**
Worse, that incompleteness is not stable: Kermadec Mc measured 3.2, 3.4, 4.3,
2.7 and 3.1 in 2005, 2010, 2015, 2020 and 2025, a swing of 1.6 magnitude units.
That rules out the usual escape hatch, which is that incompleteness cancels
between fitting and evaluation as long as it holds steady.

So the collection region is defined by a completeness rule rather than a
bounding box: a 1 degree cell is included only if it has at least 150 events in
the reference window and a measured Mc of 2.6 or lower. 41 cells of 145 qualify.
The 11 that fail are all in the northeast and are named individually in
`DECISIONS.md`.

**This removes 41% of the national event count above threshold.**

That cost is the point. The project's entire thesis is that published forecasts
should be checkable, and the first thing a checkable forecast has to survive is
its own author's scrutiny. Cutting half the map because the data will not
support a claim there is the strongest single piece of evidence that the
scoreboard can be trusted. It belongs near the top of the README and the public
page, not in a footnote.

The threshold decision followed from the same measurement. Inside the retained
region, completeness is M1.8 shallow and M2.2 deep, and stable or improving
across twenty years. That supports M3.0, which was frozen against a rule agreed
before the measurement was taken.

---

## 4. Architecture

No database server. Catalogue snapshots, forecasts and scores are files
committed to git and independently timestamped.

```
scheduler (daily)
  |
  +-- ingest      GeoNet catalogue delta   -> parquet snapshot, committed
  +-- transform   dbt on DuckDB            -> cleaned, tested, completeness filtered
  +-- region      frozen grid + boundary   -> asserted against committed hash
  +-- forecast    fit -> rate per cell     -> separable forecast files
  +-- publish     commit and timestamp at T-2h    <- the whole point
  +-- score       pyCSEP tests on windows closed and frozen
  +-- render      static JSON for the site
  +-- health      fail loudly if newest forecast > 48h old
```

Seven components, each with one purpose, a defined interface, and independent
tests.

### 4.1 ingest

**Delta ingest.** New events since the last run, appended to the snapshot.

**Full snapshot, daily.** Retained for one reason: it is the only way to build
the magnitude-revision-versus-time curve. GeoNet's
`api.geonet.org.nz/quake/history/{publicID}` was tested during design and
returns HTTP 200 with zero features for every event tried. Per-event revision
history is not exposed, so the curve can only be built forward by snapshotting
and diffing. Starting on day one costs almost nothing and is the only way to get
the data. No such curve appears to exist publicly for the GeoNet catalogue.

**What is committed is the diff, not the snapshot.** A full snapshot is 2.83 MB
compressed, so committing one daily would cost about 1 GB per year and 5 GB over
five years. That breaks the clone-and-reproduce requirement, which is the same
constraint that forced the separable forecast format in D9. Full snapshots are
therefore local, ephemeral pipeline input. What is committed is the daily
revision diff: events new since the previous snapshot, plus every event whose
magnitude, depth, location or evaluation status changed. That is the actual
content of the revision curve, it costs a few KB per day, and the curve stays
fully reproducible from committed bytes.

This is a deliberate narrowing of the general statement that catalogue snapshots
are committed. **Evaluation catalogues are unaffected**: the T+45 frozen
catalogue for a scored window is small, and D7 requires it to be committed
alongside its score. That requirement stands unchanged.

**Reliability.** Quake Search resets connections under sustained querying,
observed repeatedly during design measurement. Exponential backoff, caching by
query, and a failed pull is a hard failure rather than a silent partial write.

### 4.2 transform

dbt models on DuckDB. Tests that must pass:

- `publicid` unique and not null
- magnitude within a plausible range
- no future-dated events relative to run time
- origin time not null and parses as UTC
- no duplicate origin time and location pairs
- `depthtype` distribution within expected bounds, so a change in GeoNet
  practice is caught rather than silently absorbed
- longitude on the continuous [163.6, 183.0] convention
- freshness: newest event not older than threshold

Output is clean parquet with the completeness filter applied, region membership
resolved, and stratum assigned.

### 4.3 region

Runs once. Applies the D1 completeness rule and the D3 boundary fit, then
commits the grid, the boundary, the full sensitivity curve, and a SHA-256 of the
grid. Every other component asserts the hash before doing anything.

### 4.4 forecast

Phase 2 ships one model: **time-invariant smoothed seismicity**. A Poisson rate
per cell from smoothed historical counts, constant in time, fitted on the
undeclustered catalogue per D8.

Not filler. It is the benchmark every later model is measured against and what
makes a skill score meaningful. A model that cannot beat a constant rate has
demonstrated nothing. It ships before anything clever.

#### Fitting window, and the defect it repairs

The region is qualified on 2021 to 2026 but four retained cells were not
complete over the full historical catalogue, as recorded in the D1 defect note.
The region is frozen, so the repair lives here in the model layer.

**Phase 2 ships fit-window truncation.** The earliest year at which *every*
retained cell satisfies Mc 2.6 or lower is **2019**, so the baseline fits on
2019 to 2026. Uniform treatment, no per-cell machinery, and the completeness
rule then holds over exactly the data the model sees.

The cost is recorded honestly, because it is large. Truncation supplies 8,615
events. Per-cell exposure over the same region supplies 42,463, which is
**4.93 times more**, because only two cells actually have short exposure:
(178, -37) from 2019 and (179, -38) from 2017. The other 39 are complete from
2005. Truncation discards 14 of 21 years for 39 cells that never needed it.

**Exposure correction registers later as a separate model**, scored on identical
windows against the same baseline. Each cell contributes its own complete-period
duration, so the rate is the count over that cell's own exposure rather than
over a common window. This is standard practice for catalogues with varying
completeness, following Weichert (1980), which framed it for
magnitude-dependent completeness periods; the same machinery applies when
completeness varies spatially.

Registering it as a second model rather than silently replacing the first is
what the architecture is for. The defect becomes a second entry on the
scoreboard and a direct public measurement of what the correction is worth,
instead of a quiet fix nobody can audit.

**The trap, recorded so it is not walked into.** Under per-cell exposure you
cannot smooth raw counts, because exposure differs between neighbouring cells
and the kernel would bleed a 21-year count into a 7-year cell. Counts and
exposure must be smoothed as separate fields and divided afterwards. Doing it in
the other order reintroduces exactly the bias being removed, in a form that is
much harder to see.

### 4.5 publish

Writes the forecast plus a manifest recording model version, input catalogue
hash, grid hash, window start and end, code commit, and **GitHub Actions run
ID**. Requests an **OpenTimestamps** attestation. Publishes at T minus 2 hours.

### 4.6 score

Runs only on windows both closed and past their T+45 freeze. Expands separable
to dense, then runs pyCSEP's **N**, **S**, **M** and **L** tests plus
**information gain per earthquake** against the baseline.

Skill against the baseline is reported, not just raw likelihood, because raw
likelihood alone does not tell a reader whether the model is any good.

### 4.7 health

Fails if the newest forecast is more than 48 hours old, opening or updating a
GitHub issue.

The risk: GitHub Actions disables scheduled workflows after roughly 60 days of
repository inactivity. Daily commits probably prevent this, but bot commits are
an ambiguous case and the failure mode is silent, which is fatal for a project
whose premise is uninterrupted operation. Fallback is an external trigger such
as a Modal or Cloudflare Workers cron calling the workflow.

---

## 5. Integrity rules

Enforced by automated tests, not discipline, because discipline fails at 3am on
a Sunday fourteen months in.

### Rule 1: forecasts are never backfilled

Per D11. The publish step refuses to write a forecast whose window has already
started. The refusal boundary is the window start, not the run time, so ordinary
scheduler jitter costs nothing.

### Rule 2: history is audited in CI

A test walks the entire git history and asserts, for every published forecast,
that its commit timestamp precedes its window start, **and** that its recorded
Actions run ID and OpenTimestamps proof both verify.

Commit timestamps alone are forgeable, so the commit check is the weakest of the
three and is treated as a cheap first pass rather than as proof. This test
failing is a project-level emergency.

### Rule 3: scoring reads only committed bytes

The T+45 evaluation catalogue snapshot is committed next to the score. Scoring
reads that file and never a live API.

---

## 6. Repository layout

```
DECISIONS.md              frozen constitution, authoritative
README.md                 what this is, what it does not do, how to reproduce
docs/
  specs/                  this document and successors
  methodology.md          public-facing explanation of every choice
  glossary.md             plain-language definitions of every technical term
data/
  raw/                    ingested catalogue snapshots
  snapshots/              daily full-catalogue pulls for the revision curve
  evaluation/             T+45 frozen catalogues, one per scored window
forecasts/
  baseline/               separable forecast files by horizon and stratum
scores/                   evaluation results, provisional and of record
region/
  grid.parquet            frozen collection region
  boundary.json           fitted depth boundary and sensitivity curve
src/                      the seven components
scripts/
  measurements/           regenerates every figure quoted in the docs
tests/                    unit, integration, and the history audit
dbt/                      models, tests, sources
.github/workflows/        scheduler, health check, CI
site/                     rendered static JSON
```

---

## 7. Documentation requirements

Documentation is a deliverable, not an afterthought. Three audiences:

**A seismologist** must be able to check the method and find it defensible.
Served by `docs/methodology.md`, `DECISIONS.md`, and honest limitations.

**A hiring engineer** must see the engineering judgement quickly. Served by the
README leading with the exclusion, the integrity rules, and the CI history
audit.

**You, six months from now, mid-conversation, without preparation.** The
requirement most projects fail. Every document explains what was decided, why,
what was rejected, and what the evidence was. `docs/glossary.md` defines every
technical term in plain language: Mc, Gutenberg-Richter, maximum curvature,
Poisson rate, N-test, S-test, M-test, L-test, information gain, skill score,
ETAS, Omori decay, declustering, subduction slab, intraslab, KDE, Silverman's
rule. Every measured number is reproducible by a committed script, so "where did
that come from" is answered with a file path rather than a memory.

Prose convention: no em dashes anywhere in the project.

---

## 8. Phase scope and acceptance criteria

### Phase 1: catalogue pipeline

Ingest the historical catalogue. dbt models with the tests in 4.2. Daily full
snapshots started.

Done when: clean parquet end to end, every dbt test passes, freshness check
works, and ingest survives a simulated GeoNet outage without writing partial
data.

### Phase 2: baseline forecast

Region rule and boundary fit executed, committed and hashed. Time-invariant
smoothed seismicity per stratum and horizon. Expander implemented and tested.

Done when: a forecast can be generated, published, and expanded to a dense
pyCSEP forecast that round-trips identically.

### Phase 3: evaluation harness

pyCSEP N, S, M, L plus information gain against the baseline. T+45 freeze and
evaluation snapshotting. Provisional T+7 scoring with drift reporting.

Built before any sophisticated model, so the sophisticated model is measured
from its first day rather than assessed on vibes.

Done when: a closed and frozen window scores reproducibly from committed bytes
alone, verified by scoring twice from a clean clone and getting identical output.

### Phase 4: go live with the baseline

Scheduler running. Forecasts committing and timestamping daily and weekly.
Evaluation running on closed windows. Health check live and proven to fire.

Not linked from the site.

Done when: **14 consecutive days of unattended operation with zero manual
intervention**, the history audit passes over the full record including
OpenTimestamps verification, and the health check has been deliberately
triggered at least once to prove it alerts.

---

## 9. Testing strategy

- **dbt tests** on every model, in CI, blocking.
- **Unit tests** for the expander: expansion deterministic, separable to dense
  round-trips.
- **Property tests** on the grid: every catalogue event maps to exactly one cell
  or is explicitly counted out of region.
- **Completeness assertion over the fitting window.** Every retained cell must
  have measured Mc of 2.6 or lower over the actual period used for fitting. This
  fails loudly if either the region or the fitting window moves. It is an
  assertion rather than a note because this is the defect recorded in D1, and it
  is the class of bug that recurs when someone later extends the fit backwards
  in search of more data.
- **The history audit** from Rule 2, every CI invocation.
- **Reproducibility test**: score a known window from a clean clone, assert
  byte-identical output against the committed score.
- **Failure injection**: ingest against a mocked failing GeoNet, publish against
  an already-started window. Both must fail loudly.

---

## 10. Operational notes

**pyCSEP dependencies.** pyCSEP 0.8.0 pulls cartopy, obspy, rasterio, shapely
and pyproj.

This section previously said those were reliable on Linux and historically
painful on Windows, and concluded that scoring would run only in CI with a
conda environment for local work. **That was assumed rather than tested, and it
is wrong.** `pip install --user pycsep` succeeds on Windows and installs
cartopy 0.25, obspy 1.5 and rasterio 1.4 without incident. The `--user` flag is
needed only because this machine's system Python has a non-writable Scripts
directory, which is a local permissions matter rather than anything to do with
pyCSEP.

The consequence is worth having: the tests that check this project's integer
cell binning against pyCSEP's own now run everywhere instead of skipping on the
development machine. Two of them were wrong when first executed, because they
had been written against an API read from source rather than exercised;
`CartesianGrid2D.from_origins` requires a numpy array, not a list of tuples.
That is the argument for installing a dependency rather than reasoning about
it.

**Commit authorship.** Scheduled workflow commits are authored
`github-actions[bot]`, human commits `Jesse O'Brien <jesse@jesse-obrien.com>`.
Deliberately distinguishable, so any human touch in the forecast history is
visible.

---

## 11. Definition of done for this scope

- 14 consecutive days unattended, zero manual intervention, before the site
  links to anything.
- Every forecast's existence before its window provably established by Actions
  run ID and OpenTimestamps, not commit metadata alone.
- Baseline scored on both horizons and both strata, skill reported rather than
  raw likelihood alone.
- A reader can clone the repo and reproduce any published score from committed
  bytes.
- Public documentation states plainly that this forecasts rates, not individual
  events, and that 41% of national seismicity is excluded and why.

---

## 12. Accepted risks

**Quiet periods.** In weeks with few events the N-test has little power. The
scoreboard will look uninformative for stretches. Said out loud rather than
smoothed over.

**Daily windows are weak by construction**, and daily and weekly are not
independent evidence. Per D6, the page reports effective evidence honestly
rather than quoting 417 windows as if independent.

**ETAS may not beat the baseline**, particularly in the deep stratum.
Pre-committed in D3 to publishing either way.

**The region rule is partly activity-defined.** 93 cells were excluded for
insufficient data rather than proven incompleteness. Documented in D1 as a real
property of the rule.

**Effort is front-loaded.** Phases 1 to 4 are the real work. If the project
stalls it should stall after Phase 4, so something is running and accumulating
evidence.

---

## Appendix: what is out of scope here

Phase 5 (ETAS) and Phase 6 (the public page) get their own specs once Phase 4
has run unattended and the forecast schema has survived contact with a real
evaluation harness.

The frozen decisions bind those phases too. ETAS registers as an additional
model scored on identical windows against the same grid, threshold and strata.
