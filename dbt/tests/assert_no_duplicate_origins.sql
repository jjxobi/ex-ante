-- Two distinct ids at the identical instant and location is a duplication
-- fault, not two earthquakes.
select origintime, latitude, longitude, count(*) as n
from {{ ref('stg_quakes') }}
group by origintime, latitude, longitude
having count(*) > 1
