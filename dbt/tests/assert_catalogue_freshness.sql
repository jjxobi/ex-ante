-- The catalogue must contain a recent event. New Zealand produces several M3.0
-- events per day, so a gap of more than three days means ingest has stalled.
-- An empty relation must also fail: max() over zero rows is NULL, and a bare
-- HAVING on that NULL is vacuously true, so total data loss would otherwise
-- return green from the test this project relies on as its break proof.
select max(origintime) as newest
from {{ ref('stg_quakes') }}
having count(*) = 0 or max(origintime) < now() - interval 3 day
