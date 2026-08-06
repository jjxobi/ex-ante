"""A window with no observed events must not read as a passing score.

pyCSEP returns quantile 1.0 for the S, M and L tests when a window is empty,
because it computes log(forecast * (n_obs / n_fore)) with n_obs zero. The
negative infinities never reach a sum, so nothing crashes and all three come
back with the highest, most passing value available.

That is this project's recurring failure shape: an artifact that looks correct
and is quietly wrong. At roughly 1.6 in-region shallow events per day, 30
percent of daily windows have none, so nearly a third of a daily scoreboard
would show a perfect pass on no evidence.
"""

from __future__ import annotations

import datetime as dt
import pathlib

import pytest

from eq import baseline, expander, score, storage

FIXTURE = pathlib.Path(__file__).parent / "fixtures" / "catalogue-fit-window.parquet"
START = dt.datetime(2026, 7, 20, tzinfo=dt.timezone.utc)
END = START + dt.timedelta(days=7)


@pytest.fixture(scope="module")
def empty_window_result():
    catalogue = storage.read_parquet(FIXTURE)
    fitted = baseline.fit(catalogue, "shallow")
    dense = expander.expand(baseline.forecast(fitted, START, END))
    return score.score(dense, [], START, END)


def test_smL_are_marked_not_applicable_on_an_empty_window(empty_window_result):
    assert empty_window_result.s_test.applicable is False
    assert empty_window_result.m_test.applicable is False
    assert empty_window_result.l_test.applicable is False


def test_the_n_test_stays_applicable_on_an_empty_window(empty_window_result):
    """Observing zero against a non-zero expectation is real information.

    Unlike the other three, the N test does not condition on the observed
    events, so an empty window tells it something rather than nothing.
    """
    assert empty_window_result.n_test.applicable is True
    assert empty_window_result.n_events_used == 0


def test_the_misleading_quantile_is_still_there_underneath(empty_window_result):
    """Pin the exact behaviour being defended against.

    If pyCSEP ever stops returning 1.0 here this test fails, which is the
    signal to revisit whether the applicable flag is still needed rather than
    to quietly delete it.
    """
    assert empty_window_result.s_test.quantile == 1.0
    assert empty_window_result.m_test.quantile == 1.0
    assert empty_window_result.l_test.quantile == 1.0


def test_a_populated_window_marks_everything_applicable():
    catalogue = storage.read_parquet(FIXTURE)
    fitted = baseline.fit(catalogue, "shallow")
    dense = expander.expand(baseline.forecast(fitted, START, END))
    observed = [e for e in catalogue if START <= e["origintime"] < END]
    result = score.score(dense, observed, START, END)
    assert result.n_events_used > 0
    for test in (result.n_test, result.s_test, result.m_test, result.l_test):
        assert test.applicable is True


def test_the_n_test_can_never_be_marked_inapplicable():
    """A guard on the guard.

    Marking N inapplicable would discard the one test that still means
    something on an empty window, so the type refuses it outright.
    """
    with pytest.raises(ValueError, match="always applicable"):
        score.ConsistencyTestResult("N", 0.0, 1.0, applicable=False)


def test_applicable_defaults_to_true_so_an_omission_is_visible():
    """A result constructed without the flag must not silently suppress itself."""
    assert score.ConsistencyTestResult("S", 0.0, 0.5).applicable is True
