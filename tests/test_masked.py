"""Tests for the mask guard: eq.masked.MaskedCount.

The defect this module exists to prevent, reproduced almost exactly. A
region-masked expectation of 15.16 events per week (the fitted baseline's
forecast, which only ever covers the 4,100 cells inside the frozen
collection region) was nearly compared against an unmasked observation of
20.65 (every shallow event in the same week, including events outside the
collection region). Both numbers were individually correct; the comparison
was the mistake, and it reversed the sign of the headline finding, turning a
32.7% OVER-prediction into an apparent under-prediction. See DECISIONS.md
D13.4a and eq.masked's module docstring.

The requirement this file exists to prove: that mistake must raise, not
silently return a wrong number, and a bare number must never be able to
reach a comparison at all.
"""

from __future__ import annotations

import pathlib
from datetime import datetime, timezone

import pytest

from eq import baseline, expander, region, score, storage
from eq.masked import (
    UNMASKED,
    Comparison,
    MaskedCount,
    MaskedCountError,
    MaskMismatchError,
    compare_counts,
)

# The real, frozen collection-region mask id: the grid's own committed hash,
# per D1's hashing rule. Any region-restricted count is masked to this.
REGION_MASK = baseline.FROZEN_GRID_HASH


# ==========================================================================
# Criterion 1: the exact near-miss must raise, not silently compare wrong
# ==========================================================================

def test_the_exact_near_miss_raises_instead_of_comparing():
    """Reproduces the incident directly: a region-masked expected count of
    15.16 events/week against an unmasked observed count of 20.65 that
    counts every shallow event including out-of-region ones. This must raise
    rather than return a (wrong-sign) comparison.
    """
    expected = MaskedCount(15.16, REGION_MASK)
    observed_unmasked = MaskedCount(20.65, UNMASKED)

    with pytest.raises(MaskMismatchError):
        compare_counts(expected, observed_unmasked)


def test_two_different_region_masks_also_raise():
    """Not just UNMASKED vs masked: two counts masked to two different,
    non-UNMASKED regions must raise too. A mask mismatch is about the ids
    disagreeing, not about one side carrying a specific sentinel.
    """
    expected = MaskedCount(15.16, "region-hash-aaaa")
    observed = MaskedCount(20.65, "region-hash-bbbb")
    with pytest.raises(MaskMismatchError):
        compare_counts(expected, observed)


def test_matching_masks_compare_without_raising_and_reproduce_32_7_percent():
    """The correct comparison: both counts taken over the same region. This
    is also the number DECISIONS.md D13.4a records (15.16 expected against
    an observed in-region mean of 11.42, a 32.7% over-prediction), used here
    as a numeric regression check on the comparison arithmetic itself.
    """
    expected = MaskedCount(15.16, REGION_MASK)
    observed = MaskedCount(11.42, REGION_MASK)
    result = compare_counts(expected, observed)
    assert isinstance(result, Comparison)
    assert result.mask_id == REGION_MASK
    assert result.pct_over_prediction == pytest.approx(32.7, abs=0.1)


# ==========================================================================
# Criterion 2: a bare number must not be accepted at all
# ==========================================================================

def test_bare_int_observed_is_rejected():
    expected = MaskedCount(15.16, REGION_MASK)
    with pytest.raises(MaskedCountError):
        compare_counts(expected, 20)


def test_bare_float_observed_is_rejected():
    expected = MaskedCount(15.16, REGION_MASK)
    with pytest.raises(MaskedCountError):
        compare_counts(expected, 20.65)


def test_bare_int_expected_is_rejected():
    observed = MaskedCount(20.65, REGION_MASK)
    with pytest.raises(MaskedCountError):
        compare_counts(20, observed)


def test_wrapping_a_maskedcount_inside_a_maskedcount_is_rejected():
    """A MaskedCount whose value is itself a MaskedCount is a sign the
    original mask was already discarded or never recorded; refused at
    construction rather than silently double-wrapped.
    """
    inner = MaskedCount(15.16, REGION_MASK)
    with pytest.raises(MaskedCountError):
        MaskedCount(inner, REGION_MASK)


def test_empty_mask_id_is_rejected():
    with pytest.raises(ValueError):
        MaskedCount(15.16, "")


def test_non_string_mask_id_is_rejected():
    with pytest.raises(ValueError):
        MaskedCount(15.16, 12345)


# ==========================================================================
# Criterion: UNMASKED is a real, honest sentinel, not just None
# ==========================================================================

def test_two_unmasked_counts_do_compare_since_they_share_the_sentinel():
    """UNMASKED is a legitimate mask id like any other: two counts that both
    honestly declare themselves unfiltered can be compared to each other. The
    guard is about mismatched masks, not about forbidding UNMASKED outright.
    """
    a = MaskedCount(20.65, UNMASKED)
    b = MaskedCount(18.0, UNMASKED)
    result = compare_counts(a, b)
    assert result.mask_id == UNMASKED


# ==========================================================================
# Integration: eq.score.ScoreResult carries masked counts, same mask always
# ==========================================================================

@pytest.fixture(scope="module")
def catalogue():
    fixture = pathlib.Path(__file__).parent / "fixtures" / "catalogue-fit-window.parquet"
    return storage.read_parquet(fixture)


@pytest.fixture(scope="module")
def fitted_shallow(catalogue):
    return baseline.fit(catalogue, "shallow")


def test_score_result_expected_and_observed_are_masked_counts(fitted_shallow, catalogue):
    window_start = datetime(2026, 7, 20, tzinfo=timezone.utc)
    window_end = datetime(2026, 7, 27, tzinfo=timezone.utc)
    separable = baseline.forecast(fitted_shallow, window_start, window_end)
    dense = expander.expand(separable, expected_grid_hash=baseline.FROZEN_GRID_HASH)
    result = score.score(dense, catalogue, window_start, window_end, stratum="shallow")

    assert isinstance(result.expected_count, MaskedCount)
    assert isinstance(result.observed_count, MaskedCount)
    assert result.expected_count.mask_id == result.observed_count.mask_id
    assert result.expected_count.mask_id == region.grid_hash()

    # And the comparison this module exists to make safe actually works:
    comparison = result.n_test_comparison()
    assert isinstance(comparison, Comparison)
    assert comparison.observed == pytest.approx(result.observed_count.value)
    assert comparison.expected == pytest.approx(result.expected_count.value)


def test_score_result_observed_count_matches_n_events_used(fitted_shallow, catalogue):
    window_start = datetime(2026, 7, 20, tzinfo=timezone.utc)
    window_end = datetime(2026, 7, 27, tzinfo=timezone.utc)
    separable = baseline.forecast(fitted_shallow, window_start, window_end)
    dense = expander.expand(separable, expected_grid_hash=baseline.FROZEN_GRID_HASH)
    result = score.score(dense, catalogue, window_start, window_end, stratum="shallow")
    assert result.observed_count.value == float(result.n_events_used)
