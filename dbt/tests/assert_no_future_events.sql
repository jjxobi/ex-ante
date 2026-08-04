-- An event dated in the future means a clock or parsing fault upstream.
select publicid, origintime
from {{ ref('stg_quakes') }}
where origintime > now()
