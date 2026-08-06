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

import dataclasses
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


# --------------------------------------------------------------------------
# The structural invariant
# --------------------------------------------------------------------------

def _consistency_test_fields(result) -> list[str]:
    """Every ConsistencyTestResult on a ScoreResult, found by inspection.

    Deliberately not a hardcoded list of n_test, s_test, m_test, l_test. The
    point is that a FIFTH test added later is covered without anyone
    remembering to extend this.
    """
    return [
        field.name
        for field in dataclasses.fields(result)
        if isinstance(getattr(result, field.name), score.ConsistencyTestResult)
    ]


def test_every_conditional_test_is_inapplicable_on_an_empty_window(empty_window_result):
    """The invariant, enforced structurally rather than per construction site.

    The `applicable` default of True protects against wrongly discarding good
    data, which is a real risk in the other direction. But it does nothing to
    stop the original bug recurring: a new test type whose author forgets the
    zero-event check ships as applicable True carrying a degenerate quantile,
    which is this same defect relocated.

    So the rule is asserted over whatever tests actually exist, not over the
    three that exist today. Any conditional test that is applicable on an empty
    window fails here, whether or not anyone remembered to think about it.
    """
    names = _consistency_test_fields(empty_window_result)
    assert len(names) >= 4, "expected at least the four consistency tests"
    assert empty_window_result.n_events_used == 0

    for field_name in names:
        test = getattr(empty_window_result, field_name)
        if test.name == "N":
            assert test.applicable is True, (
                "the N test is informative on an empty window and must stay applicable"
            )
        else:
            assert test.applicable is False, (
                f"{field_name} ({test.name}) reports applicable on a window with no "
                f"observed events. It conditions on those events, so it is undefined "
                f"here, not passing. Its quantile is {test.quantile}, and pyCSEP "
                f"returns 1.0 for exactly this case, which is the most passing value "
                f"available. See DECISIONS.md D7.1a."
            )


def test_every_test_is_applicable_when_events_exist():
    """The same invariant in the other direction.

    A test wrongly marked inapplicable would silently discard real evidence,
    which is the failure the True default guards against. Also checked
    structurally.
    """
    catalogue = storage.read_parquet(FIXTURE)
    fitted = baseline.fit(catalogue, "shallow")
    dense = expander.expand(baseline.forecast(fitted, START, END))
    observed = [e for e in catalogue if START <= e["origintime"] < END]
    result = score.score(dense, observed, START, END)

    assert result.n_events_used > 0
    for field_name in _consistency_test_fields(result):
        test = getattr(result, field_name)
        assert test.applicable is True, (
            f"{field_name} ({test.name}) is marked inapplicable on a window with "
            f"{result.n_events_used} observed events, discarding real evidence"
        )
