"""The timezone guard must be able to fail, and must observe rather than assume."""

import duckdb
import pytest

from eq import warehouse


def test_connect_pins_timezone_to_utc(tmp_path):
    con = warehouse.connect(tmp_path / "t.duckdb")
    assert con.sql("select current_setting('TimeZone')").fetchone()[0] == "UTC"
    con.close()


def test_assert_utc_raises_when_the_session_is_not_utc(tmp_path):
    """The guard has to fail on a bad session, or it is decoration.

    This opens a connection deliberately set to a non-UTC zone and confirms the
    assertion fires, rather than trusting that it would.
    """
    con = duckdb.connect(str(tmp_path / "t.duckdb"))
    con.execute("SET TimeZone = 'Pacific/Auckland'")
    with pytest.raises(warehouse.TimezoneNotPinnedError) as excinfo:
        warehouse.assert_utc(con)
    assert "Pacific/Auckland" in str(excinfo.value)
    con.close()


def test_the_guard_observes_rather_than_assumes(tmp_path, monkeypatch):
    """If SET silently did nothing, the guard must still catch it.

    A guard that sets a value and then trusts it cannot detect the case where
    the set was ignored. This simulates exactly that: the SET is swallowed, the
    session stays on a non-UTC zone, and connect must still refuse.
    """
    real_connect = duckdb.connect

    class SwallowsSet:
        """A connection whose SET does nothing, which is the case being tested."""

        def __init__(self, inner):
            self._inner = inner

        def execute(self, *args, **kwargs):
            return None  # the pin is silently ignored

        def __getattr__(self, name):
            return getattr(self._inner, name)

    def connect_without_honouring_set(*args, **kwargs):
        con = real_connect(*args, **kwargs)
        con.execute("SET TimeZone = 'Pacific/Auckland'")
        return SwallowsSet(con)

    monkeypatch.setattr(warehouse.duckdb, "connect", connect_without_honouring_set)
    with pytest.raises(warehouse.TimezoneNotPinnedError):
        warehouse.connect(tmp_path / "t.duckdb")


def test_date_cast_is_stable_once_pinned(tmp_path):
    """The behaviour the pin exists to protect.

    2026-01-01T23:30:00Z is 2026-01-02 in Auckland local time and 2026-01-01 in
    UTC. Under a pinned session the cast must give the UTC answer.
    """
    con = warehouse.connect(tmp_path / "t.duckdb")
    got = con.sql(
        "select cast(TIMESTAMPTZ '2026-01-01 23:30:00+00' as date)"
    ).fetchone()[0]
    assert got.isoformat() == "2026-01-01"
    con.close()
