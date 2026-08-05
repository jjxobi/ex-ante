{{ config(severity = 'warn') }}

-- Near duplicate reporting. WARNS, never fails.
--
-- The duplicate rule in D4a is calibrated on a single known duplicate. With
-- n=1 there is no way to know whether GeoNet could emit a near duplicate that
-- differs in the last decimal place, which the exact rule would miss.
--
-- This surfaces candidates so the sample can grow and the rule can eventually
-- be revisited with more than one data point. It is a warning rather than a
-- failure on purpose: these are pairs for human judgement, not defects, and a
-- test that goes red on ambiguous cases would erode the discipline that a red
-- data contract test means something.
--
-- The criterion is agreement on origintime plus location, differing in
-- magnitude or depth. That is one field short of the frozen exact rule.
-- Currently expected to return zero rows.

with candidates as (

    select
        origintime,
        latitude,
        longitude,
        count(*) as n,
        count(distinct magnitude) as distinct_magnitudes,
        count(distinct depth) as distinct_depths,
        string_agg(publicid, ', ') as publicids
    from {{ ref('stg_quakes') }}
    where publicid not in ('2018p914028', '2018p914029')
    group by origintime, latitude, longitude
    having count(*) > 1

)

select *
from candidates
