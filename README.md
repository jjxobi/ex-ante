# Ex Ante

A publicly scored earthquake rate forecast for New Zealand.

*Ex ante*: before the event. The opposite of how a backtest reasons.

---

## The problem this exists to solve

Every portfolio shows a backtest. Backtests are cheap and unfalsifiable: you fit
a model to historical data, tune it until the number looks good, and publish the
number. Nobody can check whether you tuned until it worked, because the outcome
was already known when you built the model.

This project inverts that. Forecasts are published and independently timestamped
**before** the window they describe begins, then scored in public against the
same statistical tests seismologists use. A reader can clone this repository and
verify for themselves that the prediction predates the outcome.

There is nowhere to hide, which is the entire point.

---

## What this does not do

**It does not predict earthquakes.** Predicting the time, place and magnitude of
an individual earthquake is not currently possible, and nothing here attempts it.

**It forecasts rates, not events.** The output is an expected number of
earthquakes per grid cell per time window. "0.03 events expected in this cell
tomorrow" is not a claim that an earthquake will or will not happen there.

**It is not an early warning system.** GeoNet operates New Zealand's official
hazard monitoring. Nothing here should inform any safety decision.

**It does not cover all of New Zealand.** 41 percent of national seismicity above
threshold is deliberately excluded. See below.

---

## The most important thing on this page

The original design forecast M3.5 and above across the whole New Zealand region.
Measuring completeness region by region showed that would have been invalid.

The Kermadec arc and its offshore extension have a magnitude of completeness
between M2.9 and M4.3 depending on the cell, so more than a quarter of the
original target set sat below the completeness of the ground it stood on. Worse,
that incompleteness is not stable: it measured 3.2, 3.4, 4.3, 2.7 and 3.1 across
2005 to 2025, a swing of 1.6 magnitude units. That rules out the usual argument
that incompleteness cancels between fitting and evaluation.

So the collection region is defined by a completeness rule rather than a
bounding box, and **41 percent of the national event count was cut.**

That cost is the point. A forecast that claims to be checkable has to survive its
own author's scrutiny first. Cutting half the map because the data will not
support a claim there is the strongest single piece of evidence that the
scoreboard can be trusted.

Full reasoning in [DECISIONS.md](DECISIONS.md), section D1.

---

## How it works

```
scheduler (daily)
  |
  +-- ingest      GeoNet catalogue          -> parquet snapshot
  +-- transform   dbt on DuckDB             -> cleaned, tested, filtered
  +-- region      frozen grid and boundary  -> asserted against a hash
  +-- forecast    fit                       -> rate per cell
  +-- publish     commit and timestamp at T minus 2 hours
  +-- score       pyCSEP tests once the window has closed and settled
  +-- render      static JSON for the site
  +-- health      fail loudly if the newest forecast is over 48 hours old
```

No database server. Snapshots, forecasts and scores are files, because files in
git are what make the timestamps externally checkable.

### Three rules the whole thing rests on

1. **Forecasts are never backfilled.** A missed window stays permanently
   unforecast and is rendered as a gap. A missing forecast is honest; a late one
   is indistinguishable from fraud to a reader.
2. **History is audited in CI.** A test walks the full git history and asserts
   every forecast's timestamp precedes its window. Commit timestamps alone are
   forgeable, so an Actions run id and an OpenTimestamps proof are recorded too.
3. **Scoring reads only committed bytes.** The frozen evaluation catalogue ships
   next to the score, so anyone re-running gets the same number.

---

## Status

**Phase 1 complete**: the catalogue pipeline. 61,191 events, magnitude 3.0 and
above, spanning 2005-01-01 to 2026-08-03, ingested into validated parquet with a
dbt contract layer that fails loudly when the data or the upstream API changes.

Phases 2 to 6, the baseline forecast, the evaluation harness, unattended
operation, ETAS and the public page, are not built yet.

Nothing is linked publicly until it has run unattended for 14 consecutive days.

---

## Repository map

| Path | What it is |
|---|---|
| [DECISIONS.md](DECISIONS.md) | The frozen constitution. Every value fixed before the first forecast, and why. Start here. |
| [docs/phase-1-findings.md](docs/phase-1-findings.md) | What building it taught us, including the defects caught before they shipped. |
| [docs/runbook.md](docs/runbook.md) | How to run it, and what to do when a test fails. |
| [docs/specs/](docs/specs/) | The design specification. |
| [docs/phase-2-preconditions.md](docs/phase-2-preconditions.md) | What must be settled before Phase 2 code exists. |
| [scripts/measurements/](scripts/measurements/) | Regenerates every figure quoted anywhere in the docs. Standard library only. |
| `src/eq/` | Ingest, parsing, storage, revision diffing. |
| `dbt/` | Models and the data contract tests. |

---

## Reproducing any number

Every figure in the documentation was measured against the live GeoNet service
and is regenerable:

```bash
python scripts/measurements/<script>.py
```

Those scripts use only the Python standard library, deliberately, so a reader can
check the numbers without building an environment first. See
[scripts/measurements/README.md](scripts/measurements/README.md) for what each
one establishes and a suggested reading order.

## Running the pipeline

```bash
python -m venv .venv
python -m pip install -e ".[dev]"

python -m eq.cli snapshot          # pull the catalogue
cd dbt && dbt build --profiles-dir .   # build and test the models
```

Full detail, including what to do when a data contract test fails, is in
[docs/runbook.md](docs/runbook.md). The short version: do not adjust the test
bounds to make it pass.

---

## Data source

GeoNet, which is New Zealand's geological hazard monitoring programme, funded by
the Earthquake Commission and operated by GNS Science. Catalogue data is used
under GeoNet's terms. This project is not affiliated with or endorsed by GeoNet.
