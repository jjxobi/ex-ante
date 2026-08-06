"""Timestamp anchoring: making "this forecast existed before its window" checkable.

Per DECISIONS.md D10, the project's central claim rests on being able to prove
a forecast was committed before the window it describes. Git commit timestamps
cannot carry that proof alone, because both the author date and the committer
date are fields the person making the commit sets, not fields any third party
witnesses. A skeptical reader who noticed that would be right to stop trusting
the scoreboard.

D10 therefore requires three independent anchors, recorded together for every
forecast, in decreasing order of strength:

1. A GitHub Actions run ID. GitHub timestamps the start of a workflow run on
   its own servers, and that timestamp is retrievable through the Actions API
   independently of anything in this repository. The claim becomes "run N
   executed at time T according to GitHub", which does not depend on trusting
   this project's author.
2. An OpenTimestamps proof. A calendar server first commits to a SHA-256
   digest immediately and cheaply; that commitment is later folded into a
   Bitcoin transaction and becomes verifiable by anyone, forever, against the
   Bitcoin blockchain alone, with no trust in the calendar, in GitHub, or in
   this project's author. This is the strongest anchor precisely because it is
   the one that depends on nobody.
3. The commit itself. Cheap to check, and worth recording, but it is the
   weakest anchor by construction and is never treated as proof on its own.

Why this module does not shell out to the `ots` command-line client
---------------------------------------------------------------------
opentimestamps-client 0.7.2's CLI imports `bitcoin.rpc`, which imports
`bitcoin.wallet`, which imports `bitcoin.core.key`, which loads OpenSSL via
`ctypes.util.find_library`. That lookup returns `None` on this Windows
machine (a known python-bitcoinlib limitation unrelated to whether OpenSSL is
actually installed), so the CLI cannot even start here: `ots stamp` fails
before it does anything. None of that machinery is needed for what this
module does. Submitting a digest to a calendar and reading back a proof is
plain HTTP handled by `opentimestamps.calendar.RemoteCalendar`, and the proof
format is handled by `opentimestamps.core.timestamp`. Neither module imports
`bitcoin.rpc`. This module talks to those two directly and never imports
`bitcoin.wallet`, `bitcoin.rpc`, or the `otsclient` package.

The subtlety this module exists to enforce
---------------------------------------------
A proof created by `stamp()` is NOT yet Bitcoin anchored. `stamp()` only
reaches the calendar server's immediate response, which is a promise to
attest later ("pending"), not an attestation. Calendar servers batch pending
digests into a single Bitcoin transaction and only complete the upgrade once
that transaction has confirmed on-chain, which normally takes several hours.
Reporting a pending proof as Bitcoin anchored would make this project's
central claim stronger than the evidence supports, which is the exact failure
this module is built to prevent. `proof_status()` therefore returns
"pending", never "confirmed", until a real `BitcoinBlockHeaderAttestation`
has been merged into the local proof by `upgrade()`. That distinction is
carried into every manifest field and every string this module produces; see
`_PROOF_NOTES` below, which is the single place that wording is written so it
cannot drift between callers.

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

from opentimestamps.calendar import CommitmentNotFoundError, RemoteCalendar
from opentimestamps.core.notary import BitcoinBlockHeaderAttestation, PendingAttestation
from opentimestamps.core.op import OpSHA256
from opentimestamps.core.serialize import (
    BadMagicError,
    DeserializationError,
    StreamDeserializationContext,
    StreamSerializationContext,
)
from opentimestamps.core.timestamp import DetachedTimestampFile, Timestamp

from eq import paths

# --------------------------------------------------------------------------
# Calendar servers. Same defaults `ots stamp` uses (see otsclient.cmds), kept
# here rather than imported because importing otsclient triggers the
# bitcoin.rpc chain described above.
# --------------------------------------------------------------------------
DEFAULT_CALENDAR_URLS = (
    "https://a.pool.opentimestamps.org",
    "https://b.pool.opentimestamps.org",
    "https://a.pool.eternitywall.com",
    "https://ots.btc.catallaxy.com",
)

DEFAULT_TIMEOUT_SECONDS = 20

PROOF_SUFFIX = ".ots"

STATUS_PENDING = "pending"
STATUS_CONFIRMED = "confirmed"
STATUS_MISSING = "missing"

# The one place the pending/confirmed wording is written. Every caller
# (manifest_for, verify, upgrade) reuses these strings rather than composing
# its own, so "Bitcoin anchored" can never be typed next to a pending proof
# by accident in some other corner of this module.
_PROOF_NOTES = {
    STATUS_CONFIRMED: (
        "Bitcoin anchored: a BitcoinBlockHeaderAttestation is present in the "
        "local proof, meaning a calendar server's commitment was folded into "
        "a Bitcoin transaction and that transaction's block header has been "
        "merged into this proof."
    ),
    STATUS_PENDING: (
        "NOT Bitcoin anchored yet. This is a calendar-server commitment only "
        "(a PendingAttestation): a promise to fold the digest into a Bitcoin "
        "transaction, not yet a Bitcoin attestation. Confirmation normally "
        "takes several hours. Call eq.anchor.upgrade() later to check whether "
        "it has completed."
    ),
    STATUS_MISSING: (
        "no OpenTimestamps proof exists for this file yet. Call "
        "eq.anchor.stamp() to create one."
    ),
}

_CI_ABSENT_NOTE = (
    "no GITHUB_RUN_ID in the environment: this forecast was not published by "
    "a GitHub Actions run, so it carries no server-side CI timestamp. "
    "Provenance for this forecast rests on the OpenTimestamps proof and the "
    "commit alone, which is weaker than a CI-published forecast."
)

_CI_PRESENT_NOTE = (
    "server-side timestamped by GitHub Actions; the run start time is "
    "retrievable through the Actions API independently of this repository."
)

_COMMIT_NOTE = (
    "the weakest of the three anchors per D10: both the author date and the "
    "committer date are fields the committer sets, so this is a cheap first "
    "check, never proof on its own."
)


class AnchorError(RuntimeError):
    """Base class for this module's errors."""


class GitCommitUnavailableError(AnchorError):
    """Raised when the current commit SHA cannot be resolved.

    The commit anchor is explicitly the weakest of the three per D10, but a
    manifest missing it entirely is worse than a manifest that admits it is
    weak: silently omitting it would look like an oversight rather than a
    documented limitation.
    """


class ProofExistsError(AnchorError):
    """Raised when stamp() is asked to overwrite an existing .ots proof.

    A proof, once created, is evidence: the calendar server timestamped
    exactly those bytes at exactly that submission time. Silently replacing
    it with a fresh submission would throw away that evidence and quietly
    move the clock forward, which is the same failure D11 exists to prevent
    for forecasts. Completing a proof over time is what upgrade() is for.
    """


class OpenTimestampsSubmissionError(AnchorError):
    """Raised when no calendar server accepted the digest."""


class CorruptProofError(AnchorError):
    """Raised when an .ots file exists but cannot be parsed."""


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


def _ots_path_for(path: Path) -> Path:
    return Path(str(Path(path)) + PROOF_SUFFIX)


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
            f"weakest of the three per D10, but it must still be present, not "
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
            "note": _CI_ABSENT_NOTE,
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
        "note": _CI_PRESENT_NOTE,
    }


# --------------------------------------------------------------------------
# The OpenTimestamps anchor: proof creation, status, upgrade, verification
# --------------------------------------------------------------------------

def _load_ots(ots_path: Path) -> DetachedTimestampFile:
    ots_path = Path(ots_path)
    try:
        with open(ots_path, "rb") as fh:
            ctx = StreamDeserializationContext(fh)
            return DetachedTimestampFile.deserialize(ctx)
    except (BadMagicError, DeserializationError) as exc:
        raise CorruptProofError(f"{ots_path} is not a valid OpenTimestamps proof: {exc}") from exc


def _write_ots(detached: DetachedTimestampFile, ots_path: Path) -> None:
    """Write a DetachedTimestampFile, temp-then-replace, matching storage.py's
    atomic write pattern so a crash never leaves a partial proof on disk.

    Exclusivity for a brand new proof (never silently overwrite) is enforced
    by the caller checking existence first, in stamp(); this helper is also
    reused by upgrade(), which legitimately replaces the file with a version
    carrying a newer attestation.
    """
    ots_path = Path(ots_path)
    ots_path.parent.mkdir(parents=True, exist_ok=True)
    temp_path = ots_path.with_name(ots_path.name + ".tmp")
    try:
        with open(temp_path, "wb") as fh:
            ctx = StreamSerializationContext(fh)
            detached.serialize(ctx)
        os.replace(temp_path, ots_path)
    finally:
        if temp_path.exists():
            temp_path.unlink()


def _attestations(timestamp: Timestamp) -> set:
    return {a for _, a in timestamp.all_attestations()}


def _status_of(detached: DetachedTimestampFile) -> str:
    attestations = _attestations(detached.timestamp)
    if any(isinstance(a, BitcoinBlockHeaderAttestation) for a in attestations):
        return STATUS_CONFIRMED
    if any(isinstance(a, PendingAttestation) for a in attestations):
        return STATUS_PENDING
    # A well-formed proof from stamp() always carries at least one pending
    # attestation. Anything else means there is nothing here worth trusting.
    return STATUS_MISSING


def _walk(stamp: Timestamp):
    yield stamp
    for sub_stamp in stamp.ops.values():
        yield from _walk(sub_stamp)


def stamp(
    path: Path,
    *,
    calendar_urls: tuple[str, ...] = DEFAULT_CALENDAR_URLS,
    timeout: float = DEFAULT_TIMEOUT_SECONDS,
) -> Path:
    """Create an OpenTimestamps proof for `path` against live calendar servers.

    Hashes the file with SHA-256, submits that digest to each calendar in
    `calendar_urls` (a real network call to each), and merges every
    successful response into one Timestamp. Writes `<path>.ots`. Refuses to
    overwrite an existing proof (see ProofExistsError): completing a proof
    over time is what `upgrade()` is for.

    Every calendar response at this point is a PendingAttestation. No
    calendar server issues a Bitcoin attestation synchronously; that is
    exactly the subtlety this module exists to keep visible. See
    `proof_status()`.
    """
    path = Path(path)
    if not path.exists():
        raise FileNotFoundError(f"cannot stamp {path}: it does not exist")

    ots_path = _ots_path_for(path)
    if ots_path.exists():
        raise ProofExistsError(
            f"{ots_path} already exists. stamp() never overwrites a proof; "
            f"call eq.anchor.upgrade({ots_path!r}) to complete a pending one."
        )

    digest = hashlib.sha256(path.read_bytes()).digest()
    file_timestamp = Timestamp(digest)

    errors: list[str] = []
    successes = 0
    for url in calendar_urls:
        try:
            remote = RemoteCalendar(url, user_agent="eq-anchor")
            result = remote.submit(digest, timeout=timeout)
            file_timestamp.merge(result)
            successes += 1
        except Exception as exc:  # noqa: BLE001 - any calendar can fail independently
            errors.append(f"{url}: {exc!r}")

    if successes == 0:
        raise OpenTimestampsSubmissionError(
            "no calendar server accepted the digest for "
            f"{path}: {'; '.join(errors) if errors else 'no calendars configured'}"
        )

    detached = DetachedTimestampFile(OpSHA256(), file_timestamp)
    _write_ots(detached, ots_path)
    return ots_path


def proof_status(ots_path: Path) -> str:
    """One of "pending", "confirmed", "missing".

    Reads only the local .ots file; makes no network call. "confirmed"
    requires a BitcoinBlockHeaderAttestation to already be present in that
    local file, which only `upgrade()` (or a fresh `stamp()` on an already
    long-pending digest, which does not happen here) can put there. A proof
    fresh out of `stamp()` is always "pending", never "confirmed": see the
    module docstring.
    """
    ots_path = Path(ots_path)
    if not ots_path.exists():
        return STATUS_MISSING
    detached = _load_ots(ots_path)
    return _status_of(detached)


def upgrade(
    ots_path: Path,
    *,
    calendar_urls: tuple[str, ...] | None = None,
    timeout: float = DEFAULT_TIMEOUT_SECONDS,
) -> dict:
    """Ask each calendar named in a pending proof whether it has completed yet.

    This is the upgrade path the module docstring promises: calendar servers
    batch pending digests into a Bitcoin transaction and only attest once
    that transaction has enough confirmations, typically hours after
    `stamp()` ran. Calling this before that has happened is an ordinary,
    expected no-op, not a failure. Rewrites the .ots file in place, atomically,
    only if a new attestation was actually found.

    `calendar_urls`, if given, overrides the calendar named in each pending
    attestation's own URI, matching `ots upgrade -c`. Left as None, each
    pending attestation is checked against the calendar it names.
    """
    ots_path = Path(ots_path)
    detached = _load_ots(ots_path)

    existing = _attestations(detached.timestamp)
    changed = False
    checked: list[str] = []
    errors: list[str] = []

    for sub_stamp in _walk(detached.timestamp):
        for attestation in list(sub_stamp.attestations):
            if not isinstance(attestation, PendingAttestation):
                continue
            urls = calendar_urls or (attestation.uri,)
            checked.append(attestation.uri)
            for url in urls:
                try:
                    remote = RemoteCalendar(url, user_agent="eq-anchor")
                    upgraded_stamp = remote.get_timestamp(sub_stamp.msg, timeout=timeout)
                except CommitmentNotFoundError:
                    # Normal while still pending: the calendar has not yet
                    # folded this digest into a Bitcoin transaction.
                    continue
                except Exception as exc:  # noqa: BLE001
                    errors.append(f"{url}: {exc!r}")
                    continue

                new_attestations = _attestations(upgraded_stamp) - existing
                if new_attestations:
                    sub_stamp.merge(upgraded_stamp)
                    existing.update(new_attestations)
                    changed = True

    if changed:
        _write_ots(detached, ots_path)

    return {
        "changed": changed,
        "status": _status_of(detached),
        "checked_calendars": sorted(set(checked)),
        "errors": errors,
        "checked_at_utc": _now_utc_iso(),
    }


def verify(path: Path, ots_path: Path) -> dict:
    """Recompute `path`'s digest and check it against the proof at `ots_path`.

    Detects tampering: if the file's current bytes do not hash to the digest
    the proof was issued for, `file_matches_proof` is False and `status` is
    "tampered", regardless of what the proof's own attestations say, because
    a proof that does not match the file it is supposedly for cannot vouch
    for that file's current contents.

    When the file does match, `status` is whatever `proof_status()` would
    return ("pending" or "confirmed"), and `bitcoin_anchored` is True only
    when that status is "confirmed". A pending proof is never reported as
    Bitcoin anchored here, matching every other surface in this module.

    This function makes no network call: it is a check against what has
    already been recorded locally. Call `upgrade()` first if the intent is
    to pull in a newer attestation before verifying.
    """
    path = Path(path)
    ots_path = Path(ots_path)

    if not ots_path.exists():
        return {
            "file_matches_proof": False,
            "status": STATUS_MISSING,
            "bitcoin_anchored": False,
            "reason": f"{ots_path} does not exist; nothing to verify against.",
        }

    detached = _load_ots(ots_path)

    if not path.exists():
        return {
            "file_matches_proof": False,
            "status": _status_of(detached),
            "bitcoin_anchored": False,
            "reason": f"{path} does not exist; cannot recompute its digest.",
        }

    actual_digest = hashlib.sha256(path.read_bytes()).digest()
    proof_digest = detached.file_digest
    matches = actual_digest == proof_digest

    if not matches:
        return {
            "file_matches_proof": False,
            "actual_sha256": actual_digest.hex(),
            "proof_sha256": proof_digest.hex(),
            "status": "tampered",
            "bitcoin_anchored": False,
            "reason": (
                "the file's current bytes do not hash to the digest this proof "
                "was issued for. Either the file changed after stamping, or "
                "this is the wrong proof for this file. This proof cannot "
                "attest to the current contents of the file."
            ),
        }

    status = _status_of(detached)
    return {
        "file_matches_proof": True,
        "actual_sha256": actual_digest.hex(),
        "proof_sha256": proof_digest.hex(),
        "status": status,
        "bitcoin_anchored": status == STATUS_CONFIRMED,
        "reason": _PROOF_NOTES[status],
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
    and all three D10 anchors with their individual status: the commit
    (always present, always noted as the weakest), the GitHub Actions run
    (present with detail, or explicitly and distinguishably absent), and the
    OpenTimestamps proof at `<path>.ots` (missing if `stamp()` has not run
    yet, pending if it has but has not yet upgraded, confirmed only once a
    Bitcoin attestation is locally present).

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

    ots_path = _ots_path_for(path)
    ots_status = proof_status(ots_path)

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
            "opentimestamps": {
                "proof_path": _repo_relative(ots_path),
                "status": ots_status,
                "bitcoin_anchored": ots_status == STATUS_CONFIRMED,
                "last_checked_utc": _now_utc_iso(),
                "note": _PROOF_NOTES[ots_status],
            },
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
