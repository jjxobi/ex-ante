"""Expands a separable forecast into a dense one, reproducibly.

A separable forecast stores a rate per cell plus the magnitude distribution as
fitted Gutenberg-Richter parameters, because a dense forecast at full resolution
would put gigabytes a year into git and break the clone-and-reproduce claim that
committing to git exists to serve. This module performs the expansion.

Its output has to be byte identical across runs, machines and environments,
because a published score is verified by re-running it. That is a stronger
requirement than "correct", and D13.5 pins the five things it needs:

1. Iteration order is explicit: cells sorted ascending, then bins in order.
   Never dictionary order, never set order.
2. dtype is pinned to little-endian float64 explicitly, never inherited.
3. Reduction order is fixed. Floating point addition is not associative.
4. The canonical form hashes array bytes, not a file. Parquet embeds writer
   version and compression metadata, so a pyarrow upgrade would break a
   file-level hash while the numbers were untouched, discrediting the test
   rather than the data.
5. Dependencies are lockfile pinned, because determinism is a claim about a
   dependency set as much as about this code.

Normalisation uses the closed-form truncated Gutenberg-Richter expression rather
than dividing by a computed sum. On non-empty cells the two are numerically
indistinguishable, both bottoming out at one unit in the last place. They differ
entirely on an empty cell: the closed form multiplied by a zero rate gives clean
zeros, while dividing weights by their own sum raises ZeroDivisionError. The
closed form removes the degenerate branch instead of guarding it.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np

# Frozen in DECISIONS.md D13.3.
MMIN = 3.0
MMAX = 8.5
BIN_WIDTH = 0.1

# Frozen in D13.5. Determinism is exact; only conservation is tolerant. The
# measured worst deviation across the parameter space is 1.110e-16, one unit in
# the last place, so this carries about four orders of magnitude of headroom.
CONSERVATION_RTOL = 1e-12

# Little-endian float64, stated rather than inherited from the platform.
CANONICAL_DTYPE = "<f8"
CANONICAL_INT_DTYPE = "<i8"


class GridHashMismatchError(RuntimeError):
    """Raised when a forecast does not match the frozen grid it claims."""


@dataclass(frozen=True)
class MagnitudeClassification:
    """Where a magnitude falls relative to the forecast range.

    Per D13.4 an event above the top bin is counted and reported, never dropped
    and never raised. pyCSEP's bin1d_vec returns -1 above the last edge and
    get_index_of raises ValueError on -1, so leaving this to pyCSEP would crash
    the scorer the first time New Zealand produced an M8.5.
    """

    in_range: bool
    reported: bool
    bin_index: int | None


@dataclass(frozen=True)
class DenseForecast:
    cell_ids: list[int]
    bins: list[tuple[float, float]]
    values: list[float] = field(repr=False)


def bin_edges(
    m_min: float = MMIN, m_max: float = MMAX, bin_width: float = BIN_WIDTH
) -> list[tuple[float, float]]:
    """Half-open magnitude bins, lower inclusive, matching D13.2 and pyCSEP."""
    count = round((m_max - m_min) / bin_width)
    if count < 1:
        raise ValueError(f"magnitude range {m_min} to {m_max} yields no bins")
    return [
        (m_min + i * bin_width, m_min + (i + 1) * bin_width) for i in range(count)
    ]


def truncated_gr_probabilities(
    b: float,
    m_min: float = MMIN,
    m_max: float = MMAX,
    bin_width: float = BIN_WIDTH,
) -> list[float]:
    """Probability mass per magnitude bin under a truncated Gutenberg-Richter law.

    Closed form, so it sums to one analytically and needs no normalising pass,
    and so a zero-rate cell cannot produce a division by zero.
    """
    if b <= 0:
        raise ValueError(f"b must be positive, got {b}")
    denominator = 10 ** (-b * m_min) - 10 ** (-b * m_max)
    return [
        (10 ** (-b * lo) - 10 ** (-b * hi)) / denominator
        for lo, hi in bin_edges(m_min, m_max, bin_width)
    ]


def classify_magnitude(
    magnitude: float,
    m_min: float = MMIN,
    m_max: float = MMAX,
    bin_width: float = BIN_WIDTH,
) -> MagnitudeClassification:
    """Place a magnitude in a bin, or report it as out of range.

    Bin assignment uses integer arithmetic on tenths of a magnitude unit, for
    the same reason cell assignment does in D13.2: the obvious floating point
    expression is exact on some inputs and off by one on others, and which it is
    depends on the origin.
    """
    if magnitude < m_min or magnitude >= m_max:
        return MagnitudeClassification(in_range=False, reported=True, bin_index=None)
    index = (round(magnitude * 10) - round(m_min * 10)) // round(bin_width * 10)
    return MagnitudeClassification(in_range=True, reported=True, bin_index=int(index))


def expand(
    separable: dict,
    *,
    expected_grid_hash: str | None = None,
    m_min: float = MMIN,
    m_max: float = MMAX,
    bin_width: float = BIN_WIDTH,
) -> DenseForecast:
    """Expand a separable forecast to a dense rate per cell and magnitude bin.

    A zero-rate cell is present in the output filled with zeros. It is never
    omitted. A correctly shaped result that quietly lost rows is the exact
    failure the Phase 1 chunking rewrite produced.
    """
    if expected_grid_hash is not None and separable["grid_hash"] != expected_grid_hash:
        raise GridHashMismatchError(
            f"forecast claims grid hash {separable['grid_hash']!r} but the frozen "
            f"grid hash is {expected_grid_hash!r}. Refusing to expand against a "
            f"grid this forecast was not built for."
        )

    # Explicit sort. The input may arrive in any order and must not influence
    # the output, per D13.5.
    cell_ids = sorted(separable["cell_ids"])
    rates = separable["rates"]
    probabilities = truncated_gr_probabilities(separable["b"], m_min, m_max, bin_width)
    bins = bin_edges(m_min, m_max, bin_width)

    values: list[float] = []
    for cell_id in cell_ids:
        rate = rates[cell_id]
        for probability in probabilities:
            values.append(rate * probability)

    return DenseForecast(cell_ids=cell_ids, bins=bins, values=values)


def canonical_bytes(dense: DenseForecast) -> bytes:
    """A canonical byte representation of the whole forecast, including structure.

    Covers cell identifiers and bin edges as well as the values, so a forecast
    whose numbers are unchanged but whose grid or binning moved does not hash
    identically. Every array is materialised at an explicit little-endian dtype
    and made contiguous, so the bytes do not depend on the platform's native
    byte order or on numpy's internal layout decisions.
    """
    ids = np.ascontiguousarray(dense.cell_ids, dtype=CANONICAL_INT_DTYPE)
    lows = np.ascontiguousarray([lo for lo, _ in dense.bins], dtype=CANONICAL_DTYPE)
    highs = np.ascontiguousarray([hi for _, hi in dense.bins], dtype=CANONICAL_DTYPE)
    vals = np.ascontiguousarray(dense.values, dtype=CANONICAL_DTYPE)
    return ids.tobytes() + lows.tobytes() + highs.tobytes() + vals.tobytes()
