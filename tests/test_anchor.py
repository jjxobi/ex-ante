"""Tests for eq.anchor: the D10 provenance manifest and the OpenTimestamps proof.

Everything here is hermetic. Calendar servers are mocked by monkeypatching
anchor.RemoteCalendar with a small fake, so the suite never depends on
network reachability, external service uptime, or clock time (a real Bitcoin
attestation is normally hours away from a fresh proof, which this suite
cannot wait for and does not need to: the fake calendar can hand back a
BitcoinBlockHeaderAttestation on demand to exercise the "confirmed" path).
A real, live-network stamp against the actual calendar servers is exercised
separately by scripts/live_ots_stamp.py, deliberately kept out of the test
suite per the instruction that a live-network check belongs in scripts/, not
in a test that has to run on every commit.

The single property this file exists to pin down: a proof that has not been
upgraded with a real Bitcoin attestation must never be reported, anywhere,
as Bitcoin anchored. Every "pending" test below checks that explicitly rather
than trusting the "confirmed" tests to imply it.
"""

from __future__ import annotations

import hashlib
import json
import subprocess
from datetime import datetime, timezone
from pathlib import Path

import pytest

from opentimestamps.core.notary import BitcoinBlockHeaderAttestation, PendingAttestation
from opentimestamps.core.op import OpSHA256
from opentimestamps.core.timestamp import DetachedTimestampFile, Timestamp

from eq import anchor, paths


# --------------------------------------------------------------------------
# Fakes standing in for live calendar servers.
# --------------------------------------------------------------------------

class _FakeCalendarPending:
    """Mimics a calendar's immediate response to a fresh submission: a
    Timestamp carrying only a PendingAttestation. This is what every real
    calendar returns synchronously; nothing returns a Bitcoin attestation on
    the spot."""

    def __init__(self, url, user_agent=None):
        self.url = url

    def submit(self, digest, timeout=None):
        ts = Timestamp(digest)
        ts.attestations.add(PendingAttestation(self.url))
        return ts

    def get_timestamp(self, commitment, timeout=None):
        from opentimestamps.calendar import CommitmentNotFoundError

        raise CommitmentNotFoundError("still pending, nothing to upgrade to yet")


class _FakeCalendarConfirms:
    """A calendar whose get_timestamp() has completed the Bitcoin upgrade."""

    def __init__(self, url, user_agent=None):
        self.url = url

    def submit(self, digest, timeout=None):
        ts = Timestamp(digest)
        ts.attestations.add(PendingAttestation(self.url))
        return ts

    def get_timestamp(self, commitment, timeout=None):
        ts = Timestamp(commitment)
        ts.attestations.add(BitcoinBlockHeaderAttestation(900_000))
        return ts


class _FakeCalendarAlwaysFails:
    def __init__(self, url, user_agent=None):
        self.url = url

    def submit(self, digest, timeout=None):
        raise ConnectionError(f"could not reach {self.url}")

    def get_timestamp(self, commitment, timeout=None):
        raise ConnectionError(f"could not reach {self.url}")


def _mock_pending_calendar(monkeypatch):
    monkeypatch.setattr(anchor, "RemoteCalendar", _FakeCalendarPending)


def _mock_confirming_calendar(monkeypatch):
    monkeypatch.setattr(anchor, "RemoteCalendar", _FakeCalendarConfirms)


def _mock_failing_calendar(monkeypatch):
    monkeypatch.setattr(anchor, "RemoteCalendar", _FakeCalendarAlwaysFails)


def _write_confirmed_proof(target: Path, ots_path: Path, height: int = 900_000) -> None:
    """Build a proof carrying a real BitcoinBlockHeaderAttestation directly,
    without going through upgrade(), for tests that only care about the
    "confirmed" branch of proof_status()/verify()."""
    digest = hashlib.sha256(target.read_bytes()).digest()
    ts = Timestamp(digest)
    ts.attestations.add(BitcoinBlockHeaderAttestation(height))
    detached = DetachedTimestampFile(OpSHA256(), ts)
    anchor._write_ots(detached, ots_path)


# --------------------------------------------------------------------------
# manifest_for: hashing, anchors, and the missing-CI-anchor case
# --------------------------------------------------------------------------

def test_manifest_sha256_matches_actual_file_bytes(tmp_path):
    target = tmp_path / "forecast.parquet"
    target.write_bytes(b"some deterministic forecast bytes\x00\x01\x02")

    manifest = anchor.manifest_for(
        target,
        window_start=datetime(2026, 8, 10, tzinfo=timezone.utc),
        window_end=datetime(2026, 8, 17, tzinfo=timezone.utc),
        model="baseline-ti-smoothed",
        stratum="shallow",
    )

    expected = hashlib.sha256(target.read_bytes()).hexdigest()
    assert manifest["sha256"] == expected
    assert len(manifest["sha256"]) == 64


def test_manifest_records_window_model_and_stratum(tmp_path):
    target = tmp_path / "forecast.parquet"
    target.write_bytes(b"payload")

    manifest = anchor.manifest_for(
        target,
        window_start=datetime(2026, 8, 10, tzinfo=timezone.utc),
        window_end=datetime(2026, 8, 17, tzinfo=timezone.utc),
        model="baseline-ti-smoothed",
        stratum="deep",
    )

    assert manifest["window_start_utc"] == "2026-08-10T00:00:00Z"
    assert manifest["window_end_utc"] == "2026-08-17T00:00:00Z"
    assert manifest["model"] == "baseline-ti-smoothed"
    assert manifest["stratum"] == "deep"
    assert manifest["published_at_utc"].endswith("Z")


def test_manifest_rejects_offset_naive_window_bound(tmp_path):
    target = tmp_path / "forecast.parquet"
    target.write_bytes(b"payload")

    with pytest.raises(ValueError):
        anchor.manifest_for(
            target,
            window_start=datetime(2026, 8, 10),  # naive, per D12 this is refused
            window_end=datetime(2026, 8, 17, tzinfo=timezone.utc),
            model="baseline",
            stratum="shallow",
        )


def test_manifest_rejects_window_end_before_start(tmp_path):
    target = tmp_path / "forecast.parquet"
    target.write_bytes(b"payload")

    with pytest.raises(ValueError):
        anchor.manifest_for(
            target,
            window_start=datetime(2026, 8, 17, tzinfo=timezone.utc),
            window_end=datetime(2026, 8, 10, tzinfo=timezone.utc),
            model="baseline",
            stratum="shallow",
        )


def test_manifest_refuses_a_file_that_does_not_exist(tmp_path):
    with pytest.raises(FileNotFoundError):
        anchor.manifest_for(
            tmp_path / "never_written.parquet",
            window_start=datetime(2026, 8, 10, tzinfo=timezone.utc),
            window_end=datetime(2026, 8, 17, tzinfo=timezone.utc),
            model="baseline",
            stratum="shallow",
        )


def test_manifest_commit_anchor_is_present_and_marked_weakest(tmp_path):
    target = tmp_path / "forecast.parquet"
    target.write_bytes(b"payload")

    manifest = anchor.manifest_for(
        target,
        window_start=datetime(2026, 8, 10, tzinfo=timezone.utc),
        window_end=datetime(2026, 8, 17, tzinfo=timezone.utc),
        model="baseline",
        stratum="shallow",
    )

    expected_sha = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=paths.REPO_ROOT,
        capture_output=True,
        text=True,
        check=True,
    ).stdout.strip()

    commit = manifest["anchors"]["commit"]
    assert commit["sha"] == expected_sha
    assert "weakest" in commit["note"]


def test_manifest_ci_anchor_is_explicitly_absent_without_github_run_id(monkeypatch, tmp_path):
    monkeypatch.delenv("GITHUB_RUN_ID", raising=False)
    target = tmp_path / "forecast.parquet"
    target.write_bytes(b"payload")

    manifest = anchor.manifest_for(
        target,
        window_start=datetime(2026, 8, 10, tzinfo=timezone.utc),
        window_end=datetime(2026, 8, 17, tzinfo=timezone.utc),
        model="baseline",
        stratum="shallow",
    )

    ci = manifest["anchors"]["ci_run"]
    # The distinguishing property under test: absence is a typed, actionable
    # field, not a bare null a renderer could mistake for "not yet filled in".
    assert ci["present"] is False
    assert ci["run_id"] is None
    assert "no GITHUB_RUN_ID" in ci["note"]


def test_manifest_ci_anchor_present_with_full_detail_under_github_actions(monkeypatch, tmp_path):
    monkeypatch.setenv("GITHUB_RUN_ID", "4242424242")
    monkeypatch.setenv("GITHUB_RUN_ATTEMPT", "2")
    monkeypatch.setenv("GITHUB_WORKFLOW", "publish-forecast")
    monkeypatch.setenv("GITHUB_REPOSITORY", "jesseobrien/EQ-Project")

    target = tmp_path / "forecast.parquet"
    target.write_bytes(b"payload")

    manifest = anchor.manifest_for(
        target,
        window_start=datetime(2026, 8, 10, tzinfo=timezone.utc),
        window_end=datetime(2026, 8, 17, tzinfo=timezone.utc),
        model="baseline",
        stratum="shallow",
    )

    ci = manifest["anchors"]["ci_run"]
    assert ci["present"] is True
    assert ci["run_id"] == "4242424242"
    assert ci["run_attempt"] == "2"
    assert ci["workflow"] == "publish-forecast"
    assert ci["repository"] == "jesseobrien/EQ-Project"
    assert ci["run_url"] == "https://github.com/jesseobrien/EQ-Project/actions/runs/4242424242"


def test_manifest_opentimestamps_anchor_is_missing_before_stamp(tmp_path):
    target = tmp_path / "forecast.parquet"
    target.write_bytes(b"payload")

    manifest = anchor.manifest_for(
        target,
        window_start=datetime(2026, 8, 10, tzinfo=timezone.utc),
        window_end=datetime(2026, 8, 17, tzinfo=timezone.utc),
        model="baseline",
        stratum="shallow",
    )

    ots = manifest["anchors"]["opentimestamps"]
    assert ots["status"] == "missing"
    assert ots["bitcoin_anchored"] is False


def test_manifest_opentimestamps_anchor_pending_after_stamp_never_says_confirmed(monkeypatch, tmp_path):
    _mock_pending_calendar(monkeypatch)
    target = tmp_path / "forecast.parquet"
    target.write_bytes(b"payload")
    anchor.stamp(target)

    manifest = anchor.manifest_for(
        target,
        window_start=datetime(2026, 8, 10, tzinfo=timezone.utc),
        window_end=datetime(2026, 8, 17, tzinfo=timezone.utc),
        model="baseline",
        stratum="shallow",
    )

    ots = manifest["anchors"]["opentimestamps"]
    assert ots["status"] == "pending"
    assert ots["bitcoin_anchored"] is False
    assert "NOT Bitcoin anchored" in ots["note"]
    assert "Bitcoin anchored:" not in ots["note"]


# --------------------------------------------------------------------------
# write_manifest
# --------------------------------------------------------------------------

def test_write_manifest_round_trips(tmp_path):
    manifest = {"sha256": "abc123", "anchors": {"commit": {"sha": "deadbeef"}}}
    destination = tmp_path / "sub" / "manifest.json"

    written = anchor.write_manifest(manifest, destination)

    assert written == destination
    assert json.loads(destination.read_text(encoding="utf-8")) == manifest


def test_write_manifest_leaves_no_temp_file(tmp_path):
    destination = tmp_path / "manifest.json"
    anchor.write_manifest({"a": 1}, destination)
    assert [p.name for p in tmp_path.iterdir()] == ["manifest.json"]


# --------------------------------------------------------------------------
# stamp(): proof creation (mocked calendars)
# --------------------------------------------------------------------------

def test_stamp_creates_a_proof_file_next_to_the_target(monkeypatch, tmp_path):
    _mock_pending_calendar(monkeypatch)
    target = tmp_path / "forecast.parquet"
    target.write_bytes(b"payload")

    ots_path = anchor.stamp(target)

    assert ots_path == tmp_path / "forecast.parquet.ots"
    assert ots_path.exists()
    assert ots_path.stat().st_size > 0


def test_stamp_refuses_to_overwrite_an_existing_proof(monkeypatch, tmp_path):
    _mock_pending_calendar(monkeypatch)
    target = tmp_path / "forecast.parquet"
    target.write_bytes(b"payload")
    anchor.stamp(target)

    with pytest.raises(anchor.ProofExistsError):
        anchor.stamp(target)


def test_stamp_raises_if_every_calendar_fails(monkeypatch, tmp_path):
    _mock_failing_calendar(monkeypatch)
    target = tmp_path / "forecast.parquet"
    target.write_bytes(b"payload")

    with pytest.raises(anchor.OpenTimestampsSubmissionError):
        anchor.stamp(target)

    assert not (tmp_path / "forecast.parquet.ots").exists()


def test_stamp_refuses_a_target_that_does_not_exist(monkeypatch, tmp_path):
    _mock_pending_calendar(monkeypatch)
    with pytest.raises(FileNotFoundError):
        anchor.stamp(tmp_path / "nope.parquet")


# --------------------------------------------------------------------------
# proof_status(): pending vs confirmed vs missing
# --------------------------------------------------------------------------

def test_proof_status_missing_when_no_ots_file(tmp_path):
    assert anchor.proof_status(tmp_path / "nothing.ots") == "missing"


def test_proof_status_pending_for_a_freshly_created_proof(monkeypatch, tmp_path):
    """The central regression this module exists to prevent: a proof that was
    just created must read as pending, never confirmed, because no calendar
    server issues a Bitcoin attestation synchronously."""
    _mock_pending_calendar(monkeypatch)
    target = tmp_path / "forecast.parquet"
    target.write_bytes(b"payload")
    ots_path = anchor.stamp(target)

    assert anchor.proof_status(ots_path) == "pending"
    assert anchor.proof_status(ots_path) != "confirmed"


def test_proof_status_confirmed_requires_a_bitcoin_attestation(tmp_path):
    target = tmp_path / "forecast.parquet"
    target.write_bytes(b"payload")
    ots_path = tmp_path / "forecast.parquet.ots"
    _write_confirmed_proof(target, ots_path)

    assert anchor.proof_status(ots_path) == "confirmed"


# --------------------------------------------------------------------------
# verify(): tamper detection and the pending/confirmed distinction
# --------------------------------------------------------------------------

def test_verify_succeeds_against_an_untampered_file(monkeypatch, tmp_path):
    _mock_pending_calendar(monkeypatch)
    target = tmp_path / "forecast.parquet"
    target.write_bytes(b"original bytes")
    ots_path = anchor.stamp(target)

    result = anchor.verify(target, ots_path)

    assert result["file_matches_proof"] is True
    assert result["status"] == "pending"
    assert result["bitcoin_anchored"] is False


def test_verify_detects_a_single_byte_tamper(monkeypatch, tmp_path):
    _mock_pending_calendar(monkeypatch)
    target = tmp_path / "forecast.parquet"
    target.write_bytes(b"original bytes, thirty two long")
    ots_path = anchor.stamp(target)

    tampered = bytearray(target.read_bytes())
    tampered[0] ^= 0xFF
    target.write_bytes(bytes(tampered))

    result = anchor.verify(target, ots_path)

    assert result["file_matches_proof"] is False
    assert result["status"] == "tampered"
    assert result["bitcoin_anchored"] is False
    assert result["actual_sha256"] != result["proof_sha256"]


def test_verify_reports_pending_proof_as_not_bitcoin_anchored(monkeypatch, tmp_path):
    _mock_pending_calendar(monkeypatch)
    target = tmp_path / "forecast.parquet"
    target.write_bytes(b"payload")
    ots_path = anchor.stamp(target)

    result = anchor.verify(target, ots_path)

    assert result["status"] != "confirmed"
    assert result["bitcoin_anchored"] is False
    assert "Bitcoin anchored:" not in result["reason"]


def test_verify_reports_confirmed_only_with_a_real_bitcoin_attestation(tmp_path):
    target = tmp_path / "forecast.parquet"
    target.write_bytes(b"payload")
    ots_path = tmp_path / "forecast.parquet.ots"
    _write_confirmed_proof(target, ots_path)

    result = anchor.verify(target, ots_path)

    assert result["file_matches_proof"] is True
    assert result["status"] == "confirmed"
    assert result["bitcoin_anchored"] is True


def test_verify_missing_proof_reports_missing_not_tampered(tmp_path):
    target = tmp_path / "forecast.parquet"
    target.write_bytes(b"payload")

    result = anchor.verify(target, tmp_path / "forecast.parquet.ots")

    assert result["file_matches_proof"] is False
    assert result["status"] == "missing"
    assert result["bitcoin_anchored"] is False


# --------------------------------------------------------------------------
# upgrade(): the path that later completes a pending proof
# --------------------------------------------------------------------------

def test_upgrade_is_a_no_op_while_still_pending(monkeypatch, tmp_path):
    _mock_pending_calendar(monkeypatch)
    target = tmp_path / "forecast.parquet"
    target.write_bytes(b"payload")
    ots_path = anchor.stamp(target)

    result = anchor.upgrade(ots_path)

    assert result["changed"] is False
    assert result["status"] == "pending"
    assert anchor.proof_status(ots_path) == "pending"


def test_upgrade_completes_a_proof_once_the_calendar_has_confirmed(monkeypatch, tmp_path):
    _mock_pending_calendar(monkeypatch)
    target = tmp_path / "forecast.parquet"
    target.write_bytes(b"payload")
    ots_path = anchor.stamp(target)
    assert anchor.proof_status(ots_path) == "pending"

    _mock_confirming_calendar(monkeypatch)
    result = anchor.upgrade(ots_path)

    assert result["changed"] is True
    assert result["status"] == "confirmed"
    # Re-read from disk: the upgrade must have been persisted, not just held
    # in memory for this call's return value.
    assert anchor.proof_status(ots_path) == "confirmed"


# --------------------------------------------------------------------------
# The wording contract itself: pending is never described as Bitcoin
# anchored anywhere this module produces text.
# --------------------------------------------------------------------------

def test_pending_note_never_claims_bitcoin_anchoring():
    pending_note = anchor._PROOF_NOTES[anchor.STATUS_PENDING]
    assert "NOT Bitcoin anchored" in pending_note
    assert "Bitcoin anchored:" not in pending_note


def test_missing_note_never_claims_bitcoin_anchoring():
    missing_note = anchor._PROOF_NOTES[anchor.STATUS_MISSING]
    assert "Bitcoin anchored" not in missing_note


def test_confirmed_note_is_the_only_one_asserting_bitcoin_anchoring():
    confirmed_note = anchor._PROOF_NOTES[anchor.STATUS_CONFIRMED]
    assert confirmed_note.startswith("Bitcoin anchored:")
