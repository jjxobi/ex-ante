"""Regression guard on the grid binning hazard measured for D13.2.

These tests need neither the expander nor pyCSEP, so unlike the rest of the
Phase 2 test set they run today. They protect the measurement itself: if a
platform or Python change altered floating point behaviour, the adversarial
coordinate set would shift and every test built on it would quietly weaken.

The measurement lives in scripts/measurements/grid_edge_hazard.py.
"""

import math

LON_MIN, LON_MAX = 163.6, 183.0
LAT_MIN, LAT_MAX = -49.2, -32.3
DH = 0.1


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


def test_longitude_adversarial_set_is_still_what_we_measured():
    bad = disagreements(LON_MIN, LON_MAX, LON_MIN)
    assert len(bad) == 39, f"expected 39 disagreeing longitude edges, found {len(bad)}"
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
