-- Staging view over the newest committed catalogue snapshot.
-- Deduplicates on publicid, keeping the most recently modified record, because
-- GeoNet revises events and a snapshot union can therefore carry an id twice.

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
        row_number() over (
            partition by publicid
            order by modificationtime desc
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
