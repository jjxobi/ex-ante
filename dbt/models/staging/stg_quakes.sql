-- Staging view over the NEWEST local snapshot only, which is what makes
-- withdrawal take effect.
--
-- This model previously unioned every snapshot file. That made withdrawal
-- structurally undetectable: an event present in any snapshot stayed in the
-- view forever, even after GeoNet removed it. Measured on the first real
-- snapshot diff, GeoNet withdraws roughly one event per day, and it does so to
-- reviewed events; see DECISIONS.md section D4b. A withdrawn event left in
-- place inflates the observed count exactly as a duplicate does, so at about
-- 365 a year it dwarfs the single duplicate D4a guards against.
--
-- Reading only the newest snapshot handles it with no extra machinery: an event
-- absent from the newest snapshot is absent from the view. Revision history
-- lives in the committed diffs, which is where it belongs. The warehouse holds
-- current truth.
--
-- Snapshots are local and ephemeral. Per the design spec they are deliberately
-- not committed to git, because a daily full snapshot would cost about 1 GB a
-- year and break the clone-and-reproduce requirement. What is committed is the
-- daily revision diff.
--
-- THE GLOB PATTERN IS LOad BEARING. It matches date-shaped names only. A
-- pattern of catalogue-*.parquet would also match catalogue-ci.parquet, and
-- since 'c' sorts after '2' the lexical maximum would select the small CI slice
-- over the real catalogue, silently reducing the warehouse to a thirty day
-- window. Verified: under the unrestricted pattern DuckDB's max(file) does
-- return the CI file.
--
-- read_parquet cannot take a scalar subquery ("Table function cannot contain
-- subqueries"), so the newest file is selected by reading the filename column
-- and filtering, rather than by parameterising the read.

with all_snapshots as (

    select
        *,
        filename
    from read_parquet(
        '../data/snapshots/catalogue-????-??-??.parquet',
        union_by_name = true,
        filename = true
    )

),

newest_snapshot as (

    select max(filename) as snapshot_file
    from all_snapshots

),

source as (

    select * exclude (filename)
    from all_snapshots
    where filename = (select snapshot_file from newest_snapshot)

),

ranked as (

    select
        publicid,
        origintime,
        modificationtime,
        longitude,
        latitude,
        magnitude,
        depth,
        magnitudetype,
        depthtype,
        evaluationstatus,
        evaluationmode,
        -- Retained as a safety net. snapshot_full_catalogue already
        -- deduplicates by publicid across chunk boundaries, so within a single
        -- snapshot this should be a no-op, and the unique test on publicid
        -- proves it. NULL placement is pinned explicitly regardless: a null
        -- modificationtime must never outrank a real revision. Left to
        -- DuckDB's unpinned default null order that would be environment
        -- dependent and silent when wrong, the same shape as the
        -- session-timezone defect pinned elsewhere.
        row_number() over (
            partition by publicid
            order by modificationtime desc nulls last
        ) as recency_rank
    from source

)

select
    publicid,
    origintime,
    modificationtime,
    longitude,
    latitude,
    magnitude,
    depth,
    magnitudetype,
    depthtype,
    evaluationstatus,
    evaluationmode,
    depthtype = 'operator assigned' as depth_is_assigned
from ranked
where recency_rank = 1
