"""Tests for the frozen collection region: grid, hash, and depth stratum.

The grid itself is generated once by eq.region.build_and_write() and committed
to region/grid.parquet. These tests read the committed artifact rather than
regenerating it, the same way every other component is meant to use this
module: the region is frozen, and freezing it is the point.

The adversarial longitude edges reused below come from test_grid_binning.py,
which is where they are measured and frozen. A version of the edge test built
on cell interiors would pass while proving nothing, per D13.2: the naive and
integer paths only ever diverge exactly on a cell edge.
"""

from __future__ import annotations

import hashlib
import re
from pathlib import Path

import pytest

from eq import paths, region, storage
from test_grid_binning import (
    DH,
    LAT_MAX,
    LAT_MIN,
    LON_MAX,
    LON_MIN,
    disagreements,
    edges,
    integer_bin,
    naive_bin,
)

DECISIONS_PATH = paths.REPO_ROOT / "DECISIONS.md"


# --------------------------------------------------------------------------
# The frozen grid itself
# --------------------------------------------------------------------------

def test_grid_has_exactly_4100_cells():
    grid = region.load_grid()
    assert len(grid) == 4100


def test_grid_has_no_duplicate_cell_ids():
    grid = region.load_grid()
    ids = [row["cell_id"] for row in grid]
    assert len(ids) == len(set(ids))


def test_grid_cell_ids_and_bounds_are_integers_not_floats():
    """D13.2 is explicit: cell identifiers are integers, bounds are never floats."""
    grid = region.load_grid()
    for row in grid[:50] + grid[-50:]:
        assert isinstance(row["cell_id"], int)
        assert isinstance(row["lon_deci"], int)
        assert isinstance(row["lat_deci"], int)
        # A parquet round trip can hand back numpy integer scalars rather than
        # plain int; guard against that silently becoming a float subclass.
        assert float(row["cell_id"]).is_integer()
        assert float(row["lon_deci"]).is_integer()
        assert float(row["lat_deci"]).is_integer()


def test_grid_is_exactly_the_41_region_cells_times_100():
    """Every 0.1 degree cell inside the 41 retained 1 degree cells, D5's rule."""
    grid = region.load_grid()
    region_cells = {(row["region_cell_lon"], row["region_cell_lat"]) for row in grid}
    assert len(region_cells) == 41
    counts = {}
    for row in grid:
        key = (row["region_cell_lon"], row["region_cell_lat"])
        counts[key] = counts.get(key, 0) + 1
    assert all(count == 100 for count in counts.values())


def test_grid_matches_the_committed_completeness_table():
    """The frozen grid is exactly the cells D1's rule retains.

    This reads region/mc_by_cell.parquet, the committed per-cell completeness
    measurement, rather than recomputing from 96.7 MB of raw catalogues that
    are gitignored and therefore absent on any fresh clone. An earlier version
    of this test did the latter and failed in CI for that reason.

    Committing the intermediate is better than committing the inputs: it is
    2.4 KB, a reader can audit the rule against it without downloading
    anything, and the deeper check that the raw catalogues still reproduce this
    table lives in scripts/measurements/region_rule_regeneration.py, which is
    where a dependency on bulk local data belongs.
    """
    table = storage.read_parquet(paths.REPO_ROOT / "region" / "mc_by_cell.parquet")
    assert len(table) == 145, "expected 145 one-degree cells with any seismicity"

    retained = {
        (row["region_cell_lon"], row["region_cell_lat"])
        for row in table
        if row["retained"]
    }
    assert len(retained) == 41, "D1 freezes the region at 41 cells"

    grid_region_cells = {
        (row["region_cell_lon"], row["region_cell_lat"]) for row in region.load_grid()
    }
    assert retained == grid_region_cells


def test_the_completeness_table_agrees_with_d1s_stated_rule():
    """Re-applies D1's rule to the committed table rather than trusting its
    `retained` column, so a mislabelled row cannot pass unnoticed.
    """
    table = storage.read_parquet(paths.REPO_ROOT / "region" / "mc_by_cell.parquet")
    for row in table:
        expected = row["mc"] is not None and row["mc"] <= 2.6
        assert row["retained"] == expected, (
            f"cell ({row['region_cell_lon']}, {row['region_cell_lat']}) is marked "
            f"retained={row['retained']} but has n={row['n_events']} mc={row['mc']}"
        )


def test_every_excluded_cell_named_in_decisions_is_excluded_in_the_table():
    """D1 names 11 measurable cells that fail the completeness test. If the
    table and the constitution ever disagree, one of them is wrong.
    """
    named = {
        (180, -33), (181, -34), (181, -35), (179, -35), (180, -34), (179, -34),
        (179, -36), (178, -36), (179, -33), (180, -38), (179, -37),
    }
    table = storage.read_parquet(paths.REPO_ROOT / "region" / "mc_by_cell.parquet")
    retained = {
        (row["region_cell_lon"], row["region_cell_lat"])
        for row in table
        if row["retained"]
    }
    assert not (named & retained), "a cell D1 excludes is present in the region"


# --------------------------------------------------------------------------
# Cell assignment on edges, per D13.2. Reuses the adversarial set from
# tests/test_grid_binning.py rather than re-deriving it.
# --------------------------------------------------------------------------

def test_to_decidegree_matches_integer_bin_on_all_39_adversarial_edges():
    """The naive float path and the frozen integer path only diverge on edges.

    naive_bin and integer_bin disagree at exactly 39 longitude edges (measured
    in scripts/measurements/grid_edge_hazard.py). eq.region.to_decidegree must
    agree with integer_bin, the correct answer, at every one of them, not with
    naive_bin.
    """
    bad_edges = disagreements(LON_MIN, LON_MAX, LON_MIN)
    assert len(bad_edges) == 39
    for x in bad_edges:
        assert naive_bin(x, LON_MIN) != integer_bin(x, LON_MIN), (
            "test_grid_binning's adversarial set stopped disagreeing; it has "
            "drifted from what this test assumes"
        )
        want = integer_bin(x, LON_MIN)
        got = region.to_decidegree(x) - region.to_decidegree(LON_MIN)
        assert got == want, f"eq.region.to_decidegree disagrees with the correct answer at {x}"


def test_to_decidegree_is_exact_on_every_edge_in_the_region():
    """Not just the 39 adversarial ones: every edge on both axes."""
    for lo, hi, origin in ((LON_MIN, LON_MAX, LON_MIN), (LAT_MIN, LAT_MAX, LAT_MIN)):
        for i, x in enumerate(edges(lo, hi)):
            got = region.to_decidegree(x) - region.to_decidegree(origin)
            assert got == i, f"disagreement at edge {x} (axis origin {origin})"


def test_cell_id_for_agrees_on_a_grid_corner_exactly_on_an_adversarial_edge():
    """An event landing exactly on one of the 39 adversarial longitudes must
    still land in the correct cell, not the cell one over.
    """
    grid = region.load_grid()
    # 174.2 is one of the measured adversarial edges and sits inside the
    # retained region cell (174, -41).
    adversarial_lon = 174.2
    assert adversarial_lon in disagreements(LON_MIN, LON_MAX, LON_MIN)
    lat_inside_cell = -40.95  # interior of the (174, -41) 1 degree cell
    got = region.cell_id_for(adversarial_lon, lat_inside_cell)
    assert got is not None
    matching = [row for row in grid if row["cell_id"] == got]
    assert len(matching) == 1
    assert matching[0]["lon_deci"] == 1742


# --------------------------------------------------------------------------
# Out of region handling
# --------------------------------------------------------------------------

def test_point_outside_the_region_returns_none():
    """Never raises, never clamps. D1: out-of-region events are counted, not dropped."""
    # Far outside the whole bounding box.
    assert region.cell_id_for(0.0, 0.0) is None
    # Inside the bounding box, but in a cell D1 excludes (Kermadec, high Mc).
    assert region.cell_id_for(180.5, -32.5) is None


def test_point_on_the_far_edge_of_the_bounding_box_returns_none_not_an_error():
    assert region.cell_id_for(LON_MAX, LAT_MIN) is None
    assert region.cell_id_for(LON_MIN - 5, LAT_MIN - 5) is None


def test_negative_longitude_is_normalised_not_wrapped():
    """A raw GeoNet longitude near the antimeridian arrives negative and must
    still resolve to the same cell as its +360 equivalent.
    """
    negative = -179.5
    positive = 180.5
    assert region.lon360(negative) == pytest.approx(positive)
    assert region.cell_id_for(negative, -37.5) == region.cell_id_for(positive, -37.5)


# --------------------------------------------------------------------------
# stratum_for
# --------------------------------------------------------------------------

def test_stratum_for_is_shallow_at_exactly_41km():
    assert region.stratum_for(41.0) == "shallow"


def test_stratum_for_is_deep_just_above_41km():
    assert region.stratum_for(41.001) == "deep"


def test_stratum_for_far_from_the_boundary():
    assert region.stratum_for(0.0) == "shallow"
    assert region.stratum_for(5.0) == "shallow"
    assert region.stratum_for(600.0) == "deep"


# --------------------------------------------------------------------------
# Hash assertion
# --------------------------------------------------------------------------

def test_grid_hash_reads_the_committed_file():
    committed = region.HASH_PATH.read_text(encoding="utf-8").strip()
    assert region.grid_hash() == committed
    assert re.fullmatch(r"[0-9a-f]{64}", committed), "not a sha256 hex digest"


def test_grid_hash_matches_the_actual_file_bytes():
    """D1: the hash covers region/grid.parquet itself, not the rule or its inputs."""
    actual = hashlib.sha256(region.GRID_PATH.read_bytes()).hexdigest()
    assert region.grid_hash() == actual


def test_assert_grid_hash_passes_on_the_right_hash():
    region.assert_grid_hash(region.grid_hash())  # must not raise


def test_assert_grid_hash_raises_on_a_wrong_hash():
    with pytest.raises(region.GridHashMismatchError):
        region.assert_grid_hash("0" * 64)


def test_assert_grid_hash_message_names_both_hashes():
    wrong = "f" * 64
    with pytest.raises(region.GridHashMismatchError) as excinfo:
        region.assert_grid_hash(wrong)
    assert wrong in str(excinfo.value)
    assert region.grid_hash() in str(excinfo.value)


# --------------------------------------------------------------------------
# The constitution must not drift from reality.
# --------------------------------------------------------------------------

def test_decisions_md_records_the_actual_grid_hash():
    """D1 promises the SHA-256 of region/grid.parquet is recorded in the file.

    This is the test that stops the constitution drifting from the artifact:
    if the grid is ever regenerated and the hash in DECISIONS.md is not
    updated in the same commit, this fails.
    """
    text = DECISIONS_PATH.read_text(encoding="utf-8")
    match = re.search(r"SHA-256: `([0-9a-f]{64})`", text)
    assert match is not None, "DECISIONS.md D1 does not record a grid SHA-256"
    assert match.group(1) == region.grid_hash()


# --------------------------------------------------------------------------
# Boundary artifact
# --------------------------------------------------------------------------

def test_boundary_json_records_the_frozen_boundary_and_sensitivity_curve():
    import json

    payload = json.loads(region.BOUNDARY_PATH.read_text(encoding="utf-8"))
    assert payload["frozen_boundary_km"] == 41
    assert payload["fitted_boundary_km"] == pytest.approx(41.1, abs=0.05)
    assert payload["silverman_bandwidth"] == pytest.approx(0.0607, abs=0.0005)
    multipliers = [row["bandwidth_multiplier"] for row in payload["sensitivity_curve"]]
    assert multipliers == [0.7, 0.85, 1.0, 1.2, 1.5]
    boundaries = {row["bandwidth_multiplier"]: row["boundary_km"] for row in payload["sensitivity_curve"]}
    assert boundaries[0.7] == pytest.approx(38.7, abs=0.1)
    assert boundaries[0.85] == pytest.approx(40.0, abs=0.1)
    assert boundaries[1.0] == pytest.approx(41.1, abs=0.1)
    assert boundaries[1.2] == pytest.approx(42.9, abs=0.1)
    assert boundaries[1.5] == pytest.approx(45.3, abs=0.1)
