"""Agreement between this project's integer binning and pyCSEP's own.

pyCSEP reaches lower-inclusive binning by epsilon nudging, computing
floor((p - a0 + p_tol + a0_tol) / (h - h_tol)). This project reaches it by
integer decidegrees, per D13.2. The two mechanisms can only disagree exactly on
a cell edge, so that is the only place worth testing. A version of this test
using cell interiors would pass while proving nothing.

These tests were written before pyCSEP could be installed here, against an API
read from source rather than exercised. Two of them were wrong when first run:
CartesianGrid2D.from_origins needs a numpy array, not a list of tuples. That is
worth recording, because it is the argument for installing the dependency
rather than reasoning about it, and it is the same lesson as every other defect
this project has found.
"""

import math

import numpy as np
import pytest

LON_MIN, LON_MAX = 163.6, 183.0
LAT_MIN, LAT_MAX = -49.2, -32.3
DH = 0.1


def naive_bin(x, origin, dh=DH):
    return math.floor((x - origin) / dh)


def integer_bin(x, origin, dh=DH):
    """D13.2's rule: round once at the boundary, integer arithmetic thereafter."""
    return (round(x * 10) - round(origin * 10)) // round(dh * 10)


def adversarial_longitudes():
    """The coordinates where naive float binning and integer binning disagree.

    Measured in scripts/measurements/grid_edge_hazard.py: 39 longitude edges,
    each off by exactly one cell, on a 0.5 degree beat from 163.7. Latitude
    produces none, which is why only longitude is swept here.
    """
    out = []
    for i in range(round((LON_MAX - LON_MIN) / DH) + 1):
        x = round(LON_MIN + i * DH, 10)
        if naive_bin(x, LON_MIN) != integer_bin(x, LON_MIN):
            out.append(x)
    return out


# --------------------------------------------------------------------------
# Agreement with pyCSEP itself
# --------------------------------------------------------------------------

csep_regions = pytest.importorskip(
    "csep.core.regions",
    reason="pyCSEP is not installed. Scoring runs on Linux CI; see D10 operational notes.",
)
expander = pytest.importorskip(
    "eq.expander",
    reason="Phase 2 has not built the expander yet. These tests define its contract.",
)


def test_integer_binning_agrees_with_pycsep_on_cell_edges():
    """The only place the two mechanisms can diverge.

    An interior-point version of this test passes while proving nothing, which
    is why the points here sit exactly on 0.1 degree boundaries.
    """
    edges = [round(LON_MIN + i * DH, 10) for i in range(40)]
    origins = np.array([[lon, LAT_MIN] for lon in edges])
    region = csep_regions.CartesianGrid2D.from_origins(origins, dh=DH)
    for lon in edges:
        ours = integer_bin(lon, LON_MIN)
        theirs = region.get_index_of(np.array([lon]), np.array([LAT_MIN + DH / 2]))[0]
        assert ours == theirs, f"disagreement at exactly {lon}"


def test_pycsep_agrees_on_the_39_adversarial_coordinates():
    """The set where a naive implementation would be wrong by one cell."""
    bad = adversarial_longitudes()
    origins = np.array(
        [[round(LON_MIN + i * DH, 10), LAT_MIN] for i in range(196)]
    )
    region = csep_regions.CartesianGrid2D.from_origins(origins, dh=DH)
    for lon in bad:
        ours = integer_bin(lon, LON_MIN)
        theirs = region.get_index_of(np.array([lon]), np.array([LAT_MIN + DH / 2]))[0]
        assert ours == theirs, (
            f"integer binning and pyCSEP disagree at {lon}, "
            f"ours={ours} theirs={theirs}"
        )


def test_pycsep_origin_is_the_lower_left_corner():
    """Confirmed from source, asserted here so a version bump cannot change it."""
    region = csep_regions.CartesianGrid2D.from_origins(
        np.array([[170.0, -41.0]]), dh=DH
    )
    origin = region.origins()[0]
    assert origin[0] == pytest.approx(170.0)
    assert origin[1] == pytest.approx(-41.0)
    midpoint = region.midpoints()[0]
    assert midpoint[0] == pytest.approx(170.0 + DH / 2)
    assert midpoint[1] == pytest.approx(-41.0 + DH / 2)


def test_event_above_mmax_is_counted_and_reported_not_dropped_or_raised():
    """D13.4. pyCSEP's bin1d_vec returns -1 above the last bin edge and
    get_index_of raises ValueError on -1, so an M8.5 or greater event would
    crash the scorer rather than merely fall outside the forecast.

    The frozen rule keeps the top bin closed and handles the tail explicitly,
    the same way out-of-region events are handled in D1.
    """
    result = expander.classify_magnitude(9.0)
    assert result.in_range is False
    assert result.reported is True
    assert result.bin_index is None


def test_pycsep_returns_minus_one_above_the_top_magnitude_bin():
    """D13.4's premise, demonstrated rather than inferred.

    D13.4 keeps the top magnitude bin closed and handles the tail explicitly,
    on the grounds that pyCSEP's bin1d_vec returns -1 above the last edge and
    get_index_of raises ValueError on -1, so an M8.5 or greater event would
    crash the scorer rather than merely fall outside the forecast.

    That was read out of the source before pyCSEP could be installed here.
    This runs it.
    """
    from csep.utils import calc

    bins = np.arange(expander.MMIN, expander.MMAX, expander.BIN_WIDTH)

    assert calc.bin1d_vec(np.array([3.0]), bins)[0] == 0
    assert calc.bin1d_vec(np.array([8.4]), bins)[0] == 54

    # The tail. Both land outside, which is exactly the crash risk.
    assert calc.bin1d_vec(np.array([expander.MMAX]), bins)[0] == -1
    assert calc.bin1d_vec(np.array([9.0]), bins)[0] == -1

    # And this project's own classifier handles them rather than crashing.
    for magnitude in (expander.MMAX, 9.0):
        result = expander.classify_magnitude(magnitude)
        assert result.in_range is False
        assert result.reported is True
        assert result.bin_index is None
