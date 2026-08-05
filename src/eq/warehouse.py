"""DuckDB connections that assert their own invariants.

The Phase 1 timezone defect had 52 percent of the catalogue landing on a
different calendar date depending on which machine ran the query, because
casting a TIMESTAMPTZ resolves through the session timezone and nothing pinned
it. The fix pinned it in dbt/profiles.yml.

That mitigation has the same shape as the bug: behaviour that depends on
environment configuration, protecting against a bug caused by behaviour that
depends on environment configuration. The pin lives in the dbt profile, not in
the database file, so any connection that does not load that profile silently
gets the machine default and produces plausible, correctly typed, silently
shifted dates.

So this module does not trust configuration to carry the invariant. It sets the
timezone and then reads it back, failing loudly if it is not UTC. That holds
even if a future DuckDB version changes how settings persist, and it converts a
silent misconfiguration into a red failure at the point of use.
"""

from __future__ import annotations

from pathlib import Path

import duckdb

REQUIRED_TIMEZONE = "UTC"


class TimezoneNotPinnedError(RuntimeError):
    """Raised when a connection's effective timezone is not UTC."""


def assert_utc(connection) -> str:
    """Read back the effective timezone and fail if it is not UTC.

    Returns the timezone so a caller can log it. Reading back rather than
    assuming the SET took effect is the point: a guard that cannot observe the
    thing it guards is the failure mode this project keeps finding.
    """
    effective = connection.sql("select current_setting('TimeZone')").fetchone()[0]
    if effective != REQUIRED_TIMEZONE:
        raise TimezoneNotPinnedError(
            f"DuckDB session timezone is {effective!r}, expected "
            f"{REQUIRED_TIMEZONE!r}. Casting a TIMESTAMPTZ to a date resolves "
            f"through this setting, which shifts the calendar date for about "
            f"half the catalogue. Refusing to proceed."
        )
    return effective


def connect(database: str | Path, *, read_only: bool = False):
    """Open a DuckDB connection with the session timezone pinned and verified."""
    connection = duckdb.connect(str(database), read_only=read_only)
    try:
        connection.execute(f"SET TimeZone = '{REQUIRED_TIMEZONE}'")
    except duckdb.Error:
        # A read-only connection may refuse the SET. The assertion below still
        # decides whether the resulting session is safe to use.
        pass
    assert_utc(connection)
    return connection
