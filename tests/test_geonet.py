from datetime import date

import pytest

from eq import geonet


class FakeResponse:
    def __init__(self, text: str):
        self.text = text
        self.status_code = 200

    def raise_for_status(self) -> None:
        return None


class FlakySession:
    """Fails a fixed number of times, then succeeds."""

    def __init__(self, failures: int, payload: str = "publicid\nabc\n"):
        self.failures = failures
        self.payload = payload
        self.calls = 0

    def get(self, url, timeout=None):
        self.calls += 1
        if self.calls <= self.failures:
            raise ConnectionError("connection reset by peer")
        return FakeResponse(self.payload)


def test_build_url_contains_bbox_and_dates():
    url = geonet.build_url(3.0, date(2025, 1, 1), date(2026, 1, 1))
    assert geonet.BBOX in url
    assert "minmag=3.0" in url
    assert "startdate=2025-01-01T00:00:00" in url
    assert "enddate=2026-01-01T00:00:00" in url


def test_fetch_csv_returns_body_on_first_success():
    session = FlakySession(failures=0)
    body = geonet.fetch_csv("http://example.test", session=session, sleep=lambda _: None)
    assert body == "publicid\nabc\n"
    assert session.calls == 1


def test_fetch_csv_retries_then_succeeds():
    session = FlakySession(failures=3)
    body = geonet.fetch_csv("http://example.test", session=session, sleep=lambda _: None)
    assert body == "publicid\nabc\n"
    assert session.calls == 4


def test_fetch_csv_backs_off_exponentially():
    waits = []
    session = FlakySession(failures=3)
    geonet.fetch_csv("http://example.test", session=session, sleep=waits.append)
    assert waits == [5, 10, 20]


def test_fetch_csv_raises_after_exhausting_attempts():
    waits = []
    session = FlakySession(failures=99)
    with pytest.raises(geonet.GeoNetError) as excinfo:
        geonet.fetch_csv(
            "http://example.test", session=session, max_attempts=3, sleep=waits.append
        )
    assert "3 attempts" in str(excinfo.value)
    assert session.calls == 3
    assert waits == [5, 10]
    assert "connection reset by peer" in str(excinfo.value)
