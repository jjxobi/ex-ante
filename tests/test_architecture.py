"""Architectural claims, made executable.

This project has three kinds of drift and only two of them were covered.

Code bugs fail their tests. Untested invariants get caught by the property
tests added throughout this build, the bandwidth floor, the duplicate rule, the
empty-window rule. But a claim made in PROSE is a third kind: nothing executes
it, so nothing fails when it stops being true.

That is not hypothetical. The eq.snapshots module docstring asserted that the
two selectors shared no helper. It was accurate when written and false one
commit later, and it stayed false until someone read it. A stale architectural
claim is worse than an absent one, because a reader trusts it.

So the claims worth making are made here instead, where they run. Each test
below corresponds to a sentence somewhere in the documentation, and the sentence
is only allowed to exist because this enforces it.

These inspect source text rather than behaviour on purpose. The properties are
about how the code is arranged, not what it computes, and arrangement is exactly
what a normal test suite does not check.
"""

from __future__ import annotations

import ast
import re
import sys
import tomllib
from pathlib import Path

import pytest

SRC = Path(__file__).resolve().parent.parent / "src" / "eq"


def modules() -> list[Path]:
    return sorted(p for p in SRC.glob("*.py") if p.name != "__init__.py")


def source_of(name: str) -> str:
    return (SRC / f"{name}.py").read_text(encoding="utf-8")


def code_lines(path: Path) -> list[tuple[int, str]]:
    """Lines with comments stripped, so prose about a pattern is not mistaken
    for a use of it. Crude but adequate: this project does not put string
    literals containing code on comment lines.
    """
    out = []
    for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        stripped = line.split("#", 1)[0]
        if stripped.strip():
            out.append((number, stripped))
    return out


# --------------------------------------------------------------------------
# One definition of a dated snapshot
#
# Claimed in eq/snapshots.py: "there is one definition here and not three that
# happen to agree". This was run once as a grep by hand; it now runs always.
# --------------------------------------------------------------------------

def test_the_dated_snapshot_pattern_is_defined_exactly_once():
    definitions = []
    for path in modules():
        for number, line in code_lines(path):
            if re.search(r"^\s*DATED_(GLOB|NAME)\s*=", line):
                definitions.append(f"{path.name}:{number}")
    assert len(definitions) == 2, (
        f"expected exactly one DATED_GLOB and one DATED_NAME, found {definitions}"
    )
    assert all(d.startswith("snapshots.py") for d in definitions), (
        f"the dated snapshot pattern must live only in snapshots.py, found {definitions}"
    )


def test_only_snapshots_globs_the_snapshot_directory():
    """Every other module goes through dated_snapshots rather than globbing.

    The diff subcommand once globbed catalogue-*.parquet itself, which matches
    catalogue-ci.parquet, and since "c" sorts after "2" the lexical maximum
    returned the CI slice ahead of every real dated catalogue.
    """
    offenders = []
    for path in modules():
        if path.name == "snapshots.py":
            continue
        for number, line in code_lines(path):
            if ".glob(" in line and "SNAPSHOT" in line.upper():
                offenders.append(f"{path.name}:{number}: {line.strip()}")
    assert offenders == [], (
        "these modules glob the snapshot directory directly rather than calling "
        f"eq.snapshots.dated_snapshots: {offenders}"
    )


def test_no_unrestricted_catalogue_glob_anywhere():
    """catalogue-*.parquet must never appear as a glob pattern in source."""
    offenders = []
    for path in modules():
        for number, line in code_lines(path):
            if '"catalogue-*' in line or "'catalogue-*" in line:
                offenders.append(f"{path.name}:{number}: {line.strip()}")
    assert offenders == [], (
        f"unrestricted catalogue glob, which matches catalogue-ci.parquet: {offenders}"
    )


# --------------------------------------------------------------------------
# The freeze never reaches for the recency selector
#
# Claimed in eq/freeze.py and D7.1. Selecting the newest snapshot for a T+45
# freeze would score a window against the wrong day.
# --------------------------------------------------------------------------

def test_freeze_never_calls_the_recency_selector():
    offenders = [
        f"freeze.py:{number}: {line.strip()}"
        for number, line in code_lines(SRC / "freeze.py")
        if "newest_snapshot" in line
    ]
    assert offenders == [], (
        "eq.freeze must select by exact date, never by recency, per D7.1: "
        f"{offenders}"
    )


# --------------------------------------------------------------------------
# No module reaches into another module's private names
#
# freeze.py once called snapshots._dated_snapshots. That was one definition
# rather than two, which was the property that mattered, but a refactor of the
# private helper would not have counted freeze.py as a consumer.
# --------------------------------------------------------------------------

def test_no_module_uses_another_modules_private_name():
    package_modules = {p.stem for p in modules()}
    offenders = []
    for path in modules():
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if not isinstance(node, ast.Attribute):
                continue
            if not node.attr.startswith("_") or node.attr.startswith("__"):
                continue
            target = node.value
            if isinstance(target, ast.Name) and target.id in package_modules:
                if target.id != path.stem:
                    offenders.append(
                        f"{path.name}:{node.lineno}: {target.id}.{node.attr}"
                    )
    assert offenders == [], (
        "a module reaches into another module's private name, so a refactor of "
        f"that name would not see the caller as a consumer: {offenders}"
    )


# --------------------------------------------------------------------------
# The expander sees nothing outside its arguments
#
# Claimed in D13.5 and in eq/expander.py: expansion is a pure function of
# committed bytes, with no clock, no environment and no filesystem. The
# determinism tests check that the OUTPUT does not vary; this checks the
# expander cannot see anything that would make it vary.
# --------------------------------------------------------------------------

FORBIDDEN_IN_EXPANDER = ("os", "sys", "time", "datetime", "random", "pathlib")


def test_the_expander_imports_nothing_that_could_vary():
    tree = ast.parse(source_of("expander"))
    imported = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported.update(alias.name.split(".")[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imported.add(node.module.split(".")[0])
    leaked = sorted(imported & set(FORBIDDEN_IN_EXPANDER))
    assert leaked == [], (
        "the expander must be a pure function of its arguments per D13.5, but "
        f"it imports {leaked}, which can vary between runs or machines"
    )


def test_the_expander_reads_no_files():
    offenders = [
        f"expander.py:{number}: {line.strip()}"
        for number, line in code_lines(SRC / "expander.py")
        if "open(" in line or "read_text" in line or "read_bytes" in line
    ]
    assert offenders == [], f"the expander must not touch the filesystem: {offenders}"


# --------------------------------------------------------------------------
# The package runs on the dependencies it declares
# --------------------------------------------------------------------------

REPO = SRC.parent.parent

# Distributions whose import name is not the name pip installs. Anything absent
# is assumed to import under its own name, which is true of the rest.
IMPORT_NAME = {"pycsep": "csep"}


def declared_base_dependencies() -> set[str]:
    with (REPO / "pyproject.toml").open("rb") as handle:
        pyproject = tomllib.load(handle)
    declared = set()
    for requirement in pyproject["project"]["dependencies"]:
        distribution = re.split(r"[<>=!~\[;\s]", requirement, maxsplit=1)[0]
        declared.add(IMPORT_NAME.get(distribution, distribution))
    return declared


def test_every_module_imports_only_declared_base_dependencies():
    """A base install has to be enough to import every module in the package.

    This is the test that did not exist on the day the health workflow failed.
    eq.score imported pyCSEP, which was declared under the dev extras, so
    `pip install -e .` produced a package whose command line entry point could
    not be imported at all. Nothing caught it, because every other workflow
    installs the dev extras and therefore never runs the package as an outside
    installer would receive it.

    The dev extras are for what tests need. The moment core src code imports
    something, that something is a real dependency, and this asserts the two
    stay in agreement without needing a clean environment to notice.
    """
    declared = declared_base_dependencies()
    undeclared: dict[str, list[str]] = {}
    for path in modules():
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                roots = [alias.name.split(".")[0] for alias in node.names]
            elif isinstance(node, ast.ImportFrom) and node.module and not node.level:
                roots = [node.module.split(".")[0]]
            else:
                continue
            for root in roots:
                if root in sys.stdlib_module_names or root == "eq" or root in declared:
                    continue
                undeclared.setdefault(root, []).append(path.name)

    assert undeclared == {}, (
        "these packages are imported by core src code but are not declared in "
        "[project.dependencies], so a base install cannot import them: "
        + "; ".join(
            f"{root} (imported by {', '.join(sorted(set(files)))})"
            for root, files in sorted(undeclared.items())
        )
    )


# --------------------------------------------------------------------------
# Every architectural claim above corresponds to documented prose
# --------------------------------------------------------------------------

def test_this_module_is_referenced_by_the_decisions_file():
    """The prose and the enforcement have to know about each other.

    If DECISIONS.md stops pointing here, the claims it makes have quietly gone
    back to being unenforced sentences.
    """
    decisions = (SRC.parent.parent / "DECISIONS.md").read_text(encoding="utf-8")
    assert "test_architecture" in decisions, (
        "DECISIONS.md must reference this module, so a reader can tell which of "
        "its architectural claims are executable and which are only prose"
    )
