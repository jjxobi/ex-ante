-- Guards against someone later simplifying the origin_date cast in
-- fct_events.sql back to a plain cast(origintime as date), which would
-- silently reintroduce the session timezone dependency this fix removed.
select publicid, origintime, origin_date
from {{ ref('fct_events') }}
where origin_date <> cast(origintime at time zone 'UTC' as date)
