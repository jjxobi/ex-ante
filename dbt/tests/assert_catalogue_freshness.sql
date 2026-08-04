-- The catalogue must contain a recent event. New Zealand produces several M3.0
-- events per day, so a gap of more than three days means ingest has stalled.
select max(origintime) as newest
from {{ ref('stg_quakes') }}
having max(origintime) < now() - interval 3 day
