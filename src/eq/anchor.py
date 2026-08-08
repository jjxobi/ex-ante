"""Timestamp anchoring: making "this forecast existed before its window" checkable.

Per DECISIONS.md D10, the project's central claim rests on being able to prove
a forecast was committed before the window it describes. Git commit timestamps
cannot carry that proof alone, because both the author date and the committer
date are fields the person making the commit sets, not fields any third party
witnesses. A skeptical reader who noticed that would be right to stop trusting
the scoreboard.

D10 records two independent anchors for every forecast, in decreasing order of
strength:

1. A GitHub Actions run ID. GitHub timestamps the start of a workflow run on
   its own servers, and that timestamp is retrievable through the Actions API
   independently of anything in this repository. The claim becomes "run N
   executed at time T according to GitHub", which does not depend on trusting
   this project's author.
2. The commit itself. Cheap to check, and worth recording, but it is the
   weaker of the two anchors by construction and is never treated as proof on
   its own.

A third anchor, a Bitcoin-anchored proof over the forecast file, was built,
used, and later removed. It is not described here because it is gone from
this module; the reasoning for building it and for removing it again lives in
DECISIONS.md D10, in the same place D3 and D4a record rejected approaches.
This module no longer imports the library that anchor depended on, and a
forecast manifest built here no longer carries a field for it.

What this module still does for tamper detection
--------------------------------------------------
`manifest_for()` records the forecast file's own SHA-256, recomputed from disk
at manifest-build time. `verify()` recomputes that digest again later and
compares it against a recorded value. This check has nothing to do with the
removed third anchor: it establishes only that the bytes have not changed
since the digest was recorded, not when they first existed. It was useful
before that anchor was removed and remains just as useful after, so it
stays.

The second subtlety: an absent CI anchor is a fact, not a null
------------------------------------------------------------------
Locally there is no `GITHUB_RUN_ID`. A forecast published from a laptop has
genuinely weaker provenance than one published by a CI run, because nobody
outside this machine can independently confirm when a laptop run happened.
`manifest_for()` therefore never writes a bare `null` for the CI anchor.
It writes an explicit `"present": False` plus a note explaining why, so a
renderer (or a reader of the raw JSON) can act on the absence rather than
mistake it for a field that simply was not populated.
"""

from __future__ import annotations

import hashlib
import json
import os
import subprocess
from datetime import datetime, timezone
from pathlib import Path

from eq import paths

STATUS_OK = "ok"
STATUS_TAMPERED = "tampered"
STATUS_MISSING = "missing"

_COMMIT_NOTE = (
    "the weaker of the two anchors per D10: both the author date and the "
    "committer date are fields the committer sets, so this is a cheap first "
    "check, never proof on its own."
)

CI_ABSENT_NOTE = (
    "no GITHUB_RUN_ID in the environment: this forecast was not published by "
    "a GitHub Actions run, so it carries no server-side CI timestamp. "
    "Provenance for this forecast rests on the commit alone, which is weaker "
    "than a CI-published forecast."
)

CI_PRESENT_NOTE = (
    "server-side timestamped by GitHub Actions; the run start time is "
    "retrievable through the Actions API independently of this repository."
)


class AnchorError(RuntimeError):
    """Base class for this module's errors."""


class GitCommitUnavailableError(AnchorError):
    """Raised when the current commit SHA cannot be resolved.

    The commit anchor is explicitly the weaker of the two per D10, but a
    manifest missing it entirely is worse than a manifest that admits it is
    weak: silently omitting it would look like an oversight rather than a
    documented limitation.
    """


# --------------------------------------------------------------------------
# Hashing and small utilities
# --------------------------------------------------------------------------

def sha256_bytes(data: bytes) -> str:
    """Hex SHA-256 of raw bytes."""
    return hashlib.sha256(data).hexdigest()


def sha256_file(path: Path) -> str:
    """Hex SHA-256 of a file's actual bytes, read fresh from disk every call."""
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def _now_utc_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _as_utc_iso(value) -> str:
    """Normalise a window boundary to a canonical UTC ISO-8601 string.

    Per D12, every window boundary is UTC and boundary comparisons must be
    instant-based, never string-based. This function is the one place a
    boundary is turned into a string, so that formatting never leaks back
    into a comparison anywhere else. It refuses an offset-naive datetime
    outright: an offset-naive value cannot be verified as UTC by a reader,
    which is exactly the ambiguity D12 was written to eliminate.
    """
    if isinstance(value, str):
        dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
    elif isinstance(value, datetime):
        dt = value
    else:
        raise TypeError(
            f"window bound must be a datetime or an ISO-8601 string, got {type(value)!r}"
        )
    if dt.tzinfo is None:
        raise ValueError(
            "window bound is offset-naive; D12 requires every window boundary "
            "to be UTC, and an offset-naive value cannot be verified as UTC by "
            "a reader. Pass a timezone-aware datetime."
        )
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _repo_relative(path: Path) -> str:
    path = Path(path).resolve()
    try:
        return path.relative_to(paths.REPO_ROOT).as_posix()
    except ValueError:
        return str(path)


# --------------------------------------------------------------------------
# The commit anchor
# --------------------------------------------------------------------------

def _git_commit_sha(repo_root: Path = paths.REPO_ROOT) -> str:
    """The current commit SHA, per `git rev-parse HEAD`.

    This is the commit of the code that produced the forecast, resolved at
    manifest-build time, not a forecast's own eventual commit: the manifest
    is written before that commit exists, so it cannot reference it.
    """
    result = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=repo_root,
        capture_output=True,
        text=True,
        check=False,
    )
    sha = result.stdout.strip()
    if result.returncode != 0 or not sha:
        raise GitCommitUnavailableError(
            f"could not resolve the current commit via `git rev-parse HEAD` in "
            f"{repo_root}: {result.stderr.strip()!r}. The commit anchor is the "
            f"weaker of the two per D10, but it must still be present, not "
            f"silently dropped."
        )
    return sha


def _commit_anchor() -> dict:
    return {
        "sha": _git_commit_sha(),
        "note": _COMMIT_NOTE,
    }


# --------------------------------------------------------------------------
# The GitHub Actions anchor
# --------------------------------------------------------------------------

def _ci_anchor() -> dict:
    """The Actions run anchor, or an explicit, distinguishable absence.

    See the module docstring's second subtlety: `present: False` plus a note
    is written rather than a bare null, so a renderer can act on the absence
    instead of mistaking it for an unpopulated field.
    """
    run_id = os.environ.get("GITHUB_RUN_ID")
    if not run_id:
        return {
            "present": False,
            "run_id": None,
            "run_attempt": None,
            "workflow": None,
            "repository": None,
            "run_url": None,
            "note": CI_ABSENT_NOTE,
        }

    repository = os.environ.get("GITHUB_REPOSITORY")
    return {
        "present": True,
        "run_id": run_id,
        "run_attempt": os.environ.get("GITHUB_RUN_ATTEMPT"),
        "workflow": os.environ.get("GITHUB_WORKFLOW"),
        "repository": repository,
        "run_url": (
            f"https://github.com/{repository}/actions/runs/{run_id}"
            if repository
            else None
        ),
        "note": CI_PRESENT_NOTE,
    }


# --------------------------------------------------------------------------
# Tamper detection by hash comparison
# --------------------------------------------------------------------------

def verify(path: Path, expected_sha256: str) -> dict:
    """Recompute `path`'s digest and compare it against a previously recorded one.

    This is the tamper-detection check that survives the removal of the third
    D10 anchor (DECISIONS.md D10): it does not depend on that anchor, on
    GitHub, or on any external party, only on a digest recorded earlier
    (typically `manifest["sha256"]` from `manifest_for()`).

    Returns `status` "ok" when the current bytes hash to `expected_sha256`,
    "tampered" when they hash to something else, and "missing" when `path`
    does not exist at all. This function makes no claim about *when* the file
    first existed, only about whether its bytes have changed since the digest
    was recorded.
    """
    path = Path(path)

    if not path.exists():
        return {
            "file_matches": False,
            "actual_sha256": None,
            "expected_sha256": expected_sha256,
            "status": STATUS_MISSING,
            "reason": f"{path} does not exist; cannot recompute its digest.",
        }

    actual = sha256_file(path)
    matches = actual == expected_sha256

    return {
        "file_matches": matches,
        "actual_sha256": actual,
        "expected_sha256": expected_sha256,
        "status": STATUS_OK if matches else STATUS_TAMPERED,
        "reason": (
            "the file's current bytes hash to the recorded digest."
            if matches
            else "the file's current bytes do not hash to the recorded digest. "
            "Either the file changed after the digest was recorded, or this is "
            "the wrong digest for this file."
        ),
    }


# --------------------------------------------------------------------------
# The manifest
# --------------------------------------------------------------------------

def manifest_for(
    path: Path,
    *,
    window_start,
    window_end,
    model: str,
    stratum: str,
) -> dict:
    """Build the D10 provenance manifest for a real, already-written file.

    Records the file's own SHA-256 (recomputed from disk, never trusted from
    a caller), the forecast window, model and stratum, the publication time,
    and both D10 anchors: the commit (always present, always noted as the
    weaker one) and the GitHub Actions run (present with detail, or
    explicitly and distinguishably absent).

    Raises FileNotFoundError if `path` does not exist: a manifest for a file
    that was never actually written would be worse than no manifest, since it
    would look like evidence.
    """
    path = Path(path)
    if not path.exists():
        raise FileNotFoundError(f"cannot build a manifest for {path}: it does not exist")

    window_start_utc = _as_utc_iso(window_start)
    window_end_utc = _as_utc_iso(window_end)
    if window_end_utc <= window_start_utc:
        raise ValueError(
            f"window_end ({window_end_utc}) must be after window_start "
            f"({window_start_utc})"
        )

    return {
        "file": _repo_relative(path),
        "sha256": sha256_file(path),
        "window_start_utc": window_start_utc,
        "window_end_utc": window_end_utc,
        "model": model,
        "stratum": stratum,
        "published_at_utc": _now_utc_iso(),
        "anchors": {
            "commit": _commit_anchor(),
            "ci_run": _ci_anchor(),
        },
    }


def write_manifest(manifest: dict, destination: Path) -> Path:
    """Write a manifest as JSON, atomically. Returns the destination path.

    Temp-then-replace, matching storage.write_parquet_atomic's pattern: a
    crashed or failed write never leaves a partial manifest where a reader
    might find it.
    """
    destination = Path(destination)
    destination.parent.mkdir(parents=True, exist_ok=True)
    temp_path = destination.with_name(destination.name + ".tmp")
    try:
        temp_path.write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
        os.replace(temp_path, destination)
    finally:
        if temp_path.exists():
            temp_path.unlink()
    return destination
