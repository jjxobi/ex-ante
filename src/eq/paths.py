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

# Not under data/, for the same reason REGION_DIR is not. D7 requires the
# T+45 frozen evaluation catalogue to ship in the same commit as the score it
# produced, so it cannot live under data/, which is entirely gitignored. This
# is a separate directory from EVALUATION_DIR above on purpose: EVALUATION_DIR
# is working, gitignored space, while this one holds the committed, one file
# per scored window artifact D7 requires.
EVALUATION_CATALOGUE_DIR = REPO_ROOT / "evaluation"

# Committed, per the design spec section 6 repository layout. A forecast has
# to predate its window in the public git history (D10, D11), so the file has
# to be committed, which rules out data/ the same way it rules out
# EVALUATION_CATALOGUE_DIR and REGION_DIR above.
FORECASTS_DIR = REPO_ROOT / "forecasts"

# Committed static JSON a site reads. Regenerated every publication cycle from
# already-committed forecasts, manifests and scores, so it carries no
# information that is not independently reconstructible from the rest of the
# repository.
SITE_DIR = REPO_ROOT / "site"


def ensure_dirs() -> None:
    """Create every data directory this project writes to."""
    for directory in (RAW_DIR, SNAPSHOT_DIR, EVALUATION_DIR, REVISION_DIR):
        directory.mkdir(parents=True, exist_ok=True)
