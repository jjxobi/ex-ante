-- Longitude must be on the continuous [163.6, 183.0] convention. A negative
-- value means the antimeridian unwrap was skipped somewhere.
select publicid, longitude
from {{ ref('stg_quakes') }}
where longitude < 163.0 or longitude > 184.0
