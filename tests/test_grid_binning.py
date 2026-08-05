"""Regression guard on the grid binning hazard measured for D13.2.

These tests need neither the expander nor pyCSEP, so unlike the rest of the
Phase 2 test set they run today. They protect the measurement itself: if a
platform or Python change altered floating point behaviour, the adversarial
coordinate set would shift and every test built on it would quietly weaken.

The measurement lives in scripts/measurements/grid_edge_hazard.py.
"""

import math

# The parameters the adversarial set is a function of. It is NOT a list of 39
# magic numbers; it is the disagreement set for this origin at this spacing.
# Change either and the set changes, which is why the parameters are asserted
# explicitly below rather than left implicit in a hardcoded fixture.
LON_MIN, LON_MAX = 163.6, 183.0
LAT_MIN, LAT_MAX = -49.2, -32.3
DH = 0.1

# The count committed alongside those parameters. Derived, never hand written.
EXPECTED_LON_DISAGREEMENTS = 39


def naive_bin(x, origin, dh=DH):
    """The obvious implementation, which D13.2 forbids."""
    return math.floor((x - origin) / dh)


def integer_bin(x, origin, dh=DH):
    """D13.2's rule: round once at the boundary, integer arithmetic after."""
    return (round(x * 10) - round(origin * 10)) // round(dh * 10)


def edges(lo, hi):
    return [round(lo + i * DH, 10) for i in range(round((hi - lo) / DH) + 1)]


def disagreements(lo, hi, origin):
    return [x for x in edges(lo, hi) if naive_bin(x, origin) != integer_bin(x, origin)]


def test_derivation_parameters_match_the_frozen_grid():
    """Assert the inputs, not just the output.

    The adversarial set is a function of the origin and the spacing. If either
    moved, a hardcoded coordinate fixture would keep passing while guarding
    coordinates that are no longer adversarial, which is silent weakening in a
    different costume. This fails at the parameters instead.

    When Phase 2 generates region/grid.parquet, this test also asserts its
    SHA-256, so the set is provably derived under the grid actually in use.
    """
    assert (LON_MIN, LON_MAX) == (163.6, 183.0), (
        "longitude range changed. The adversarial set must be re-measured with "
        "scripts/measurements/grid_edge_hazard.py and EXPECTED_LON_DISAGREEMENTS updated."
    )
    assert (LAT_MIN, LAT_MAX) == (-49.2, -32.3), "latitude range changed, re-measure"
    assert DH == 0.1, "cell size changed, and D1 freezes it at 0.1 degrees"


def test_longitude_adversarial_set_is_still_what_we_measured():
    bad = disagreements(LON_MIN, LON_MAX, LON_MIN)
    assert len(bad) == EXPECTED_LON_DISAGREEMENTS, (
        f"expected {EXPECTED_LON_DISAGREEMENTS} disagreeing longitude edges for "
        f"origin {LON_MIN} at spacing {DH}, found {len(bad)}. Either the "
        f"parameters moved or float behaviour changed; re-run "
        f"scripts/measurements/grid_edge_hazard.py before touching this number."
    )
    assert bad[:3] == [163.7, 164.2, 164.7]


def test_every_disagreement_is_off_by_exactly_one_cell():
    for x in disagreements(LON_MIN, LON_MAX, LON_MIN):
        assert naive_bin(x, LON_MIN) == integer_bin(x, LON_MIN) - 1


def test_disagreements_recur_on_a_half_degree_beat():
    bad = disagreements(LON_MIN, LON_MAX, LON_MIN)
    gaps = {round(b - a, 6) for a, b in zip(bad, bad[1:])}
    assert gaps == {0.5}, f"expected a uniform 0.5 degree beat, got gaps {sorted(gaps)}"


def test_latitude_axis_produces_no_disagreements():
    """The more instructive half.

    Identical arithmetic over a comparable range, zero disagreements. Whether
    the hazard bites depends on the origin and the coordinate, so it cannot be
    reasoned about case by case and has to be removed structurally.
    """
    assert disagreements(LAT_MIN, LAT_MAX, LAT_MIN) == []


def test_the_canonical_counterexample_is_not_actually_a_counterexample():
    """Guards against reintroducing the wrong example D13.2 retracts.

    (174.5 - 163.6) / 0.1 is often quoted as floating point binning going
    wrong. It does not: it evaluates to 109.00000000000006 and floors to 109,
    the correct answer. The hazard is real but it is not here.
    """
    value = (174.5 - 163.6) / 0.1
    assert math.floor(value) == 109
    assert integer_bin(174.5, LON_MIN) == 109


def test_integer_binning_is_exact_on_every_edge_in_the_region():
    """Integer binning must agree with the exact decimal answer everywhere."""
    for lo, hi, origin in ((LON_MIN, LON_MAX, LON_MIN), (LAT_MIN, LAT_MAX, LAT_MIN)):
        for i, x in enumerate(edges(lo, hi)):
            assert integer_bin(x, origin) == i
