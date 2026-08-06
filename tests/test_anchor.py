"""Tests for eq.anchor: the D10 provenance manifest and hash-based tamper detection.

DECISIONS.md D10 originally recorded three anchors: the commit, the GitHub
Actions run ID, and a Bitcoin-anchored proof over the forecast file. The
third anchor was removed; the reasoning is recorded in D10 itself, in the
same place D3 and D4a record rejected approaches. This suite now covers what
remains: the commit anchor, the CI anchor, and the SHA-256 based tamper
check, which never depended on the third anchor and is unaffected by its
removal.
"""

from __future__ import annotations

import json
import subprocess
from datetime import datetime, timezone

import pytest

from eq import anchor, paths


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

    expected = anchor.sha256_file(target)
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


def test_manifest_commit_anchor_is_present_and_marked_weaker(tmp_path):
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
    assert "weaker" in commit["note"]


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


def test_manifest_anchors_contain_only_commit_and_ci_run(tmp_path):
    """The third D10 anchor is gone. This pins the manifest shape so a
    reintroduction (accidental or otherwise) fails a test rather than
    drifting back in silently."""
    target = tmp_path / "forecast.parquet"
    target.write_bytes(b"payload")

    manifest = anchor.manifest_for(
        target,
        window_start=datetime(2026, 8, 10, tzinfo=timezone.utc),
        window_end=datetime(2026, 8, 17, tzinfo=timezone.utc),
        model="baseline",
        stratum="shallow",
    )

    assert set(manifest["anchors"].keys()) == {"commit", "ci_run"}


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
# sha256_bytes / sha256_file
# --------------------------------------------------------------------------

def test_sha256_bytes_matches_hashlib():
    import hashlib

    data = b"some bytes to hash"
    assert anchor.sha256_bytes(data) == hashlib.sha256(data).hexdigest()


def test_sha256_file_reads_fresh_from_disk_every_call(tmp_path):
    target = tmp_path / "forecast.parquet"
    target.write_bytes(b"version one")
    first = anchor.sha256_file(target)

    target.write_bytes(b"version two")
    second = anchor.sha256_file(target)

    assert first != second
    assert second == anchor.sha256_file(target)


# --------------------------------------------------------------------------
# verify(): tamper detection by SHA-256 comparison, independent of any
# external anchor. This is what survives the removal of the third D10 anchor.
# --------------------------------------------------------------------------

def test_verify_succeeds_against_an_untampered_file(tmp_path):
    target = tmp_path / "forecast.parquet"
    target.write_bytes(b"original bytes")
    recorded_sha256 = anchor.sha256_file(target)

    result = anchor.verify(target, recorded_sha256)

    assert result["file_matches"] is True
    assert result["status"] == "ok"
    assert result["actual_sha256"] == recorded_sha256


def test_verify_detects_a_single_byte_tamper(tmp_path):
    target = tmp_path / "forecast.parquet"
    target.write_bytes(b"original bytes, thirty two long")
    recorded_sha256 = anchor.sha256_file(target)

    tampered = bytearray(target.read_bytes())
    tampered[0] ^= 0xFF
    target.write_bytes(bytes(tampered))

    result = anchor.verify(target, recorded_sha256)

    assert result["file_matches"] is False
    assert result["status"] == "tampered"
    assert result["actual_sha256"] != result["expected_sha256"]


def test_verify_missing_file_reports_missing_not_tampered(tmp_path):
    target = tmp_path / "never_written.parquet"

    result = anchor.verify(target, "0" * 64)

    assert result["file_matches"] is False
    assert result["status"] == "missing"


def test_verify_round_trips_through_a_real_manifest(tmp_path):
    """The intended usage: record sha256 via manifest_for at publish time,
    verify against it later."""
    target = tmp_path / "forecast.parquet"
    target.write_bytes(b"a real forecast payload")

    manifest = anchor.manifest_for(
        target,
        window_start=datetime(2026, 8, 10, tzinfo=timezone.utc),
        window_end=datetime(2026, 8, 17, tzinfo=timezone.utc),
        model="baseline",
        stratum="shallow",
    )

    result = anchor.verify(target, manifest["sha256"])
    assert result["status"] == "ok"

    target.write_bytes(b"a real forecast payload, tampered")
    result = anchor.verify(target, manifest["sha256"])
    assert result["status"] == "tampered"


# --------------------------------------------------------------------------
# The third anchor's surface is gone, not merely unused.
# --------------------------------------------------------------------------

def test_no_third_anchor_surface_remains_on_the_module():
    removed = {
        "stamp",
        "proof_status",
        "upgrade",
        "STATUS_PENDING",
        "STATUS_CONFIRMED",
        "ProofExistsError",
        "CorruptProofError",
        "DEFAULT_CALENDAR_URLS",
        "PROOF_SUFFIX",
    }
    present = removed & set(dir(anchor))
    assert not present, f"removed anchor surface still present: {present}"
