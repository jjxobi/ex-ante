-- Two distinct ids at the identical instant and location is a duplication
-- fault, not two earthquakes.
--
-- Documented exception: 2018p914028 and 2018p914029 are identical in every
-- scientific field (origintime, latitude, longitude, magnitude, depth,
-- magnitudetype) to nine decimal places. They differ only in publicid and in
-- modificationtime, fourteen minutes apart, and both are evaluationmode
-- "automatic" with an empty evaluationstatus, meaning neither was ever
-- reviewed. This is one earthquake ingested twice by GeoNet's automatic
-- system and never merged, confirmed as the only such pair across all
-- 61,191 events in the catalogue. See DECISIONS.md D4a.
--
-- The exclusion below filters out those two specific publicids before
-- grouping. It is deliberately narrow: it removes exactly this one known
-- pair and nothing else, so any NEW duplicate that appears anywhere else in
-- the catalogue still fails this test. It must never be widened, for
-- example into a count threshold, to silence a future failure.
select origintime, latitude, longitude, count(*) as n
from {{ ref('stg_quakes') }}
where publicid not in ('2018p914028', '2018p914029')
group by origintime, latitude, longitude
having count(*) > 1
