-- New Zealand has never recorded anything near M10, and negative magnitudes
-- below -1 indicate a parsing fault rather than a real microearthquake.
select publicid, magnitude
from {{ ref('stg_quakes') }}
where magnitude < -1.0 or magnitude > 10.0
