-- Staging view over every local snapshot file matching catalogue-*.parquet,
-- not just the newest. Snapshots are local and ephemeral: per the design
-- spec, they are deliberately not committed to git, because a daily full
-- snapshot would cost about 1 GB per year and break the clone-and-reproduce
-- requirement. What is committed is the daily revision diff, not the
-- snapshot.
-- Deduplicates on publicid, keeping the most recently modified record, because
-- GeoNet revises events and a snapshot union can therefore carry an id twice.
-- Withdrawal is not modelled in Phase 1: this view has no way to know an event
-- was removed upstream, so an event present in any local snapshot remains in
-- this view even if a later snapshot no longer contains it.

with source as (

    select *
    from read_parquet('../data/snapshots/catalogue-*.parquet', union_by_name = true)

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
        -- NULL placement is pinned explicitly: a null modificationtime must
        -- never outrank a real revision. Left to DuckDB's unpinned default
        -- null order, that would be environment dependent and silent when
        -- wrong, the same shape as the session-timezone defect this project
        -- pins elsewhere.
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
