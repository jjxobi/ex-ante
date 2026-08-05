-- Duplicate detection, frozen in DECISIONS.md section D4a.
--
-- Two records are duplicates if they carry different publicids but agree
-- EXACTLY on origintime, latitude, longitude, magnitude and depth. A duplicate
-- is one solution recorded twice, so it agrees to full float precision in every
-- field; two distinct earthquakes essentially never do.
--
-- This is deliberately NOT a tolerance based rule. Measured across all 11,961
-- catalogue pairs within 300 seconds of each other, genuine doublets get closer
-- in time (0.019 s) and closer in space (0.000 km) than the known duplicate
-- does, so no threshold in any dimension separates the populations. Conjunctive
-- exact agreement does: it matches exactly one pair in 61,191 events, while the
-- nearest genuine pair agrees on only 3 of the 5 fields and differs by 53
-- seconds in origin time.
--
-- Matching on location alone would be wrong. Five genuine pairs agree exactly
-- on latitude, longitude and depth while differing in origin time by 50 to 220
-- seconds, because they share an operator-assigned hypocentre. It is the
-- conjunction with origin time that discriminates.
--
-- The exclusion below covers the single documented pair, 2018p914028 and
-- 2018p914029, recorded in D4a. It is deliberately narrow so that a genuinely
-- new duplicate still fails the build. It must never be widened, and never
-- converted into a count threshold, to silence a future failure.
--
-- Duplicates matter because they inflate the observed event count, and observed
-- event count is exactly what the N-test consumes in Phase 3.

select
    origintime,
    latitude,
    longitude,
    magnitude,
    depth,
    count(*) as duplicate_count,
    string_agg(publicid, ', ') as publicids
from {{ ref('stg_quakes') }}
where publicid not in ('2018p914028', '2018p914029')
group by origintime, latitude, longitude, magnitude, depth
having count(*) > 1
