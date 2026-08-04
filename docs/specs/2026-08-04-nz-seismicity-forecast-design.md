# NZ Seismicity Forecast: Design Specification

Date: 2026-08-04
Status: approved, not yet implemented
Scope: Phases 1 to 4 (catalogue pipeline, baseline forecast, evaluation harness, unattended live operation)

---

## 1. What this project is

A publicly scored earthquake rate forecast for New Zealand.

Every day and every week, the system publishes a probabilistic forecast of how many earthquakes of magnitude 3.5 or greater will occur in each cell of a fixed spatial grid. The forecast is committed to git **before** the window it describes begins. After the window closes and the catalogue has settled, the forecast is scored in public using the same statistical tests that professional seismologists use to evaluate forecast models.

### Why this is different from a backtest

A backtest is unfalsifiable. You fit a model to historical data, tune it until the number looks impressive, and publish the number. Nobody can check whether you tuned until it worked, because the outcome was already known when you built the model.

This project inverts that. The forecast exists, cryptographically timestamped in git history, before the earthquakes happen. A reader can clone the repository, check the commit log, and verify for themselves that the prediction predates the outcome. There is nowhere to hide.

### Why it gets better with time

Almost every portfolio project is worth the same on the day it ships as it is a year later. This one is worth more every day it keeps running, because the evidence base grows. Fourteen scored forecasts prove very little. Four hundred prove something real.

---

## 2. What this project explicitly does not do

This must be stated on the public page, in the README, and in this spec, because overclaiming here is the fastest way to destroy the project's credibility.

**This does not predict earthquakes.** Predicting the time, place and magnitude of an individual earthquake is not currently possible, and no part of this system attempts it.

**This forecasts rates, not events.** The output is an expected number of earthquakes per grid cell per time window. A statement like "0.03 events expected in this cell tomorrow" is not a statement that an earthquake will or will not happen there.

**This is not an early warning system.** GeoNet operates New Zealand's official hazard monitoring. Nothing here should be used for any safety decision.

**This is not comparable to published CSEP results for New Zealand.** The reference pyCSEP New Zealand case study evaluates five-year forecasts at Mw 4.95 and above, depth 40 km and shallower. This project uses a different threshold, a different depth treatment and much shorter windows. The numbers are not interchangeable and the page will say so.

---

## 3. Frozen decisions

These are the constitution of the project. They are frozen before the first forecast is published and they do not change. Changing any of them breaks comparability with every forecast already scored, and would require declaring a new experiment with a new identifier and starting the record over.

They live in `DECISIONS.md` at the repository root, which is committed before any forecast file exists.

### 3.1 Magnitude threshold: M 3.5 and above

**The decision.** Only earthquakes of magnitude 3.5 or greater are forecast and scored.

**Why a threshold is needed at all.** GeoNet's catalogue is not complete down to arbitrarily small magnitudes. Below some magnitude, real earthquakes happen that the sensor network does not record. That magnitude is called the magnitude of completeness, written Mc. Forecasting below Mc produces meaningless numbers, because the model is trying to predict events that would not appear in the catalogue even if they occurred.

**How Mc was measured.** Using 48,888 events from 2023 to 2026, the frequency-magnitude distribution was built in 0.1 magnitude bins and Mc estimated by maximum curvature, which takes the modal bin of the non-cumulative distribution. Critically, this was done separately for each depth stratum, because completeness is not uniform:

| Stratum | Events | Modal bin (Mc estimate) |
|---|---|---|
| Shallow | 31,324 | M 1.7 |
| Deep | 17,564 | M 2.2 |

Deep seismicity is about half a magnitude less complete than shallow, which is expected: deep events are more often offshore and further from instruments. A single national Mc would have quietly biased one stratum against the other.

**Why 3.5 rather than something closer to Mc.** Margin. Mc varies regionally as well as by depth, and is worse offshore than onshore. M 3.5 clears the measured shallow Mc by 1.8 magnitude units and the deep Mc by 1.3, which absorbs regional variation without a per-region completeness model.

**What raising the threshold does not buy.** It is tempting to raise the threshold further to reduce boundary churn, meaning events whose magnitude is revised across the threshold and therefore enter or leave the target set. This does not work. Under the Gutenberg-Richter law the fraction of the target set within 0.2 magnitude units of the threshold is fixed at roughly 1 minus 10 to the power of -0.2, about 37%, regardless of where the threshold sits. Magnitude uncertainty stays roughly constant in absolute terms. So raising the threshold costs statistical power and buys no protection. It was not done.

**What is done about boundary churn instead.** Every scored window publishes the count of events within 0.2 magnitude units of the threshold, so a reader can see the exposure directly.

### 3.2 Depth strata: two, boundary fitted

**The decision.** Two strata, shallow and deep. Each is a separately registered and separately scored forecast. They are never blended into a single number.

**Why depth matters in New Zealand specifically.** New Zealand sits on a subduction zone. A large fraction of its seismicity happens deep inside the descending slab rather than in the shallow crust. Measured over 2005 to 2025, more than half of M 3.5 and above events are deeper than 40 km. Crustal and slab earthquakes behave differently, most importantly in how they trigger aftershocks: deep intraslab events produce far fewer aftershocks than shallow crustal ones.

**Why two strata and not three or four.** Statistical power. At M 3.5 the whole country produces roughly 2.2 events per day. Splitting that three or four ways leaves each stratum with well under one event per day, which makes the daily N-test uninformative and leaves too few events to fit separate model parameters. Two strata capture the regime change that matters most, crustal against slab, without spending events on a second-order distinction.

**Why separate scoring rather than one blended forecast.** Three reasons. It doubles the scoreboard instead of halving it, for essentially the same pipeline work. It makes the low-power problem explicit rather than hiding it inside a mixed rate. And it turns the project into an answer to a genuine open question: does an ETAS model calibrated on crustal seismicity transfer to subduction seismicity? A scoreboard showing a model performing differently across two tectonic regimes is far more interesting than a single aggregate number.

**Where the boundary goes: fitted, not assumed.** The conventional choice is 40 km, and the raw depth histogram appears to support a boundary somewhere in that region. It cannot be trusted, for a reason detailed in section 3.3. Instead, the boundary is set by an explicit, reproducible criterion implemented in committed code, run once, with the resulting value frozen.

The procedure: using free depths only (see 3.3), minimise the total within-stratum variance of the depth distribution over candidate boundaries from 30 km to 90 km in 1 km steps. The chosen boundary, the full candidate curve, and the code that produced it are all committed. The point is not that this criterion is uniquely correct, it is that the boundary is derived by a rule anyone can re-run rather than by an assertion.

**A known simplification, stated publicly.** A flat horizontal plane is a crude model of the actual geometry. The Hikurangi subduction interface lies roughly 15 to 25 km beneath the east coast of the North Island and deepens westward past 50 km. Any horizontal boundary therefore misassigns some events near the margin. This is acceptable for version one and is disclosed rather than hidden.

**A pre-committed expectation.** ETAS is expected to underperform in the deep stratum, possibly barely beating the time-invariant baseline. This is theory being confirmed, not a bug, because the triggering behaviour that ETAS exists to model is genuinely weaker for deep intraslab events. **This result will be published either way.** Deciding this in advance is what stops it becoming a result that quietly never gets written up.

### 3.3 The depth data quality problem

This is the single most important data-quality finding and it must be carried through the pipeline.

**42% of M 3.5 and above events have operator-assigned depths, not measured ones.** When a solution is poorly constrained, GeoNet fixes the depth to a convention rather than solving for it. The signature is unmistakable in the exact-value counts over 2005 to 2026:

| Fixed depth | Occurrences |
|---|---|
| 33 km | 3,886 |
| 12 km | 2,718 |
| 5 km | 1,962 |
| 100 km | 456 |
| 10 km | 214 |

Nearly half the depth column is convention rather than measurement.

**How this is handled.** The `depthtype` field is carried through every layer of the pipeline and is never dropped. A dbt test asserts on its distribution so that a change in GeoNet's practice is detected rather than silently absorbed. The boundary-fitting procedure in 3.2 uses free depths only, because fitting to a histogram containing a pile of 3,886 events at exactly 33 km would put the boundary on an artifact.

**Why the stratum assignment is nonetheless robust.** 92% of assigned depths fall below 40 km, and the two large piles sit at 33 km and 100 km. For any boundary between those two values, the 33 km pile lands in shallow and the 100 km pile lands in deep. The assignment is therefore insensitive to exactly where in that range the fitted boundary falls.

**What the clean data actually shows.** On free depths only (n = 14,177), counts fall steeply from 1,909 in the 0 to 10 km bin to 495 in the 30 to 40 km bin, then sit on a flat plateau of roughly 210 to 280 per 10 km bin all the way from 40 km to 130 km, before rising to a slab peak of about 500 around 200 to 220 km. The crustal population's decline terminates in the 40 to 50 km range. The global minimum is at 70 to 80 km but it is a shallow minimum within a flat plateau, not a clean bimodal trough. This ambiguity is precisely why the boundary is fitted by a stated rule rather than eyeballed.

### 3.4 Spatial grid

**The decision.** Cells of 0.1 degree by 0.1 degree. The collection region is a frozen set of roughly 6,114 cells, being those cells containing at least one M 3.0 or greater event between 2005 and 2025.

**Why not the full bounding box.** The New Zealand region bounding box contains about 32,800 cells at 0.1 degree resolution, of which only about 6,114 have ever recorded a M 3.0 event in twenty years. Roughly 80% of the box is permanently near-zero ocean. Carrying those cells inflates every forecast file by a factor of five for no information gain.

**Longitude convention.** Longitude is represented continuously on [163.6, 183.0] rather than wrapping to negative values past 180. New Zealand seismicity extends to longitude 182.96, and wrapping at the antimeridian would put a discontinuity through the Kermadec arc, which is seismically active. Every component uses this convention and a test asserts it.

**Out-of-region events.** Events falling outside the frozen collection region are counted and reported separately. They are never silently dropped, because silently dropping observed events would flatter the forecast.

**Frozen means hashed.** The grid definition is generated once, committed, and a SHA-256 of it is recorded in `DECISIONS.md`. Every downstream component asserts the hash matches before running. Accidentally regenerating the grid becomes a loud failure instead of a silent invalidation of the whole record.

### 3.5 Forecast horizons: daily and weekly, weekly leads

**The decision.** Both horizons are produced and scored. The weekly forecast is the headline on the public page. The daily forecast is published and scored but presented as the long-run calibration record.

**Why weekly leads, against the CSEP convention of daily.** At M 3.5 the expected count is roughly 1.0 events per day in the shallow stratum and 1.1 in the deep stratum. An N-test comparing an observed count against an expected count of about one has almost no statistical power. It will be uninformative most days, and not just during quiet periods, this is the normal state. The weekly horizon gives roughly 7.2 and 8.0 expected events per stratum, which is enough for the N-test and the S-test to say something.

**Why daily is kept anyway.** It produces 365 scored windows a year rather than 52, which matters for long-run calibration statistics where individual windows being weak is acceptable. It also gives the commit-timestamp argument a daily cadence, which is more visibly falsifiable.

### 3.6 Catalogue revision policy

This is the subtlest decision in the project and getting it wrong would invalidate every published number.

**The problem.** GeoNet revises magnitudes and locations after human review. The catalogue that exists when a forecast window closes is not the catalogue that will exist a month later. If you score against a moving target, your published scores silently change every time you recompute, and reproducibility becomes a claim you cannot support.

**The measurement.** Using all 1,908 M 3.0 and above events in calendar 2025, the lag from origin time to most recent modification time was computed:

| Statistic | Value |
|---|---|
| Median lag | 19.9 days |
| 75th percentile | 24.9 days |
| 90th percentile | 28.1 days |
| 99th percentile | 30.1 days |
| Maximum | 89.9 days |
| Still revising after 7 days | 83.1% |
| Still revising after 30 days | 1.4% |

An honest caveat on what this measures: `modificationtime` records the most recent touch of the record, so this measures record churn, not specifically magnitude churn. It shows that at T+7 the great majority of records are still moving, which is enough to rule out a T+7 freeze.

**The decision, in three parts.**

1. **The score of record is computed at T+30**, meaning 30 days after the forecast window closes. By then 98.6% of events have reached their final revision.

2. **The evaluation catalogue is snapshotted and committed alongside the score.** Scoring reads that committed file, not a live API. This is the part that actually delivers reproducibility: the remaining 1.4% of drift does not matter, because anyone re-running the evaluation against the exact bytes that were used will reproduce the number exactly.

3. **A provisional score is published at T+7, clearly labelled as unstable**, so the scoreboard is not a month behind. The difference between the T+7 and T+30 score is itself published, which shows readers how much scores actually move.

### 3.7 Forecast representation on disk

**The decision.** Forecasts are stored in separable form: a rate per (cell, stratum) plus the magnitude distribution as fitted Gutenberg-Richter parameters. A deterministic expander inflates this to a full dense pyCSEP gridded forecast at scoring time.

**Why not store the dense forecast.** A conventional dense CSEP forecast over the full box with depth layers and 0.1-width magnitude bins is roughly 4.4 million values per forecast. Two models times two horizons times daily commits puts multiple gigabytes a year into git. That directly breaks the clone-and-reproduce requirement that committing to git was supposed to serve.

**What separable costs.** About 18,000 floats per forecast, roughly 30 KB compressed, about 120 KB per day across both models and both horizons, and roughly 45 MB per year. The repository stays clonable.

**The honest caveat.** Because the magnitude distribution is shared across space, the M-test evaluates the global Gutenberg-Richter fit rather than per-cell magnitude structure. Most operational ETAS forecasts are separable in exactly this way, so this is conventional rather than a dodge, but it is stated publicly rather than glossed.

**Why reproducibility survives.** The expansion is a pure function of committed bytes with no external input, so any reader can regenerate the dense forecast and reproduce the score.

---

## 4. Architecture

No database server. Catalogue snapshots, forecasts and scores are files committed to git, which is what makes the timestamps externally verifiable.

```
scheduler (daily)
  |
  +-- ingest      GeoNet catalogue delta   -> parquet snapshot, committed
  +-- transform   dbt on DuckDB            -> cleaned, tested, completeness filtered
  +-- region      frozen grid + boundary   -> asserted against committed hash
  +-- forecast    fit -> rate per cell     -> separable forecast files
  +-- publish     commit BEFORE the window opens        <- the whole point
  +-- score       pyCSEP tests on windows closed and frozen
  +-- render      static JSON for the site
  +-- health      fail loudly if newest forecast > 48h old
```

Seven components, each with one purpose, a defined interface, and independent tests.

### 4.1 ingest

Pulls from GeoNet and writes raw parquet. Two distinct jobs:

**Delta ingest.** New events since the last run, appended to the catalogue snapshot.

**Full snapshot.** A complete catalogue pull, retained daily. This exists for one reason: it is the only way to build the magnitude-revision-versus-time curve. GeoNet's `api.geonet.org.nz/quake/history/{publicID}` endpoint was tested during design and returns HTTP 200 with zero features for every event tried, recent and historical. Per-event revision history is not exposed. The curve can therefore only be built forward, by snapshotting daily and diffing. Starting these snapshots on day one, before any forecasting works, costs almost nothing and is the only way to get the data.

A documented magnitude-revision-versus-time curve for the GeoNet catalogue does not appear to exist publicly. It justifies the T+30 choice with evidence rather than assertion, and it is a genuinely useful artifact in its own right.

**Reliability.** Quake Search resets connections under sustained querying, observed repeatedly during design measurement. Ingest uses exponential backoff, caches by query, and treats a failed pull as a hard failure rather than silently writing a partial catalogue.

### 4.2 transform

dbt models on DuckDB, staging through to marts. Tests that must pass:

- `publicid` is unique and not null
- magnitude falls within a plausible range
- no future-dated events relative to run time
- origin time is not null and parses as UTC
- no duplicate origin time and location pairs
- `depthtype` distribution is within expected bounds, so a change in GeoNet practice is caught
- longitude falls on the continuous [163.6, 183.0] convention
- freshness: the newest event is not older than an acceptable threshold

Output is clean parquet with the completeness filter applied and stratum assigned.

### 4.3 region

Runs once. Generates the frozen grid and fits the depth boundary by the procedure in 3.2. Commits the grid definition, the boundary value, the full candidate curve from the fit, and a SHA-256 of the grid. Every other component asserts the hash before doing anything.

### 4.4 forecast

Phase 2 ships one model: **time-invariant smoothed seismicity**. A Poisson rate per cell derived from smoothed historical counts, constant in time.

This is not filler. It is the benchmark every later model is measured against, and it is what makes a skill score meaningful. A model that cannot beat a constant rate has demonstrated nothing. It ships to production before anything clever.

Runs per (stratum, horizon), emitting separable forecast files.

### 4.5 publish

Writes the forecast plus a manifest recording model version, input catalogue hash, grid hash, window start and end, and code commit. Commits before the window opens.

### 4.6 score

Runs only on windows that are both closed and past their T+30 freeze. Expands separable to dense, then runs:

- **N-test**, is the total number of events consistent with the forecast
- **S-test**, is the spatial distribution consistent
- **M-test**, is the magnitude distribution consistent
- **L-test**, is the joint likelihood consistent
- **Information gain per earthquake** against the baseline, which is the skill score

Skill against the baseline is reported, not just raw likelihood, because raw likelihood alone does not tell a reader whether the model is any good.

### 4.7 health

Fails if the newest forecast is more than 48 hours old, and opens or updates a GitHub issue when it does.

The specific risk being mitigated: GitHub Actions disables scheduled workflows after roughly 60 days of repository inactivity. Daily forecast commits are probably enough to prevent this, but bot commits are an ambiguous case and the failure mode is silent, which is fatal for a project whose entire premise is uninterrupted operation. If the issue-based check proves unreliable, the fallback is an external trigger such as a Modal or Cloudflare Workers cron calling the workflow.

---

## 5. Integrity rules

Three rules the entire premise rests on. Each is enforced by an automated test, not by discipline, because discipline fails at 3am on a Sunday fourteen months in.

### Rule 1: forecasts are never backfilled

If the scheduler misses a window, that window is permanently unforecast. It is recorded as a gap and rendered as a gap on the scoreboard.

A missing forecast is honest. A forecast committed after the window it describes is fraud, whether or not it was intended as such, because a reader cannot distinguish the two. The publish step refuses to write a forecast whose window has already started.

### Rule 2: history is audited in CI

A test walks the entire git history and asserts, for every published forecast, that its commit timestamp precedes its window start. This runs on every CI invocation.

This test failing is a project-level emergency, not a flaky test to be retried. It is the single check that substantiates the project's central claim.

### Rule 3: scoring reads only committed bytes

The T+30 evaluation catalogue snapshot is committed next to the score. Scoring reads that file. It never queries a live API at scoring time.

This is what makes "a reader can clone the repo and reproduce any published score" true rather than aspirational.

---

## 6. Repository layout

```
DECISIONS.md              frozen constitution, committed before first forecast
README.md                 what this is, what it does not do, how to reproduce
docs/
  specs/                  this document and successors
  methodology.md          the public-facing explanation of every choice
  glossary.md             plain-language definitions of every technical term
data/
  raw/                    ingested catalogue snapshots
  snapshots/              daily full-catalogue pulls for the revision curve
  evaluation/             T+30 frozen catalogues, one per scored window
forecasts/
  baseline/               separable forecast files by horizon and stratum
scores/                   evaluation results, provisional and of record
region/
  grid.parquet            frozen collection region
  boundary.json           fitted depth boundary and candidate curve
src/                      the seven components
scripts/
  measurements/           regenerates every figure quoted in the spec
tests/                    unit, integration, and the history audit
dbt/                      models, tests, sources
.github/workflows/        scheduler, health check, CI
site/                     rendered static JSON
```

---

## 7. Documentation requirements

This is a portfolio project. It has to be legible to three distinct audiences, and the documentation is a deliverable rather than an afterthought.

**A seismologist** must be able to check the method and find it defensible. This is served by `docs/methodology.md`, this spec, and honest statements of every limitation.

**A hiring engineer** must be able to see the engineering judgement quickly. This is served by the README leading with the integrity rules, the CI history audit, and the reasoning behind the frozen decisions.

**You, six months from now, mid-conversation, without preparation.** This is the requirement most projects fail. Every document must explain not just what was decided but why, what was rejected, and what the evidence was. `docs/glossary.md` defines every technical term in plain language: Mc, Gutenberg-Richter, Poisson rate, N-test, S-test, M-test, L-test, information gain, ETAS, Omori decay, declustering, subduction slab, intraslab. Every measured number in this spec is reproducible by a committed script, so if you are asked "where did 83% come from" the answer is a file path, not a memory.

The rule for all project prose: no em dashes.

---

## 8. Phase scope and acceptance criteria

### Phase 1: catalogue pipeline

Ingest the historical GeoNet catalogue. dbt models on DuckDB with the tests listed in 4.2. Daily full snapshots started.

Done when: clean parquet is produced end to end, every dbt test passes, freshness check works, and the ingest survives a simulated GeoNet outage without writing partial data.

### Phase 2: baseline forecast

Frozen grid and fitted depth boundary committed and hashed. Time-invariant smoothed seismicity model producing separable forecasts per stratum and horizon. Expander implemented and tested for determinism.

Done when: a forecast can be generated, published, and expanded to a dense pyCSEP forecast that round-trips identically.

### Phase 3: evaluation harness

pyCSEP N, S, M and L tests wired up, plus information gain per earthquake against the baseline. T+30 freeze logic and evaluation catalogue snapshotting. Provisional T+7 scoring with drift reporting.

Built before any sophisticated model, so that the sophisticated model is measured from its first day rather than assessed on vibes.

Done when: a closed and frozen window can be scored reproducibly from committed bytes alone, verified by scoring the same window twice from a clean clone and getting identical output.

### Phase 4: go live with the baseline

Scheduler running. Forecasts committing daily and weekly. Evaluation running on closed windows. Health check live and proven to fire.

Not linked from the site.

Done when: **14 consecutive days of unattended operation with zero manual intervention**, the history audit passes over the full record, and the health check has been deliberately triggered at least once to prove it actually alerts.

---

## 9. Testing strategy

- **dbt tests** on every model, run in CI, blocking.
- **Unit tests** for the expander, specifically that expansion is deterministic and that separable to dense round-trips.
- **Property tests** on the grid: every event in the catalogue maps to exactly one cell or is explicitly counted out of region.
- **The history audit** from Rule 2, run on every CI invocation.
- **Reproducibility test**: score a known window from a clean clone and assert byte-identical output against the committed score.
- **Failure injection**: ingest against a mocked failing GeoNet, publish against an already-started window, both must fail loudly.

---

## 10. Operational notes

**pyCSEP dependencies.** pyCSEP 0.8.0 pulls cartopy, obspy, rasterio, shapely and pyproj. These are reliable on Linux and historically painful on Windows. Scoring therefore runs in CI on Linux. Local development uses a conda environment, since the primary development machine is Windows.

**Commit authorship.** All commits, including those made by the scheduled workflow, are authored `Jesse O'Brien <jesob5@gmail.com>`.

**Prose convention.** No em dashes anywhere in the project.

---

## 11. Definition of done for this spec's scope

- 14 consecutive days of unattended operation, zero manual intervention, before the site links to anything.
- Every forecast's commit timestamp provably precedes its forecast window, verified by an automated audit over full history.
- Baseline scored on both horizons and both strata, with skill reported rather than raw likelihood alone.
- A reader can clone the repo and reproduce any published score from committed bytes.
- The public-facing documentation states plainly that this forecasts rates, not individual events.

---

## 12. Accepted risks

**Quiet periods.** In weeks with few M 3.5 events the N-test has almost no power. The scoreboard will look uninformative for stretches. This is accepted and will be said out loud on the page rather than smoothed over.

**Low daily power is structural.** At roughly one event per day per stratum, the daily N-test is weak by construction, not just during quiet spells. This is why weekly leads.

**ETAS may not beat the baseline**, particularly in the deep stratum. Pre-committed in 3.2 to publishing this either way.

**Effort is front-loaded.** Phases 1 to 4 are the real work. If the project stalls, it should stall after Phase 4, so that something is running and accumulating evidence.

---

## Appendix A: measurements behind the frozen decisions

Every number in this spec came from a query run on 2026-08-04 against GeoNet Quake Search, bounding box `163.60840,-49.18170,182.98828,-32.28713`. The scripts that produced them are committed under `scripts/measurements/` so that any figure can be regenerated.

**Event rates, calendar 2025, all depths**

| Threshold | Events/year | Per day | Per week |
|---|---|---|---|
| M 2.5 | 4,257 | 11.66 | 81.9 |
| M 3.0 | 1,908 | 5.23 | 36.7 |
| M 3.5 | 820 | 2.25 | 15.8 |
| M 4.0 | 298 | 0.82 | 5.7 |
| M 4.5 | 96 | 0.26 | 1.8 |

**Per-stratum rates at M 3.5, 2023 to 2026 average**

| Stratum | Per day | Per week |
|---|---|---|
| Shallow | 1.03 | 7.2 |
| Deep | 1.14 | 8.0 |

**Catalogue coverage.** 57,961 M 3.0 and above events from 2005 to 2025, occupying 6,114 distinct 0.1 degree cells out of roughly 32,800 in the bounding box. Longitude range 163.82 to 182.96, latitude range -49.17 to -32.29.

**Revision lag and depth-type distributions** are given in sections 3.6 and 3.3 respectively.

---

## Appendix B: what is deliberately out of scope here

Phase 5 (ETAS) and Phase 6 (the public page) are not covered by this spec. They get their own specs once Phase 4 has been running unattended and the forecast schema has survived contact with a real evaluation harness.

The frozen decisions in section 3 bind those phases too. ETAS registers as an additional model scored on identical windows against the same grid, thresholds and strata.
