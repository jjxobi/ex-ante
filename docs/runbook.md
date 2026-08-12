# Runbook

Plain instructions for running this pipeline by hand, written so that six
months from now nothing has to be reconstructed from memory.

## One-time setup

```bash
python -m venv .venv
.venv/Scripts/activate        # Windows
python -m pip install -e ".[dev]"
```

## Take a catalogue snapshot

```bash
python -m eq.cli snapshot
```

Writes `data/snapshots/catalogue-YYYY-MM-DD.parquet`. Safe to run repeatedly:
the same date overwrites atomically, and a failure leaves the previous file
untouched. The full catalogue is fetched in five year chunks, because
GeoNet's Quake Search rejects oversized result sets with HTTP 400 above
roughly 33,000 rows in a single request.

## Ingest a specific range

```bash
python -m eq.cli range --start 2025-01-01 --end 2026-01-01 \
  --min-magnitude 3.0 --out data/raw/2025.parquet
```

## Build and test the models

```bash
cd dbt
dbt build --profiles-dir .
```

`dbt` here is the plain console script installed by `dbt-core`, not
`python -m dbt`: dbt-core 1.12 ships no `__main__.py`, so the module form does
not work.

**Windows note.** `pip install -e ".[dev]"` (or a user-level `pip install`)
puts `dbt.exe` in a Python `Scripts` directory that is often not on `PATH`
(for example `%APPDATA%\Python\Python312\Scripts` for a user install). If
`dbt --version` reports "command not found", either invoke it by full path,
e.g. `"$env:APPDATA\Python\Python312\Scripts\dbt.exe" --version`, or add that
Scripts directory to `PATH` for the session.

As of this writing, this builds 2 models and runs 22 data tests, 24 nodes
total, all passing, against a catalogue of 61,191 events at magnitude 3.0 and
above, spanning 2005-01-01 to 2026-08-03.

## The public page

`site/index.html` is served by GitHub Pages through `.github/workflows/pages.yml`.

Pages' "deploy from a branch" setting only offers the repository root or `/docs`
as a source folder, and neither suits: the root would publish the whole
repository, and `/docs` already holds this runbook and the design specs. So the
site is uploaded as an artifact from a workflow instead, which can publish any
folder.

**One-time setup:** Settings > Pages > Source > **GitHub Actions**. Not "deploy
from a branch".

The page reads the JSON the forecast run already commits, so it needs no
rebuild step. It deploys when the **forecast workflow finishes**, not when that
workflow pushes: GitHub does not start a workflow from a push made with the
default `GITHUB_TOKEN`, so keying off the push left the page frozen on
2026-08-08 while the record advanced for four more days.

This is the only workflow whose failure is purely cosmetic: it never writes to
the repository, so it cannot cost a window or damage the record.

## Audit the published record

```bash
python scripts/audit_history.py            # offline
python scripts/audit_history.py --online   # also verifies the Actions runs
python scripts/audit_history.py --json     # machine readable
```

Exit codes are `0` clean, `1` violations found, and `2` could not audit (shallow
clone, or `--online` without `gh`). The last is deliberately distinct: "audited
and found problems" and "never actually audited" are different answers, and a
caller that conflates them draws the wrong conclusion from either.

It runs from `.github/workflows/audit.yml` after every forecast run, plus a
daily schedule as a backstop, and again in CI on human pushes.

**That used to be wrong, and the wrongness is worth recording.** This said it
ran "on every push to `main`, which includes the scheduler's daily publish
commit". It did not. GitHub deliberately does not start a workflow from a push
made with the default `GITHUB_TOKEN`, which is what the forecast job pushes
with, so between 2026-08-08 and 2026-08-12 the record grew every day and
nothing audited it. A sentence claiming a check runs is worse than no check,
because it stops anyone looking.

This is the check a reader runs when they do not trust the author, so a few
properties are deliberate:

- It imports nothing from `eq`. An auditor built on the module it audits
  inherits that module's bugs and then confirms them.
- It reads git history, not just the current checkout. A forecast that was
  altered and altered back would leave `HEAD` looking honest.
- It **refuses** to run against a shallow clone rather than reporting clean.
  `actions/checkout` fetches depth 1 by default, which would leave every check
  examining one commit and printing "Clean", and a green audit gets read as
  proof. This is why `ci.yml` sets `fetch-depth: 0`.

Human-authored commits under `forecasts/` are reported as **notices**, not
violations. D10 does not forbid a human touching the record, it requires that
such a touch be visible; failing the audit would create an incentive to hide
it. There is currently one, on a test evaluation catalogue.

Every check is mutation-tested in `tests/test_history_audit.py`: neutering any
one check must break at least one test, so a check that has quietly stopped
working cannot keep passing.

## Always read origin_date, never re-derive it

`origintime` is stored as `TIMESTAMP WITH TIME ZONE`. Casting it to a date
directly resolves through DuckDB's *session* timezone, not necessarily UTC.

This project pins that session to UTC in `dbt/profiles.yml`. But that pin
lives in the profile, not in the database file. Anyone who opens
`data/eq.duckdb` with a different client, a notebook, the `duckdb` CLI, or a
BI tool, gets the machine's local timezone instead, silently, with no error.

This is not a theoretical risk. Measured on the real catalogue, 31,963 of
61,191 events, 52.2 percent, land on a different calendar date between UTC
and Pacific/Auckland. That is not a midnight edge case: Auckland is UTC+12 or
UTC+13, so local midnight falls in the early afternoon UTC, and a large slice
of events shift date under a naive cast.

The materialised `origin_date` column in `fct_events` is safe, because it was
computed once under the pinned UTC session, at build time, and is stored as a
plain date from then on.

**Therefore:** always read `origin_date` from `fct_events`. Never re-derive a
date from `origintime` outside dbt. If you must do it ad hoc, for example in a
one-off notebook query, write the cast explicitly:

```sql
cast(origintime at time zone 'UTC' as date)
```

Do not write `cast(origintime as date)` and trust the session default.

## A note on the staging view

`stg_quakes` is a `view` defined over a relative parquet glob,
`'../data/snapshots/catalogue-*.parquet'`. That path only resolves when the
working directory is `dbt/`, which is where `dbt build` is normally run from.
Querying `stg_quakes` from the repository root, or from any other working
directory, raises a DuckDB `IOException` about no files matching the pattern.

`fct_events` is materialised as a `table`, not a view, so it has no such
dependency and is queryable from anywhere once built.

If you need to query the models ad hoc, prefer `fct_events`, or `cd dbt`
first if you specifically need `stg_quakes`.

## When a dbt test fails

**Do not adjust the test bounds to make it pass.** Every one of these tests
encodes a fact measured on 2026-08-04 and recorded in `DECISIONS.md`. A
failure means one of two things: either the pipeline broke, or GeoNet changed
practice. Both require understanding before anything is edited. Loosening a
bound to make a red test green destroys the thing the test exists to catch,
and the next person to read it will trust a number that is no longer true.

If a test fails:

1. Read the comment at the top of the failing test file. It names the fact
   being protected and the `DECISIONS.md` section it comes from.
2. Reproduce the failure and inspect the offending rows directly, not just
   the aggregate the test reports.
3. Decide which of the two explanations holds: pipeline defect, or a genuine
   shift in the upstream catalogue.
4. Only once that is understood, and written down, consider whether the
   bound itself needs revisiting, as a deliberate, documented decision, not
   as a quick fix to get CI green again.

The most likely genuine failure is `assert_depthtype_share`. About 42 percent
of events above M3.5 carry an operator-assigned depth: measured precisely at
0.424 over the M3.5 and above population on the current catalogue, matching
the 0.42 recorded in `DECISIONS.md` section D4, which is measured over the
same M3.5 and above population for 2005 to 2026. If that share moves outside
20 to 65 percent, the depth data feeding stratum assignment has changed
character, which affects Phase 2 onwards.

The freshness check, `assert_catalogue_freshness`, is almost as important: it
fails if the newest event in the catalogue is more than three days old, which
usually means ingest has silently stopped running rather than that
earthquakes have stopped happening.

## Regenerating any figure quoted in the documentation

```bash
python scripts/measurements/<script>.py
```

See `scripts/measurements/README.md` for what each one establishes.
