import os
from pathlib import Path

import pytest

# --------------------------------------------------------------------------
# Hypothesis profiles
#
# By default hypothesis seeds randomly and caches counterexamples in a local
# .hypothesis directory that CI throws away, so a failure found in CI is not
# reproducible locally. That sits badly in a project whose central claim is
# reproducibility.
#
# CI therefore runs derandomised, so the same examples run every time and a
# CI failure can be reproduced exactly. Local development keeps randomisation,
# where exploration is what you actually want.
# --------------------------------------------------------------------------
try:
    from hypothesis import settings as _hyp_settings

    _hyp_settings.register_profile("ci", derandomize=True, print_blob=True)
    _hyp_settings.register_profile("dev", derandomize=False)
    _hyp_settings.load_profile(os.getenv("HYPOTHESIS_PROFILE", "dev"))
except ImportError:  # pragma: no cover
    pass


# --------------------------------------------------------------------------
# Skip-set interlock
#
# "2 skipped" is a number nobody reads. A third skip appearing because an
# import broke or a fixture vanished looks identical to the two that were
# intended, and the suite stays green either way. This pins the exact set of
# node IDs allowed to skip and fails if the actual set differs in EITHER
# direction.
#
# The "either direction" half is the useful one: when Phase 2 lands and the
# expander tests should start passing, a silent continued skip becomes a
# failure rather than a green suite.
# --------------------------------------------------------------------------
EXPECTED_SKIPS_FILE = Path(__file__).parent / "expected_skips.txt"
_skipped_node_ids: set[str] = set()


def _load_expected_skips() -> set[str]:
    if not EXPECTED_SKIPS_FILE.exists():
        return set()
    return {
        line.strip()
        for line in EXPECTED_SKIPS_FILE.read_text(encoding="utf-8").splitlines()
        if line.strip() and not line.lstrip().startswith("#")
    }


def pytest_runtest_logreport(report):
    """Per-test skips, for example a skipif marker."""
    if report.skipped and report.when == "setup":
        _skipped_node_ids.add(report.nodeid.replace("\\", "/"))


def pytest_collectreport(report):
    """Module-level skips.

    A module-level importorskip raises during collection, so no test is ever
    collected from that file and pytest_runtest_logreport never fires. Without
    this hook a whole skipped module would be invisible to the interlock, which
    is exactly the blind spot the interlock exists to close.
    """
    if report.skipped and report.nodeid:
        _skipped_node_ids.add(report.nodeid.replace("\\", "/"))


def _is_full_run(config) -> bool:
    """Only enforce on a whole-suite run, not on a filtered subset."""
    return not (
        getattr(config.option, "file_or_dir", None)
        or getattr(config.option, "keyword", None)
        or getattr(config.option, "markexpr", None)
        or getattr(config.option, "last_failed", False)
    )


def pytest_sessionfinish(session, exitstatus):
    config = session.config
    if not _is_full_run(config) or not EXPECTED_SKIPS_FILE.exists():
        return
    if getattr(config, "workerinput", None) is not None:  # pragma: no cover
        return

    expected = _load_expected_skips()
    actual = set(_skipped_node_ids)
    unexpected = sorted(actual - expected)
    no_longer = sorted(expected - actual)
    if not unexpected and not no_longer:
        return

    lines = ["", "SKIP SET MISMATCH. tests/expected_skips.txt is the contract."]
    if unexpected:
        lines.append("  Skipped but not expected to be:")
        lines += [f"    + {n}" for n in unexpected]
        lines.append("  A new skip hides a test. Fix the cause, or add it deliberately.")
    if no_longer:
        lines.append("  Expected to skip but did not:")
        lines += [f"    - {n}" for n in no_longer]
        lines.append("  If this test now runs, remove it from the file in the same commit.")
    print("\n".join(lines))
    session.exitstatus = 1


SAMPLE_HEADER = (
    "publicid,eventtype,origintime,modificationtime,longitude,latitude,magnitude,"
    "depth,magnitudetype,depthtype,evaluationmethod,evaluationstatus,evaluationmode,"
    "earthmodel,usedphasecount,usedstationcount,magnitudestationcount,minimumdistance,"
    "azimuthalgap,originerror,magnitudeuncertainty"
)

SAMPLE_ROWS = [
    (
        "2026p083320,earthquake,2026-01-31T19:53:16.616Z,2026-03-02T21:59:29.607Z,"
        "177.6536407470703,-37.31378936767578,3.213517159669955,35.041107177734375,"
        "MLv,,LOCSAT,confirmed,manual,iasp91,52,35,23,0.45,186.14,0.56,0.22"
    ),
    (
        "2026p083039,earthquake,2026-01-31T17:23:39.040Z,2026-03-02T21:20:11.603Z,"
        "-179.5,-44.46416473388672,3.159493173743447,5,"
        "MLv,operator assigned,LOCSAT,confirmed,manual,iasp91,42,30,13,0.36,39.58,0.68,0.17"
    ),
]


@pytest.fixture
def sample_csv() -> str:
    return SAMPLE_HEADER + "\n" + "\n".join(SAMPLE_ROWS) + "\n"
