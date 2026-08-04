-- Measured on 2026-08-04: 42 percent of M3.5 and above events carry an
-- operator-assigned depth. This test does not assert that exact figure, it
-- asserts the share stays in a band wide enough to absorb normal variation and
-- narrow enough to catch GeoNet changing practice. If this fails, investigate
-- before adjusting the bounds: the depth data feeding stratum assignment has
-- changed character.
--
-- This is a full-catalogue contract, deliberately inert on the small slices
-- CI builds. Measured population sizes and shares: all-time share 0.424 over
-- 61,191 events (2005 to 2026), versus 0.580 over the last 365 days. CI only
-- ingests a 30-day slice of about 94 M3.5 and above events; at that sample
-- size the 0.65 ceiling is roughly 1.4 standard deviations from a recent share
-- near 0.58, so the test would go red on noise alone roughly one run in ten.
-- The population-size guard below keeps the test silent below 2000 M3.5 and
-- above events so it only fires against a population large enough for the
-- bounds to mean something.
with share as (
    select
        count(*) filter (where depth_is_assigned) * 1.0 / nullif(count(*), 0) as assigned_share
    from {{ ref('stg_quakes') }}
    where magnitude >= 3.5
)
select assigned_share
from share
where (assigned_share < 0.20 or assigned_share > 0.65)
  and (select count(*) from {{ ref('stg_quakes') }} where magnitude >= 3.5) >= 2000
