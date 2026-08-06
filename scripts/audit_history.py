"""Audits the published record against git history rather than against itself.

WHY THIS EXISTS SEPARATELY FROM THE PIPELINE. Every integrity claim this
project makes is currently self-reported. A manifest states its own
`published_at_utc`, and D10 already concedes the commit timestamp is "the
weaker of the two anchors" because the committer sets it. A reader who wants
to check the record has to trust the same code that wrote it.

So this script imports nothing from `eq`. Not the path helpers, not the
manifest reader, not the window arithmetic. An auditor built on the module it
audits inherits that module's bugs and confirms them. Everything here is
derived from git and from the JSON on disk, using only the standard library,
so a stranger can read this one file and know what was checked.

WHAT IT CHECKS

  1. No forecast was modified after its window opened. Pre-window overwrites
     are legal refits on fresher data. Post-window edits are the single thing
     this project exists to make impossible, so they are a violation.
  2. Every forecast was introduced by a commit predating its window (Rule 1,
     D11), read from git history rather than from the manifest.
  3. Every manifest's sha256 matches the forecast content as committed, at
     each commit where that content changed.
  4. The window in the manifest, the window in the forecast, and the date in
     the file path all agree.
  5. The grid hash is identical across every manifest ever published (D1).
  6. Forecast commits are authored github-actions[bot]. Human commits are not
     forbidden by D10, they are required to be visible, so these are reported
     as notices rather than violations.
  7. Evaluation catalogues appear no earlier than T+45 from window close (D7),
     so no window can have been scored against a catalogue seen too early.

  With --online, each manifest's recorded Actions run is fetched through the
  GitHub API and its server-side start time compared against the manifest.
  That timestamp is the one anchor neither the committer nor this repository
  can set, which is why D10 calls it the stronger of the two.

USAGE

    python scripts/audit_history.py            # offline, exits 1 on violation
    python scripts/audit_history.py --online   # also verifies the Actions runs
    python scripts/audit_history.py --json     # machine readable

A clean run over a record that spans months is the artifact. It is meant to be
run by someone who does not trust the author, which includes the author.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import subprocess
import sys
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent

# D7: a window is frozen and scored 45 days after it closes.
FREEZE_DAYS = 45

# D10: the identity scheduled workflow commits are authored under.
BOT_AUTHOR = "github-actions[bot]"

EVALUATION_NAME = re.compile(
    r"^catalogue-(\d{4}-\d{2}-\d{2})-to-(\d{4}-\d{2}-\d{2})\.parquet$"
)

VIOLATION = "violation"
NOTICE = "notice"


@dataclass
class Finding:
    check: str
    severity: str
    subject: str
    detail: str


@dataclass
class Change:
    """One commit's touch of one path."""

    sha: str
    committed_at: datetime
    author: str
    status: str


# --------------------------------------------------------------------------
# git, kept to as few invocations as possible
# --------------------------------------------------------------------------


def git(*args: str) -> str:
    result = subprocess.run(
        ["git", *args],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    if result.returncode != 0:
        raise RuntimeError(f"git {' '.join(args)} failed: {result.stderr.strip()}")
    return result.stdout


def history(*paths: str) -> dict[str, list[Change]]:
    """Every commit touching the given paths, as one git call.

    One `git log` per file would work today, with sixteen forecasts. This
    project intends to accumulate a file per window per model per horizon per
    stratum for years, so the audit has to stay a single pass over history
    rather than growing with the record it is auditing.

    Merge commits show no name-status by default. A file that only ever
    entered through a merge would therefore have no history here, which
    `check_every_forecast_has_history` treats as a finding rather than as a
    pass.
    """
    raw = git(
        "log",
        "--format=%x01%H%x1f%ct%x1f%an",
        "--name-status",
        "--",
        *paths,
    )
    touched: dict[str, list[Change]] = {}
    sha = author = ""
    committed_at = datetime.fromtimestamp(0, timezone.utc)
    for line in raw.splitlines():
        if line.startswith("\x01"):
            sha, epoch, author = line[1:].split("\x1f")
            committed_at = datetime.fromtimestamp(int(epoch), timezone.utc)
            continue
        if not line.strip():
            continue
        parts = line.split("\t")
        status, path = parts[0], parts[-1]
        touched.setdefault(path, []).append(
            Change(sha=sha, committed_at=committed_at, author=author, status=status)
        )
    return touched


class ShallowCloneError(RuntimeError):
    """Raised rather than reporting a clean audit over a truncated history."""


def assert_full_history() -> None:
    """Refuse to audit a shallow clone.

    actions/checkout fetches depth 1 by default. Every check here iterates over
    the commits that touched a file, so against a shallow clone they would each
    find one commit or none, examine almost nothing, and print "Clean". That is
    the worst available outcome: a green audit is read as proof, and this one
    would be proof of nothing.

    So this refuses instead of degrading. An audit that cannot see the history
    has to say so, not pass quietly.
    """
    if git("rev-parse", "--is-shallow-repository").strip() == "true":
        raise ShallowCloneError(
            "this is a shallow clone, so the history needed to audit the record "
            "is not present. Refusing to report a result that would look clean "
            "because nothing was checked. Use fetch-depth: 0 in CI, or "
            "git fetch --unshallow locally."
        )


def files_at_head(prefix: str) -> list[str]:
    raw = git("ls-tree", "-r", "--name-only", "HEAD", prefix)
    return [line for line in raw.splitlines() if line.strip()]


def blob_at(sha: str, path: str) -> bytes | None:
    result = subprocess.run(
        ["git", "show", f"{sha}:{path}"],
        cwd=REPO_ROOT,
        capture_output=True,
    )
    return result.stdout if result.returncode == 0 else None


# --------------------------------------------------------------------------
# helpers
# --------------------------------------------------------------------------


def parse_utc(text: str) -> datetime:
    return datetime.fromisoformat(text.replace("Z", "+00:00"))


def iso(moment: datetime) -> str:
    return moment.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def load_json_at(sha: str, path: str) -> dict | None:
    blob = blob_at(sha, path)
    if blob is None:
        return None
    try:
        return json.loads(blob.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        return None


def manifest_path_for(forecast_path: str) -> str:
    return forecast_path[: -len(".json")] + ".manifest.json"


def is_manifest(path: str) -> bool:
    return path.endswith(".manifest.json")


# --------------------------------------------------------------------------
# the checks
# --------------------------------------------------------------------------


def check_every_forecast_has_history(
    forecasts: list[str], touched: dict[str, list[Change]]
) -> list[Finding]:
    """A file present at HEAD with no commit history is not an oddity to skip.

    It is the one way a file could sit in the tree without any of the
    timestamp checks below ever running against it, so it is reported rather
    than passed over.
    """
    return [
        Finding(
            check="history-present",
            severity=VIOLATION,
            subject=path,
            detail=(
                "present at HEAD but no commit history found, so none of the "
                "timing checks could be applied to it"
            ),
        )
        for path in forecasts
        if not touched.get(path)
    ]


def check_no_post_window_modification(
    forecasts: list[str],
    touched: dict[str, list[Change]],
    windows: dict[str, datetime],
) -> list[Finding]:
    """The load-bearing check.

    Rule 1 lives inside eq.publish, which refuses to write once the window has
    started. This verifies the outcome from the outside, against every commit
    that ever touched the file, so a bug in that refusal, or a hand edit that
    bypassed it entirely, is visible in the record rather than only in the code
    that was supposed to prevent it.
    """
    findings = []
    for path in forecasts:
        start = windows.get(path)
        if start is None:
            continue
        for change in touched.get(path, []):
            if change.committed_at >= start:
                findings.append(
                    Finding(
                        check="no-post-window-modification",
                        severity=VIOLATION,
                        subject=path,
                        detail=(
                            f"commit {change.sha[:12]} ({change.status}) touched this "
                            f"at {iso(change.committed_at)}, but the window opened at "
                            f"{iso(start)}. A forecast altered after its window began "
                            f"is indistinguishable from a backfill to a reader."
                        ),
                    )
                )
    return findings


def check_published_before_window(
    forecasts: list[str],
    touched: dict[str, list[Change]],
    windows: dict[str, datetime],
) -> list[Finding]:
    """Rule 1 stated positively: the file existed before the window opened."""
    findings = []
    for path in forecasts:
        start = windows.get(path)
        if start is None:
            continue
        changes = touched.get(path, [])
        adds = [c for c in changes if c.status.startswith("A")]
        introduced = min(
            (c.committed_at for c in (adds or changes)),
            default=None,
        )
        if introduced is None:
            continue
        if introduced >= start:
            findings.append(
                Finding(
                    check="published-before-window",
                    severity=VIOLATION,
                    subject=path,
                    detail=(
                        f"first appears at {iso(introduced)}, at or after its window "
                        f"opened at {iso(start)}"
                    ),
                )
            )
    return findings


def check_manifest_hashes(
    forecasts: list[str], touched: dict[str, list[Change]]
) -> list[Finding]:
    """The manifest's sha256 must match the forecast content it names.

    Checked at every commit where the forecast changed, not only at HEAD. A
    manifest that matched at HEAD but never matched historically would mean the
    committed record and its checksum drifted apart at some point and were
    quietly reconciled later.
    """
    findings = []
    for path in forecasts:
        manifest_name = manifest_path_for(path)
        for change in touched.get(path, []):
            blob = blob_at(change.sha, path)
            manifest = load_json_at(change.sha, manifest_name)
            if blob is None or manifest is None:
                findings.append(
                    Finding(
                        check="manifest-hash",
                        severity=VIOLATION,
                        subject=path,
                        detail=(
                            f"at commit {change.sha[:12]} the forecast and its "
                            f"manifest do not both exist, so the content is "
                            f"unverifiable at that point in history"
                        ),
                    )
                )
                continue
            actual = hashlib.sha256(blob).hexdigest()
            recorded = manifest.get("sha256")
            if actual != recorded:
                findings.append(
                    Finding(
                        check="manifest-hash",
                        severity=VIOLATION,
                        subject=path,
                        detail=(
                            f"at commit {change.sha[:12]} the manifest records "
                            f"{recorded} but the committed content hashes to {actual}"
                        ),
                    )
                )
            named = manifest.get("file")
            if named != path:
                findings.append(
                    Finding(
                        check="manifest-hash",
                        severity=VIOLATION,
                        subject=path,
                        detail=(
                            f"at commit {change.sha[:12]} the manifest names "
                            f"{named!r}, which is not the file it sits beside"
                        ),
                    )
                )
    return findings


def check_windows_agree(forecasts: list[str]) -> tuple[dict[str, datetime], list[Finding]]:
    """The window has three independent statements: the manifest, the forecast
    payload, and the date in the path. All three must say the same thing.

    Returns the window start per forecast so the timing checks have one agreed
    value to work from, and refuses to supply one where the three disagree.
    """
    findings: list[Finding] = []
    windows: dict[str, datetime] = {}
    for path in forecasts:
        forecast = load_json_at("HEAD", path)
        manifest = load_json_at("HEAD", manifest_path_for(path))
        if forecast is None or manifest is None:
            findings.append(
                Finding(
                    check="window-agreement",
                    severity=VIOLATION,
                    subject=path,
                    detail="forecast or manifest missing or unreadable at HEAD",
                )
            )
            continue

        from_manifest = manifest.get("window_start_utc")
        from_forecast = forecast.get("window_start_utc")
        from_path = Path(path).stem

        if from_manifest != from_forecast:
            findings.append(
                Finding(
                    check="window-agreement",
                    severity=VIOLATION,
                    subject=path,
                    detail=(
                        f"manifest says the window starts {from_manifest}, the "
                        f"forecast says {from_forecast}"
                    ),
                )
            )
            continue

        start = parse_utc(from_manifest)
        if start.strftime("%Y-%m-%d") != from_path:
            findings.append(
                Finding(
                    check="window-agreement",
                    severity=VIOLATION,
                    subject=path,
                    detail=(
                        f"the file is named {from_path} but its window starts "
                        f"{from_manifest}"
                    ),
                )
            )
            continue

        windows[path] = start
    return windows, findings


def check_grid_hash_constant(
    forecasts: list[str], touched: dict[str, list[Change]]
) -> list[Finding]:
    """D1 freezes one grid. Every manifest ever committed must name it.

    Taken across history rather than across HEAD, because a grid that changed
    and changed back would leave HEAD looking consistent.
    """
    seen: dict[str, list[str]] = {}
    for path in forecasts:
        manifest_name = manifest_path_for(path)
        for change in touched.get(path, []):
            manifest = load_json_at(change.sha, manifest_name)
            if manifest is None:
                continue
            grid = manifest.get("grid_hash")
            if grid:
                seen.setdefault(grid, []).append(f"{manifest_name}@{change.sha[:12]}")
    if len(seen) <= 1:
        return []
    return [
        Finding(
            check="grid-hash-constant",
            severity=VIOLATION,
            subject="forecasts/",
            detail=(
                "more than one grid hash appears across the record, so forecasts "
                "are not all against the same region: "
                + "; ".join(
                    f"{grid[:12]} in {len(where)} manifest(s), first {where[0]}"
                    for grid, where in sorted(seen.items())
                )
            ),
        )
    ]


def check_commit_authorship(touched: dict[str, list[Change]]) -> list[Finding]:
    """D10 does not forbid a human touching the forecast history. It requires
    that such a touch be visible. Reported as a notice for exactly that reason:
    failing the audit would create pressure to hide it.
    """
    human: dict[str, set[str]] = {}
    for path, changes in touched.items():
        for change in changes:
            if change.author != BOT_AUTHOR:
                human.setdefault(f"{change.sha[:12]} by {change.author}", set()).add(path)
    return [
        Finding(
            check="commit-authorship",
            severity=NOTICE,
            subject=who,
            detail=(
                f"a commit not authored {BOT_AUTHOR} touched {len(paths)} path(s) "
                f"under the published record, for example {sorted(paths)[0]}. D10 "
                f"permits this and requires it be visible, which is what this is."
            ),
        )
        for who, paths in sorted(human.items())
    ]


def check_evaluation_not_early(touched: dict[str, list[Change]]) -> list[Finding]:
    """D7 freezes a window's evaluation catalogue at T+45 from window close.

    A catalogue committed before that is a catalogue that could have been seen
    while the outcome was still revisable, which is what the 45 days exist to
    prevent.
    """
    findings = []
    for path, changes in sorted(touched.items()):
        match = EVALUATION_NAME.match(Path(path).name)
        if not match:
            continue
        window_end = datetime.strptime(match.group(2), "%Y-%m-%d").replace(
            tzinfo=timezone.utc
        )
        due = window_end + timedelta(days=FREEZE_DAYS)
        introduced = min(c.committed_at for c in changes)
        if introduced < due:
            findings.append(
                Finding(
                    check="evaluation-not-early",
                    severity=VIOLATION,
                    subject=path,
                    detail=(
                        f"committed {iso(introduced)}, but the window closes "
                        f"{iso(window_end)} and its T+{FREEZE_DAYS} freeze is not due "
                        f"until {iso(due)}, which is "
                        f"{(due - introduced).days} day(s) later"
                    ),
                )
            )
    return findings


class GitHubCliUnavailableError(RuntimeError):
    """Raised rather than quietly omitting the only anchor that matters most."""


def assert_gh_available() -> None:
    """Refuse --online without the GitHub CLI, instead of skipping the check.

    Same reasoning as the shallow clone refusal. Someone who passes --online is
    asking for the strong anchor to be verified. Printing a clean result while
    silently having verified nothing is the one outcome worse than failing.
    """
    try:
        subprocess.run(["gh", "--version"], capture_output=True, check=True)
    except (FileNotFoundError, OSError, subprocess.CalledProcessError) as exc:
        raise GitHubCliUnavailableError(
            "--online needs the GitHub CLI (gh) on PATH and authenticated. The "
            "Actions run start time is the one anchor neither the committer nor "
            "this repository can set, so a run that could not check it must not "
            "report as though it had. Install gh, or omit --online and rely on "
            "the weaker commit anchor alone."
        ) from exc


def check_actions_runs(forecasts: list[str]) -> list[Finding]:
    """The strong anchor, per D10.

    The commit timestamp is set by whoever commits. The Actions run start time
    is recorded by GitHub, server side, and is retrievable independently of
    this repository. This is the only check here that a determined author of a
    fabricated history could not satisfy by choosing numbers.
    """
    assert_gh_available()
    findings = []
    for path in forecasts:
        manifest = load_json_at("HEAD", manifest_path_for(path))
        if manifest is None:
            continue
        run = manifest.get("anchors", {}).get("ci_run", {})
        run_id, repository = run.get("run_id"), run.get("repository")
        if not run.get("present") or not run_id or not repository:
            findings.append(
                Finding(
                    check="actions-run",
                    severity=NOTICE,
                    subject=path,
                    detail="no Actions run anchor recorded, so only the weak anchor exists",
                )
            )
            continue
        result = subprocess.run(
            ["gh", "api", f"repos/{repository}/actions/runs/{run_id}", "--jq", ".run_started_at"],
            capture_output=True,
            text=True,
        )
        if result.returncode != 0:
            findings.append(
                Finding(
                    check="actions-run",
                    severity=VIOLATION,
                    subject=path,
                    detail=(
                        f"run {run_id} in {repository} could not be fetched, so its "
                        f"claimed timestamp is unverified: {result.stderr.strip()[:160]}"
                    ),
                )
            )
            continue
        started = parse_utc(result.stdout.strip())
        published = parse_utc(manifest["published_at_utc"])
        drift = abs((published - started).total_seconds())
        if drift > 3600:
            findings.append(
                Finding(
                    check="actions-run",
                    severity=VIOLATION,
                    subject=path,
                    detail=(
                        f"the manifest claims it was published {iso(published)} but "
                        f"GitHub records run {run_id} as starting {iso(started)}, "
                        f"{drift / 3600:.1f} hours apart"
                    ),
                )
            )
    return findings


# --------------------------------------------------------------------------
# driver
# --------------------------------------------------------------------------


def audit(*, online: bool = False) -> list[Finding]:
    assert_full_history()
    forecasts = [p for p in files_at_head("forecasts/") if not is_manifest(p)]
    touched = history("forecasts/", "evaluation/")

    windows, findings = check_windows_agree(forecasts)
    findings += check_every_forecast_has_history(forecasts, touched)
    findings += check_no_post_window_modification(forecasts, touched, windows)
    findings += check_published_before_window(forecasts, touched, windows)
    findings += check_manifest_hashes(forecasts, touched)
    findings += check_grid_hash_constant(forecasts, touched)
    findings += check_commit_authorship(touched)
    findings += check_evaluation_not_early(touched)
    if online:
        findings += check_actions_runs(forecasts)
    return findings


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "--online",
        action="store_true",
        help="also verify each manifest's Actions run against the GitHub API",
    )
    parser.add_argument("--json", action="store_true", help="emit findings as JSON")
    args = parser.parse_args(argv)

    try:
        findings = audit(online=args.online)
    except (ShallowCloneError, GitHubCliUnavailableError) as exc:
        # Exit 2, distinct from the 1 that means violations were found. "Could
        # not audit" and "audited and found problems" are different answers, and
        # a caller that conflates them would treat an audit that never ran as a
        # failing record, or worse, retry until it got a 0 from somewhere.
        print(f"Cannot audit: {exc}", file=sys.stderr)
        return 2

    violations = [f for f in findings if f.severity == VIOLATION]
    notices = [f for f in findings if f.severity == NOTICE]

    if args.json:
        print(
            json.dumps(
                {
                    "violations": [asdict(f) for f in violations],
                    "notices": [asdict(f) for f in notices],
                    "clean": not violations,
                },
                indent=2,
            )
        )
        return 1 if violations else 0

    forecasts = [p for p in files_at_head("forecasts/") if not is_manifest(p)]
    print(f"Audited {len(forecasts)} forecast(s) against git history.")
    if args.online:
        print("Actions run anchors verified against the GitHub API.")
    print()

    for label, group in (("VIOLATION", violations), ("notice", notices)):
        for finding in group:
            print(f"  [{label}] {finding.check}: {finding.subject}")
            print(f"      {finding.detail}")
            print()

    if violations:
        print(f"{len(violations)} violation(s). The record does not support its claims.")
        return 1
    print(f"Clean. {len(notices)} notice(s).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
