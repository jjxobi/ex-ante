"""Canonical repository paths.

Every component resolves paths through this module so that no path string is
written twice. REPO_ROOT is found by walking upward until DECISIONS.md is seen,
which keeps the package importable from any working directory. This module
covers the paths Python code needs; dbt's own connection path lives in
`dbt/profiles.yml`, not here.
"""

from pathlib import Path


def _find_repo_root() -> Path:
    here = Path(__file__).resolve()
    for candidate in [here, *here.parents]:
        if (candidate / "DECISIONS.md").is_file():
            return candidate
    raise RuntimeError("could not locate repository root: no DECISIONS.md found")


REPO_ROOT = _find_repo_root()

DATA_DIR = REPO_ROOT / "data"
RAW_DIR = DATA_DIR / "raw"
SNAPSHOT_DIR = DATA_DIR / "snapshots"
EVALUATION_DIR = DATA_DIR / "evaluation"
REVISION_DIR = DATA_DIR / "revisions"

# Not under data/: the frozen grid and depth boundary are committed artifacts,
# not raw catalogue data, and data/ is gitignored while region/ is not.
REGION_DIR = REPO_ROOT / "region"
MEASUREMENTS_DIR = REPO_ROOT / "scripts" / "measurements"


def ensure_dirs() -> None:
    """Create every data directory this project writes to."""
    for directory in (RAW_DIR, SNAPSHOT_DIR, EVALUATION_DIR, REVISION_DIR):
        directory.mkdir(parents=True, exist_ok=True)
