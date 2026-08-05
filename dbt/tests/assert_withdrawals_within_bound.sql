-- A partial ingest and a genuine withdrawal are indistinguishable from outside.
--
-- Now that stg_quakes reads only the newest snapshot, an event missing from
-- that snapshot leaves the warehouse. That is correct for a real withdrawal and
-- catastrophic for a truncated pull: a chunk returning a plausible but
-- incomplete result would silently delete hundreds of real events.
--
-- Ingest already raises on any chunk that parses to zero records, so a wholly
-- failed chunk cannot reach here. This covers the case of a chunk that returns
-- something, just not everything.
--
-- Threshold: 20 per day. The measured rate is ONE per day, from the first real
-- snapshot diff on 2026-08-05. This is deliberately undercalibrated and is
-- recorded as such in DECISIONS.md D4b: it rests on a single day of
-- observation. Revisit it once the daily diffs have enough history to
-- characterise the distribution, which is one of the things those diffs are
-- for. Do NOT raise this bound to silence a failure; a spike is the signal.
--
-- Returns no rows when fewer than two snapshots exist, so a fresh checkout with
-- a single snapshot passes rather than erroring.

with all_snapshots as (

    select
        publicid,
        filename
    from read_parquet(
        '../data/snapshots/catalogue-????-??-??.parquet',
        union_by_name = true,
        filename = true
    )

),

snapshot_files as (

    select distinct filename
    from all_snapshots

),

newest as (

    select max(filename) as f
    from snapshot_files

),

previous as (

    select max(filename) as f
    from snapshot_files
    where filename < (select f from newest)

),

withdrawn as (

    select publicid
    from all_snapshots
    where filename = (select f from previous)

    except

    select publicid
    from all_snapshots
    where filename = (select f from newest)

)

select
    (select f from previous) as previous_snapshot,
    (select f from newest) as newest_snapshot,
    count(*) as withdrawn_count
from withdrawn
having count(*) > 20
