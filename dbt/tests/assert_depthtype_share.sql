-- Measured on 2026-08-04: 42 percent of M3.5 and above events carry an
-- operator-assigned depth. This test does not assert that exact figure, it
-- asserts the share stays in a band wide enough to absorb normal variation and
-- narrow enough to catch GeoNet changing practice. If this fails, investigate
-- before adjusting the bounds: the depth data feeding stratum assignment has
-- changed character.
with share as (
    select
        count(*) filter (where depth_is_assigned) * 1.0 / nullif(count(*), 0) as assigned_share
    from {{ ref('stg_quakes') }}
    where magnitude >= 3.5
)
select assigned_share
from share
where assigned_share < 0.20 or assigned_share > 0.65
