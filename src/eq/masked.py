"""MaskedCount: a count that carries the region it was taken over.

The defect this module exists to prevent. A region-masked expectation (15.16
shallow events per week, the fitted baseline's forecast, which only ever
covers the 4,100 cells inside the frozen collection region) was nearly
compared against an unmasked observation (20.65, a raw count of every shallow
event in the same week regardless of whether it fell inside the collection
region). Both numbers were individually correct: one really is the expected
count over the region, the other really is the count of every shallow event
that occurred. The comparison was the mistake, and it reversed the sign of the
headline finding, turning a 32.7% OVER-prediction into an apparent
under-prediction.

That class of mistake survives review because nothing in it is individually
wrong. Documenting "always filter to region before comparing" does not stop
it from recurring, because the next person to write a comparison has no
reason to know the rule exists. So it is made structurally impossible instead:
a bare number cannot reach a comparison at all. Only a MaskedCount can, and
comparing two of them requires their mask_id to match.

mask_id is deliberately just a string, not a richer type. The one property
that matters is that two counts taken over the same region produce the same
mask_id and two counts taken over different regions (or one masked and one
not) produce different ones. The frozen grid's own SHA-256
(`eq.region.grid_hash()`) already has exactly that property for in-region
counts, per D1's hashing rule, so callers use it directly rather than this
module inventing a parallel identifier. UNMASKED is the sentinel for a count
that is known, explicitly, not to have been filtered to the collection
region; it is a human-readable string rather than a hash so it can never
collide with a real grid hash and so a mask mismatch error naming it is
self-explanatory.
"""

from __future__ import annotations

from dataclasses import dataclass

# Sentinel mask_id for a count that is deliberately, explicitly not filtered
# to the collection region: a raw sum over some wider event set. Never a hex
# digest, so it can never collide with a real grid hash and always reads as
# what it is when it shows up in an error message.
UNMASKED = "unmasked:not-filtered-to-region"


class MaskedCountError(TypeError):
    """Raised when a bare number is used where a MaskedCount is required.

    A subclass of TypeError, not ValueError: the defect is that the argument
    is the wrong kind of thing (a number with no provenance) rather than a
    MaskedCount with an unacceptable value.
    """


class MaskMismatchError(ValueError):
    """Raised when two MaskedCounts being compared were not taken over the
    same region.

    This is the exact failure mode this module exists to catch: two
    individually correct counts, taken over different regions, compared as
    if they meant the same thing.
    """


@dataclass(frozen=True)
class MaskedCount:
    """A count tagged with the id of the region it was computed over.

    value is the count itself (a float count of events, or an int, but never
    another MaskedCount: wrapping a wrapped value is a sign the mask was
    already lost or never known at the point of construction). mask_id
    records which region it covers; two counts are comparable only if their
    mask_id is identical. Use `eq.masked.UNMASKED` for a count that is
    deliberately not region filtered, so that fact is visible in the type
    rather than implicit in how the number happened to be computed.
    """

    value: float
    mask_id: str

    def __post_init__(self) -> None:
        if isinstance(self.value, MaskedCount):
            raise MaskedCountError(
                "MaskedCount.value must be a plain number, not another "
                "MaskedCount: wrapping a wrapped value means the original "
                "mask has already been discarded or was never recorded."
            )
        if isinstance(self.value, bool) or not isinstance(self.value, (int, float)):
            raise MaskedCountError(
                f"MaskedCount.value must be an int or float, got "
                f"{type(self.value).__name__}"
            )
        if not isinstance(self.mask_id, str) or not self.mask_id:
            raise ValueError(
                "MaskedCount.mask_id must be a non-empty string naming the "
                "region this count was taken over. Use eq.masked.UNMASKED "
                "for a count that is deliberately not region filtered."
            )


@dataclass(frozen=True)
class Comparison:
    """The result of comparing two MaskedCounts taken over the same region."""

    mask_id: str
    expected: float
    observed: float
    difference: float  # observed - expected
    ratio: float  # observed / expected
    pct_over_prediction: float  # 100 * (expected - observed) / observed


def _require_masked(name: str, value: object) -> MaskedCount:
    if not isinstance(value, MaskedCount):
        raise MaskedCountError(
            f"{name} must be a MaskedCount carrying the region mask it was "
            f"taken over; got a bare {type(value).__name__} ({value!r}). "
            f"An unmasked count must not be able to reach a comparison "
            f"against an expected count: that is exactly the mistake this "
            f"type exists to make impossible. Wrap it explicitly, using "
            f"eq.masked.UNMASKED if it genuinely was not filtered to a "
            f"region, so the mismatch below fires instead of a silently "
            f"wrong comparison."
        )
    return value


def compare_counts(expected: MaskedCount, observed: MaskedCount) -> Comparison:
    """Compare an expected count to an observed count, over the same region.

    Raises MaskedCountError if either argument is not a MaskedCount, and
    MaskMismatchError if the two were taken over different regions (including
    the case where one is UNMASKED and the other is not). Only if both checks
    pass does this return the plain-number comparison.
    """
    expected = _require_masked("expected", expected)
    observed = _require_masked("observed", observed)
    if expected.mask_id != observed.mask_id:
        raise MaskMismatchError(
            f"cannot compare counts taken over different regions: expected "
            f"was masked to {expected.mask_id!r}, observed was masked to "
            f"{observed.mask_id!r}. Comparing them would silently mix a "
            f"count restricted to one region with a count over a different "
            f"one (or an unfiltered one), which is the defect this type "
            f"exists to prevent: individually correct numbers, wrongly "
            f"compared."
        )
    diff = observed.value - expected.value
    ratio = (observed.value / expected.value) if expected.value else float("nan")
    pct_over = (
        100.0 * (expected.value - observed.value) / observed.value
        if observed.value
        else float("nan")
    )
    return Comparison(
        mask_id=expected.mask_id,
        expected=float(expected.value),
        observed=float(observed.value),
        difference=float(diff),
        ratio=float(ratio),
        pct_over_prediction=float(pct_over),
    )
