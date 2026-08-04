-- Daily forecast binning depends on the DuckDB session timezone being UTC.
-- origin_date is derived from a TIMESTAMPTZ cast, and that cast resolves
-- through the session timezone. A non UTC session (this machine defaults to
-- Pacific/Auckland) would silently shift the calendar date for roughly half
-- of the catalogue. This test fails loudly instead of letting that happen.
select current_setting('TimeZone') as session_timezone
where current_setting('TimeZone') <> 'UTC'
