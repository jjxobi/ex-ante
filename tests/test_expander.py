"""Property tests for the forecast expander, written BEFORE the expander exists.

Why these come first, per DECISIONS.md D13.5: in Phase 1 the unchunked snapshot
inherited its emptiness guarantee for free by delegating to a function that had
it. The chunked rewrite inlined the loop and the guarantee vanished, because it
had been structural rather than asserted. Nobody re-checked it, because the
review was scoped to chunking.

The expander has exactly that shape. Determinism and round-tripping would
otherwise be properties of how the code happens to be written. So they are
written down first, and the implementation has to satisfy them.

THE CONTRACT THESE TESTS PIN. The expander does not exist yet, so these tests
fail at import. That is intended. This is the interface Phase 2 must provide, or
must consciously change here first:

    expander.MMIN, expander.MMAX, expander.BIN_WIDTH   frozen, from D13.3
    expander.CONSERVATION_RTOL                         frozen 1e-12, from D13.5
    expander.bin_edges() -> list[tuple[float, float]]
    expander.truncated_gr_probabilities(b) -> list[float]
    expander.expand(separable) -> DenseForecast
    expander.canonical_bytes(dense) -> bytes

    separable, a plain dict so it is trivially serialisable:
        {
          "grid_hash": str,
          "cell_ids": list[int],        sorted ascending, the frozen grid
          "b": float,                   one value per stratum, per D13.3
          "rates": dict[int, float],    cell id to expected count, zeros allowed
        }

    DenseForecast exposes:
        .cell_ids   list[int], sorted, exactly the frozen grid
        .bins       list[tuple[float, float]]
        .values     row-major, len == len(cell_ids) * len(bins)
"""

import math
import os
import subprocess
import sys

import pytest

expander = pytest.importorskip(
    "eq.expander",
    reason="Phase 2 has not built the expander yet. These tests define its contract.",
)

try:
    from hypothesis import given, settings
    from hypothesis import strategies as st

    HAVE_HYPOTHESIS = True
except ImportError:  # pragma: no cover
    HAVE_HYPOTHESIS = False


# --------------------------------------------------------------------------
# fixtures
# --------------------------------------------------------------------------

def make_separable(cell_ids=None, rates=None, b=1.0, grid_hash="test-hash"):
    cell_ids = sorted(cell_ids if cell_ids is not None else [1, 2, 3, 4])
    if rates is None:
        rates = {c: 0.1 * (i + 1) for i, c in enumerate(cell_ids)}
    return {
        "grid_hash": grid_hash,
        "cell_ids": cell_ids,
        "b": b,
        "rates": {c: rates.get(c, 0.0) for c in cell_ids},
    }


def independent_truncated_gr(b, m_min, m_max, width):
    """Deliberately reimplemented from the definition, not imported.

    A test that calls the same function it is checking proves nothing.
    """
    n = round((m_max - m_min) / width)
    denom = 10 ** (-b * m_min) - 10 ** (-b * m_max)
    out = []
    for i in range(n):
        lo = m_min + i * width
        hi = m_min + (i + 1) * width
        out.append((10 ** (-b * lo) - 10 ** (-b * hi)) / denom)
    return out


# ==========================================================================
# Group A: purity and determinism. Bit exact, no tolerance.
# ==========================================================================

def test_same_input_twice_in_one_process_is_byte_identical():
    sep = make_separable()
    a = expander.canonical_bytes(expander.expand(sep))
    b = expander.canonical_bytes(expander.expand(sep))
    assert a == b


def test_identical_across_processes_with_different_hash_seeds():
    """Catches dict and set iteration order, the classic hidden nondeterminism.

    PYTHONHASHSEED randomises string hashing, so anything relying on dict order
    of string keys produces a different answer per process.
    """
    script = (
        "import sys; sys.path.insert(0, 'src');"
        "from eq import expander;"
        "from tests.test_expander import make_separable;"
        "sys.stdout.write(expander.canonical_bytes("
        "expander.expand(make_separable())).hex())"
    )
    outs = []
    for seed in ("0", "1", "12345"):
        env = dict(os.environ, PYTHONHASHSEED=seed, PYTHONPATH="src" + os.pathsep + ".")
        r = subprocess.run(
            [sys.executable, "-c", script], capture_output=True, text=True, env=env
        )
        assert r.returncode == 0, r.stderr
        outs.append(r.stdout)
    assert len(set(outs)) == 1, "output varies with PYTHONHASHSEED"


@pytest.mark.parametrize(
    "env_overrides",
    [
        {"TZ": "UTC"},
        {"TZ": "Pacific/Auckland"},
        {"TZ": "America/New_York"},
        {"LC_ALL": "C"},
        {"LC_ALL": "de_DE.UTF-8"},
    ],
)
def test_output_invariant_under_locale_and_timezone(env_overrides):
    """This project has specific cause to test this one.

    A Phase 1 defect had 52 percent of the catalogue landing on a different
    calendar date depending on the session timezone. Nothing in the expander
    should be able to notice the environment at all.
    """
    script = (
        "import sys; sys.path.insert(0, 'src');"
        "from eq import expander;"
        "from tests.test_expander import make_separable;"
        "sys.stdout.write(expander.canonical_bytes("
        "expander.expand(make_separable())).hex())"
    )
    base_env = dict(os.environ, PYTHONPATH="src" + os.pathsep + ".")
    ref = subprocess.run(
        [sys.executable, "-c", script], capture_output=True, text=True, env=base_env
    )
    assert ref.returncode == 0, ref.stderr
    env = dict(base_env, **env_overrides)
    got = subprocess.run(
        [sys.executable, "-c", script], capture_output=True, text=True, env=env
    )
    assert got.returncode == 0, got.stderr
    assert got.stdout == ref.stdout


def test_shuffled_input_collections_give_identical_output():
    """Forces explicit sorting rather than reliance on arrival order."""
    import random

    cells = [10, 20, 30, 40, 50]
    rates = {c: 0.01 * c for c in cells}
    reference = expander.canonical_bytes(expander.expand(make_separable(cells, rates)))

    for seed in range(5):
        shuffled = cells[:]
        random.Random(seed).shuffle(shuffled)
        sep = {
            "grid_hash": "test-hash",
            "cell_ids": shuffled,
            "b": 1.0,
            "rates": {c: rates[c] for c in shuffled},
        }
        assert expander.canonical_bytes(expander.expand(sep)) == reference


# ==========================================================================
# Group B: conservation. Tolerant, per D13.5.
# ==========================================================================

def test_each_cell_sums_to_its_separable_rate():
    sep = make_separable()
    dense = expander.expand(sep)
    n_bins = len(dense.bins)
    for i, cell in enumerate(dense.cell_ids):
        got = sum(dense.values[i * n_bins:(i + 1) * n_bins])
        want = sep["rates"][cell]
        assert got == pytest.approx(want, rel=expander.CONSERVATION_RTOL, abs=1e-300)


def test_global_total_is_conserved():
    sep = make_separable()
    dense = expander.expand(sep)
    assert sum(dense.values) == pytest.approx(
        sum(sep["rates"].values()), rel=expander.CONSERVATION_RTOL
    )


def test_no_nan_no_inf_no_negative_values():
    sep = make_separable(rates={1: 0.0, 2: 1e-300, 3: 5.0, 4: 0.0})
    dense = expander.expand(sep)
    for v in dense.values:
        assert not math.isnan(v), "NaN in expanded forecast"
        assert math.isfinite(v), "infinity in expanded forecast"
        assert v >= 0.0, "negative rate in expanded forecast"


# ==========================================================================
# Group C: structural invariants.
#
# This group exists because of Phase 1, and it is the one most likely to be
# skipped. A correctly shaped output that quietly lost rows is the exact
# failure the chunking rewrite produced.
# ==========================================================================

def test_output_length_is_exactly_cells_times_bins():
    sep = make_separable()
    dense = expander.expand(sep)
    assert len(dense.values) == len(dense.cell_ids) * len(dense.bins)


def test_zero_rate_cells_still_appear_with_zeros():
    """A zero-rate cell must not vanish. D13.5 is explicit about this."""
    sep = make_separable(rates={1: 0.0, 2: 0.0, 3: 1.0, 4: 0.0})
    dense = expander.expand(sep)
    assert dense.cell_ids == sep["cell_ids"]
    assert len(dense.values) == len(sep["cell_ids"]) * len(dense.bins)
    n_bins = len(dense.bins)
    zero_block = dense.values[0:n_bins]
    assert all(v == 0.0 for v in zero_block)


def test_cell_id_set_equals_the_frozen_grid_exactly():
    """Not a subset, not a superset."""
    sep = make_separable(cell_ids=[7, 3, 11, 2])
    dense = expander.expand(sep)
    assert set(dense.cell_ids) == set(sep["cell_ids"])
    assert dense.cell_ids == sorted(sep["cell_ids"])


def test_magnitude_bin_edges_match_the_frozen_values():
    dense = expander.expand(make_separable())
    assert dense.bins[0][0] == expander.MMIN
    assert dense.bins[-1][1] == pytest.approx(expander.MMAX)
    assert len(dense.bins) == round((expander.MMAX - expander.MMIN) / expander.BIN_WIDTH)
    for lo, hi in dense.bins:
        assert hi - lo == pytest.approx(expander.BIN_WIDTH)


def test_grid_hash_is_asserted_before_expansion():
    """The existing frozen rule: every component checks the grid hash first."""
    sep = make_separable(grid_hash="not-the-frozen-hash")
    with pytest.raises(Exception) as excinfo:
        expander.expand(sep, expected_grid_hash="the-frozen-hash")
    assert "hash" in str(excinfo.value).lower()


@pytest.mark.parametrize(
    "sep",
    [
        pytest.param(make_separable(rates={1: 0.0, 2: 0.0, 3: 0.0, 4: 0.0}), id="all-zero"),
        pytest.param(make_separable(cell_ids=[42], rates={42: 1.0}), id="single-cell"),
    ],
)
def test_degenerate_inputs_produce_valid_output_not_exceptions(sep):
    dense = expander.expand(sep)
    assert len(dense.values) == len(dense.cell_ids) * len(dense.bins)
    assert all(math.isfinite(v) and v >= 0.0 for v in dense.values)


def test_single_magnitude_bin_is_valid():
    dense = expander.expand(
        make_separable(), m_min=3.0, m_max=3.1, bin_width=0.1
    )
    assert len(dense.bins) == 1
    n = len(dense.cell_ids)
    assert len(dense.values) == n * 1


# ==========================================================================
# Group D: magnitude distribution correctness.
#
# Tests the maths independently of the plumbing, so a failure says which one
# broke rather than leaving you to guess.
# ==========================================================================

@pytest.mark.parametrize("b", [0.7, 0.9, 1.0, 1.1, 1.3])
def test_bin_probabilities_match_independent_closed_form(b):
    got = expander.truncated_gr_probabilities(b)
    want = independent_truncated_gr(b, expander.MMIN, expander.MMAX, expander.BIN_WIDTH)
    assert len(got) == len(want)
    for g, w in zip(got, want):
        assert g == pytest.approx(w, rel=1e-15)


@pytest.mark.parametrize("b", [0.7, 1.0, 1.3])
def test_bin_probabilities_decrease_monotonically(b):
    probs = expander.truncated_gr_probabilities(b)
    for earlier, later in zip(probs, probs[1:]):
        assert later < earlier, "magnitude bin probabilities must decrease for positive b"


@pytest.mark.parametrize("b", [0.6, 0.8, 1.0, 1.2, 1.4])
def test_bin_probabilities_sum_to_one(b):
    total = sum(expander.truncated_gr_probabilities(b))
    assert total == pytest.approx(1.0, rel=expander.CONSERVATION_RTOL)


def test_stratum_b_values_are_not_swapped():
    """Catches a silent stratum mix-up that every other test would pass.

    Shallow and deep carry different b values per D13.3. If the two were
    transposed, totals, shapes, conservation and determinism would all still
    hold. Only the magnitude distribution would be wrong, and only this test
    looks at it per stratum.
    """
    shallow = expander.expand(make_separable(b=0.9))
    deep = expander.expand(make_separable(b=1.2))
    assert shallow.values != deep.values, "different b must produce different output"

    want_shallow = independent_truncated_gr(
        0.9, expander.MMIN, expander.MMAX, expander.BIN_WIDTH
    )
    n_bins = len(shallow.bins)
    rate = make_separable()["rates"][shallow.cell_ids[0]]
    for j in range(n_bins):
        assert shallow.values[j] == pytest.approx(rate * want_shallow[j], rel=1e-12)


# ==========================================================================
# Group A and B under generative testing.
#
# Fixed examples check the cases you thought of. These check the ones you
# did not.
# ==========================================================================

@pytest.mark.skipif(not HAVE_HYPOTHESIS, reason="hypothesis not installed")
@settings(max_examples=200, deadline=None)
@given(
    n_cells=st.integers(min_value=1, max_value=40),
    b=st.floats(min_value=0.5, max_value=1.6, allow_nan=False, allow_infinity=False),
    seed=st.integers(min_value=0, max_value=10_000),
)
def test_conservation_holds_for_arbitrary_valid_forecasts(n_cells, b, seed):
    import random

    rng = random.Random(seed)
    cells = list(range(n_cells))
    # Deliberately include exact zeros, which is where normalisation by a
    # computed sum would divide by zero.
    rates = {c: (0.0 if rng.random() < 0.3 else rng.uniform(0, 10)) for c in cells}
    dense = expander.expand(make_separable(cells, rates, b=b))

    assert len(dense.values) == len(cells) * len(dense.bins)
    assert all(math.isfinite(v) and v >= 0.0 for v in dense.values)
    assert sum(dense.values) == pytest.approx(
        sum(rates.values()), rel=expander.CONSERVATION_RTOL, abs=1e-300
    )


@pytest.mark.skipif(not HAVE_HYPOTHESIS, reason="hypothesis not installed")
@settings(max_examples=100, deadline=None)
@given(
    n_cells=st.integers(min_value=1, max_value=25),
    b=st.floats(min_value=0.5, max_value=1.6, allow_nan=False, allow_infinity=False),
)
def test_determinism_holds_for_arbitrary_valid_forecasts(n_cells, b):
    cells = list(range(n_cells))
    rates = {c: float(c) for c in cells}
    sep = make_separable(cells, rates, b=b)
    assert expander.canonical_bytes(expander.expand(sep)) == expander.canonical_bytes(
        expander.expand(sep)
    )
