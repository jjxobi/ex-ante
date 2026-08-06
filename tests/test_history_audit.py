"""Every check in the history audit, proven able to fail.

An auditor is worth exactly what its checks can catch, and a check that cannot
fire is indistinguishable from one that passes. This project has been bitten by
that shape repeatedly: a scorer that returned a perfect quantile on empty
windows, a skip interlock that protected only one side, a freshness test that
passed more easily the more it was broken.

So each test here builds a small git repository containing a specific forged
history, runs the real audit against it, and asserts the corresponding check
reports a violation. The last test builds an honest history and asserts silence,
which is the control: without it, a check that fired on everything would look
identical to a check that works.

Timestamps are forged through GIT_AUTHOR_DATE and GIT_COMMITTER_DATE, which is
precisely the capability D10 has in mind when it calls the commit timestamp the
weaker of the two anchors. Being able to forge it here is the point.
"""

from __future__ import annotations

import hashlib
import json
import subprocess
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

import audit_history  # noqa: E402

GRID = "14b2e0b854b5ae89771ad3346204e801f1f32580fd9a09481b9b6f6fe9cd4e44"
BOT = audit_history.BOT_AUTHOR
WINDOW_START = "2026-03-01T00:00:00Z"
WINDOW_END = "2026-03-02T00:00:00Z"
FORECAST = "forecasts/baseline/daily/shallow/2026-03-01.json"
MANIFEST = "forecasts/baseline/daily/shallow/2026-03-01.manifest.json"

BEFORE_WINDOW = "2026-02-27T00:00:00Z"
AFTER_WINDOW = "2026-03-05T00:00:00Z"


def run(repo: Path, *args: str, when: str | None = None, author: str = BOT) -> None:
    env = None
    if when is not None:
        import os

        env = os.environ | {
            "GIT_AUTHOR_DATE": when,
            "GIT_COMMITTER_DATE": when,
            "GIT_AUTHOR_NAME": author,
            "GIT_COMMITTER_NAME": author,
            "GIT_AUTHOR_EMAIL": "bot@example.com",
            "GIT_COMMITTER_EMAIL": "bot@example.com",
        }
    subprocess.run(
        ["git", *args], cwd=repo, check=True, capture_output=True, text=True, env=env
    )


def forecast_body(window_start: str = WINDOW_START) -> str:
    return json.dumps(
        {
            "model": "baseline",
            "horizon": "daily",
            "stratum": "shallow",
            "window_start_utc": window_start,
            "window_end_utc": WINDOW_END,
            "grid_hash": GRID,
            "b": 0.867,
            "rates": {"1": 0.01},
        },
        indent=2,
        sort_keys=True,
    ) + "\n"


def manifest_body(
    content: str,
    *,
    window_start: str = WINDOW_START,
    grid: str = GRID,
    sha: str | None = None,
    file_name: str = FORECAST,
) -> str:
    return json.dumps(
        {
            "file": file_name,
            "sha256": sha if sha is not None else hashlib.sha256(content.encode()).hexdigest(),
            "window_start_utc": window_start,
            "window_end_utc": WINDOW_END,
            "model": "baseline",
            "horizon": "daily",
            "stratum": "shallow",
            "published_at_utc": BEFORE_WINDOW,
            "grid_hash": grid,
            "anchors": {"ci_run": {"present": True, "run_id": "1", "repository": "x/y"}},
        },
        indent=2,
        sort_keys=True,
    ) + "\n"


def write(repo: Path, path: str, content: str) -> None:
    target = repo / path
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(content, encoding="utf-8", newline="")


@pytest.fixture
def repo(tmp_path, monkeypatch):
    """A git repository the audit will treat as the record."""
    run(tmp_path, "init", "-q")
    run(tmp_path, "config", "user.name", BOT)
    run(tmp_path, "config", "user.email", "bot@example.com")
    monkeypatch.setattr(audit_history, "REPO_ROOT", tmp_path)
    return tmp_path


def publish_cleanly(repo: Path, *, when: str = BEFORE_WINDOW, author: str = BOT) -> str:
    """One honest publication: forecast and matching manifest, before the window."""
    content = forecast_body()
    write(repo, FORECAST, content)
    write(repo, MANIFEST, manifest_body(content))
    run(repo, "add", "-A")
    run(repo, "commit", "-q", "-m", "publish", when=when, author=author)
    return content


def violations(online: bool = False) -> list[audit_history.Finding]:
    return [f for f in audit_history.audit(online=online) if f.severity == audit_history.VIOLATION]


def checks_that_fired(findings) -> set[str]:
    return {f.check for f in findings}


# --------------------------------------------------------------------------
# The control: an honest record must produce silence
# --------------------------------------------------------------------------


def test_an_honest_history_reports_no_violations(repo):
    publish_cleanly(repo)
    assert violations() == []


# --------------------------------------------------------------------------
# Each check, proven able to fire
# --------------------------------------------------------------------------


def test_a_forecast_modified_after_its_window_opened_is_caught(repo):
    """The load-bearing check. Publishing honestly and then editing later is
    the exact move the project claims to have made impossible."""
    publish_cleanly(repo)
    tampered = forecast_body().replace('"b": 0.867', '"b": 0.999')
    write(repo, FORECAST, tampered)
    write(repo, MANIFEST, manifest_body(tampered))
    run(repo, "add", "-A")
    run(repo, "commit", "-q", "-m", "quietly improve the forecast", when=AFTER_WINDOW)

    fired = checks_that_fired(violations())
    assert "no-post-window-modification" in fired


def test_a_forecast_first_committed_after_its_window_is_caught(repo):
    """Rule 1: a forecast that never existed before the window it describes."""
    publish_cleanly(repo, when=AFTER_WINDOW)

    fired = checks_that_fired(violations())
    assert "published-before-window" in fired
    assert "no-post-window-modification" in fired


def test_a_manifest_recording_the_wrong_hash_is_caught(repo):
    content = forecast_body()
    write(repo, FORECAST, content)
    write(repo, MANIFEST, manifest_body(content, sha="0" * 64))
    run(repo, "add", "-A")
    run(repo, "commit", "-q", "-m", "publish", when=BEFORE_WINDOW)

    found = [f for f in violations() if f.check == "manifest-hash"]
    assert found, "a manifest whose checksum does not match its file must fail"
    assert "0000" in found[0].detail


def test_content_changed_without_updating_the_manifest_is_caught(repo):
    """The subtler version: the pair was consistent once, then drifted."""
    publish_cleanly(repo)
    write(repo, FORECAST, forecast_body().replace('"b": 0.867', '"b": 0.500'))
    run(repo, "add", "-A")
    run(repo, "commit", "-q", "-m", "edit before the window", when="2026-02-28T00:00:00Z")

    assert "manifest-hash" in checks_that_fired(violations())


def test_a_manifest_disagreeing_with_its_forecast_about_the_window_is_caught(repo):
    content = forecast_body()
    write(repo, FORECAST, content)
    write(repo, MANIFEST, manifest_body(content, window_start="2026-04-01T00:00:00Z"))
    run(repo, "add", "-A")
    run(repo, "commit", "-q", "-m", "publish", when=BEFORE_WINDOW)

    assert "window-agreement" in checks_that_fired(violations())


def test_a_file_named_for_a_different_day_than_its_window_is_caught(repo):
    """The path is a third independent statement of the window, and a record
    whose filenames drift from its contents is unreadable by inspection."""
    content = forecast_body(window_start="2026-03-09T00:00:00Z")
    write(repo, FORECAST, content)
    write(repo, MANIFEST, manifest_body(content, window_start="2026-03-09T00:00:00Z"))
    run(repo, "add", "-A")
    run(repo, "commit", "-q", "-m", "publish", when=BEFORE_WINDOW)

    assert "window-agreement" in checks_that_fired(violations())


def test_a_manifest_naming_a_different_file_is_caught(repo):
    content = forecast_body()
    write(repo, FORECAST, content)
    write(repo, MANIFEST, manifest_body(content, file_name="forecasts/somewhere/else.json"))
    run(repo, "add", "-A")
    run(repo, "commit", "-q", "-m", "publish", when=BEFORE_WINDOW)

    assert "manifest-hash" in checks_that_fired(violations())


def test_a_second_grid_hash_anywhere_in_history_is_caught(repo):
    """D1 freezes one region. A grid that changed and changed back would leave
    HEAD looking consistent, which is why this reads all of history."""
    publish_cleanly(repo)
    content = forecast_body().replace('"b": 0.867', '"b": 0.868')
    write(repo, FORECAST, content)
    write(repo, MANIFEST, manifest_body(content, grid="d" * 64))
    run(repo, "add", "-A")
    run(repo, "commit", "-q", "-m", "refit", when="2026-02-28T00:00:00Z")

    assert "grid-hash-constant" in checks_that_fired(violations())


def test_an_evaluation_catalogue_committed_before_its_freeze_is_caught(repo):
    """D7's 45 days exist so a window cannot be scored against a catalogue seen
    while its events were still revisable."""
    publish_cleanly(repo)
    write(repo, "evaluation/catalogue-2026-03-01-to-2026-03-08.parquet", "not really parquet")
    run(repo, "add", "-A")
    run(repo, "commit", "-q", "-m", "score early", when="2026-03-09T00:00:00Z")

    found = [f for f in violations() if f.check == "evaluation-not-early"]
    assert found, "a catalogue committed 36 days before its freeze must fail"
    assert "2026-04-22" in found[0].detail


def test_an_evaluation_catalogue_committed_on_time_passes(repo):
    """The other side of the same check, so it is not simply always failing."""
    publish_cleanly(repo)
    write(repo, "evaluation/catalogue-2026-03-01-to-2026-03-08.parquet", "not really parquet")
    run(repo, "add", "-A")
    run(repo, "commit", "-q", "-m", "score on time", when="2026-04-23T00:00:00Z")

    assert [f for f in violations() if f.check == "evaluation-not-early"] == []


def test_a_human_authored_forecast_commit_is_reported_as_a_notice(repo):
    """D10 permits a human touch and requires it be visible. Failing the audit
    would create pressure to hide it, so this is a notice, and this test pins
    that distinction so it is not quietly promoted later."""
    publish_cleanly(repo, author="Jesse O'Brien")

    findings = audit_history.audit()
    notices = [f for f in findings if f.check == "commit-authorship"]
    assert notices, "a non-bot commit under forecasts/ must be surfaced"
    assert notices[0].severity == audit_history.NOTICE
    assert violations() == []


def test_a_shallow_clone_is_refused_rather_than_reported_clean(repo, tmp_path, monkeypatch):
    """The most dangerous way this audit could fail is by succeeding.

    actions/checkout fetches depth 1 by default, and against a shallow clone
    every check would iterate over one commit or none, find nothing, and print
    "Clean". A green audit gets read as proof, so one that checked nothing must
    refuse instead of degrading quietly.
    """
    publish_cleanly(repo)
    write(repo, FORECAST, forecast_body().replace('"b": 0.867', '"b": 0.999'))
    run(repo, "add", "-A")
    run(repo, "commit", "-q", "-m", "tamper after the window", when=AFTER_WINDOW)

    # Deep clone: the tampering is visible.
    deep = tmp_path / "deep"
    subprocess.run(
        ["git", "clone", "-q", repo.as_uri(), str(deep)], check=True, capture_output=True
    )
    monkeypatch.setattr(audit_history, "REPO_ROOT", deep)
    assert "no-post-window-modification" in checks_that_fired(violations())

    # Shallow clone: the same tampering is invisible, so the audit must refuse.
    shallow = tmp_path / "shallow"
    subprocess.run(
        ["git", "clone", "-q", "--depth", "1", repo.as_uri(), str(shallow)],
        check=True,
        capture_output=True,
    )
    monkeypatch.setattr(audit_history, "REPO_ROOT", shallow)
    with pytest.raises(audit_history.ShallowCloneError):
        audit_history.audit()


def test_online_without_the_github_cli_refuses_rather_than_skipping(repo, monkeypatch):
    """--online asks for the strong anchor to be verified. If it cannot be,
    saying so beats printing a clean result that checked nothing."""
    publish_cleanly(repo)
    real_run = subprocess.run

    def no_gh(args, *rest, **kwargs):
        if args and args[0] == "gh":
            raise FileNotFoundError(2, "The system cannot find the file specified")
        return real_run(args, *rest, **kwargs)

    monkeypatch.setattr(audit_history.subprocess, "run", no_gh)

    # Offline is unaffected: the refusal is scoped to the check that needs gh.
    assert violations() == []
    with pytest.raises(audit_history.GitHubCliUnavailableError):
        audit_history.audit(online=True)

    assert audit_history.main(["--online"]) == 2, (
        "a run that could not audit must be distinguishable from one that "
        "audited and found violations"
    )


def test_a_forecast_with_no_commit_history_is_caught(repo, monkeypatch):
    """A file in the tree that no commit accounts for would otherwise slip past
    every timing check in silence, since they all iterate over its commits."""
    publish_cleanly(repo)
    real_history = audit_history.history
    monkeypatch.setattr(audit_history, "history", lambda *paths: {})

    fired = checks_that_fired(violations())
    assert "history-present" in fired
    assert real_history is not audit_history.history
