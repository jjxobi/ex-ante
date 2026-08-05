# Phase 1: what building it actually taught us

This is the record of things discovered while building the catalogue pipeline
that were not known when it was designed. Some are facts about the GeoNet
catalogue, some are defects caught before they shipped. They are written down
because in six months the interesting question will not be "what does the code
do", it will be "why is it like that".

Every figure here was measured against the live service or the real catalogue on
2026-08-04 and 2026-08-05.

---

## Facts about the GeoNet catalogue

### Quake Search rejects large result sets, it does not truncate them

The CSV export refuses oversized queries with HTTP 400 rather than silently
returning the first N rows. Measured boundaries:

| Range, M3.0 and above | Result |
|---|---|
| 2005 to 2010 | 18,462 rows |
| 2005 to 2012 | 29,151 rows |
| 2005 to 2014 | 33,206 rows |
| 2005 to 2018 | HTTP 400 |
| 2005 to 2026 | HTTP 400 |

This is fortunate. A truncating API would have produced a short catalogue that
looked complete. The full snapshot is therefore fetched in five year chunks,
deduplicated by event id across chunk boundaries, and the whole pull fails if any
single chunk comes back empty.

The full catalogue at M3.0 and above from 2005 to now is **61,191 events**.

### The catalogue contains one true duplicate

Events `2018p914028` and `2018p914029` are identical in every scientific field
to nine decimal places: same origin time, latitude, longitude, magnitude
3.086434784 and depth 111.875. They differ only in their public id and a
modification time fourteen minutes apart, and both are unreviewed automatic
solutions. It is one earthquake ingested twice and never merged.

It is the only such pair in 61,191 events, and no other origin time is shared
anywhere in the catalogue. Recorded as D4a in `DECISIONS.md`, because a duplicate
inflates the observed event count, and the observed count is exactly what the
N-test consumes in Phase 3.

### GeoNet's practice on assigned depths has drifted

About 42 percent of events at M3.5 and above carry an operator-assigned depth
across the whole catalogue, which matches the figure measured at design time and
recorded in D4. But the recent share is higher:

| Window | M3.5 and above events | Assigned-depth share |
|---|---|---|
| All time | 25,061 | 0.424 |
| Last 365 days | 921 | 0.580 |
| Last 30 days | 94 | 0.468 |

Worth knowing before Phase 2 leans on depth.

### Per-event revision history is not available

`api.geonet.org.nz/quake/history/{publicID}` returns HTTP 200 with zero features
for every event tried, recent and historical. The revision curve can therefore
only be built forward, by snapshotting daily and diffing. That is why the diff
machinery exists in Phase 1 even though nothing scores anything yet: if it does
not start now, the history is unrecoverable.

---

## Defects caught before they shipped

These are recorded because the interesting ones were invisible to code review and
only surfaced on contact with reality.

### origin_date was environment dependent

`origintime` is a `TIMESTAMP WITH TIME ZONE`, and DuckDB resolves a cast to date
through the **session** timezone, which was pinned nowhere. Measured on the real
catalogue, **31,963 of 61,191 events, 52.2 percent, land on a different calendar
date** depending on the machine. New Zealand is UTC+12 or +13, so local midnight
falls in the early afternoon UTC; this was never a midnight edge case.

For a project whose output is daily forecast windows, a UTC continuous
integration runner and a local machine would have binned events into different
days, silently. Now pinned in three places: an explicit `at time zone 'UTC'`
cast, `TimeZone: 'UTC'` in the dbt profile, and two guard tests.

The residue is documented in the runbook: the pin lives in the profile, not in
the database file, so anything opening `data/eq.duckdb` with another client gets
local time. Always read `origin_date`; never re-derive a date from `origintime`.

### The chunked snapshot could hide a missing chunk

When the full-catalogue pull was split into chunks, the emptiness check stayed on
the aggregate. A single chunk returning a valid but header-only response would
have dropped up to 30 percent of the catalogue while writing a correctly named,
correctly typed file indistinguishable from a good one. Now every chunk is
checked individually.

This one is worth remembering: the unchunked version got the guarantee for free
by delegating to a function that already had it, and the rewrite lost it. The
review that approved the rewrite was scoped to chunking correctness, so nobody
re-checked an invariant that used to be somebody else's job.

### A test that could not fail

The freshness test was to be proven by deliberately breaking it. The instruction
said to widen the interval from 3 days to 3000 and expect a failure. Widening it
makes the test **pass**, because `now()` minus a large interval is far in the
past. The break has to narrow the interval to zero.

Had the unexpected pass been accepted, the conclusion would have been "test
verified" when nothing was verified at all. Every data contract test in this
project should be proven by watching it fail once.

### Dedup direction was untestable with one snapshot

`stg_quakes` keeps the most recently modified row per event id. With only one
snapshot file every group has one row, so ascending and descending ordering give
identical results and the row count proves nothing. Verified by injecting a
synthetic second snapshot containing a revised copy of a real event and
confirming the newer revision won.

NULL placement in that ordering was also unpinned, which is the same class of
bug as the timezone defect: correct today, environment dependent, silent when
wrong. Now `nulls last`.

---

## Known gaps carried into Phase 2

- The continuous integration workflow has never executed. It cannot run until
  the repository has a remote. Until then it is an untested test.
- The freshness test depends on the live GeoNet service returning a recent
  event. This is deliberate, since the test exists to detect a stalled ingest,
  which a fixture cannot show. It does mean the build is not hermetic.
- Withdrawal is now modelled. It was not, at the end of Phase 1: every snapshot
  was unioned, so an event GeoNet withdrew never left `stg_quakes`. The first
  real snapshot diff measured the rate at roughly one per day, on reviewed
  events, so the staging model now reads only the newest snapshot. See D4b.
- Region membership and depth stratum are deliberately absent. They depend on
  the frozen collection region and the fitted depth boundary that Phase 2
  produces.
