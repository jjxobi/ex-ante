-- The clean event table. Everything downstream reads this and nothing reads
-- the raw parquet directly.
--
-- Region membership and depth stratum are deliberately absent. They depend on
-- the frozen grid and the fitted depth boundary, which Phase 2 produces. Adding
-- them here would couple this model to decisions that do not exist yet.

select
    publicid,
    origintime,
    cast(origintime as date) as origin_date,
    modificationtime,
    longitude,
    latitude,
    magnitude,
    depth,
    magnitudetype,
    depthtype,
    depth_is_assigned,
    evaluationstatus,
    evaluationmode,
    case
        when magnitude >= 5.0 then 'M5+'
        when magnitude >= 4.0 then 'M4-5'
        when magnitude >= 3.0 then 'M3-4'
        else 'below M3'
    end as magnitude_band
from {{ ref('stg_quakes') }}
