# Phase 1: Catalogue Pipeline Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Ingest the GeoNet earthquake catalogue into validated parquet, with dbt tests on DuckDB that fail loudly when the data or the upstream API changes.

**Architecture:** A thin HTTP client with retry and backoff pulls CSV from GeoNet Quake Search. A pure parsing layer converts rows to typed records. An atomic writer lands parquet so a failed pull never leaves partial data. dbt models on DuckDB stage and clean the raw parquet, with schema tests as the contract. A second ingest path takes a daily full-catalogue snapshot, which is the only way to build the revision curve because GeoNet does not expose per-event history.

**Tech Stack:** Python 3.12, requests, pyarrow, duckdb, dbt-core with dbt-duckdb, pytest.

**Scope boundary:** Region membership and depth stratum are **not** assigned in Phase 1. They depend on the frozen grid and boundary, which Phase 2 generates. Phase 1 ends at a clean, validated, deduplicated event table.

## Global Constraints

- Python 3.12. The repository already has `duckdb`, `pandas`, `pyarrow`, `requests`, `pytest` installed; `dbt-duckdb` is not and must be installed.
- **No em dashes anywhere in any file**, including code comments, docstrings, commit messages, and documentation. Use commas, colons, semicolons or parentheses.
- **Every commit is attributed to Jesse O'Brien alone.** No third-party co-author trailers of any kind appear on any commit.
- Human commits are authored `Jesse O'Brien <jesse@jesse-obrien.com>`. This is already set in the repo's local git config; do not change it.
- Frozen values come from `DECISIONS.md` and are never hardcoded ad hoc in more than one place. Phase 1 uses only the bounding box.
- GeoNet bounding box, verbatim: `163.60840,-49.18170,182.98828,-32.28713`
- Longitude uses the continuous [163.6, 183.0] convention. Values arriving negative (past the antimeridian) get 360 added. Never wrap.
- Commit after every task. Small commits.

---

## File Structure

| File | Responsibility |
|---|---|
| `pyproject.toml` | Package metadata, dependencies, pytest config |
| `src/eq/__init__.py` | Package marker, version |
| `src/eq/paths.py` | Canonical repository paths, single source of truth |
| `src/eq/geonet.py` | HTTP client: URL building, retry with backoff, returns raw CSV text |
| `src/eq/parse.py` | Pure functions: CSV text to typed records, longitude convention |
| `src/eq/storage.py` | Atomic parquet write, never leaves partial files |
| `src/eq/ingest.py` | Orchestration: delta ingest and full snapshot |
| `src/eq/revisions.py` | Diff consecutive snapshots into the committed revision record |
| `src/eq/cli.py` | Command line entrypoints |
| `dbt/dbt_project.yml` | dbt project config |
| `dbt/profiles.yml` | DuckDB connection profile |
| `dbt/models/staging/stg_quakes.sql` | Typed, deduplicated staging view over raw parquet |
| `dbt/models/staging/schema.yml` | Column tests on staging |
| `dbt/models/marts/fct_events.sql` | Clean event fact table |
| `dbt/models/marts/schema.yml` | Contract tests on the mart |
| `dbt/tests/assert_no_future_events.sql` | Singular test: no event dated after run time |
| `dbt/tests/assert_depthtype_share.sql` | Singular test: assigned-depth share within expected bounds |
| `tests/conftest.py` | Shared fixtures, sample CSV |
| `tests/test_geonet.py` | Client retry and URL behaviour |
| `tests/test_parse.py` | Parsing and longitude convention |
| `tests/test_storage.py` | Atomicity under failure |
| `tests/test_ingest.py` | Orchestration, including the outage case |
| `tests/test_revisions.py` | Diff semantics: new, revised, withdrawn |

---

### Task 1: Project scaffolding and canonical paths

**Files:**
- Create: `pyproject.toml`
- Create: `src/eq/__init__.py`
- Create: `src/eq/paths.py`
- Test: `tests/test_paths.py`

**Interfaces:**
- Consumes: nothing
- Produces: `eq.paths.REPO_ROOT: Path`, `eq.paths.RAW_DIR: Path`, `eq.paths.SNAPSHOT_DIR: Path`, `eq.paths.DUCKDB_PATH: Path`, `eq.paths.ensure_dirs() -> None`

- [ ] **Step 1: Write the failing test**

Create `tests/test_paths.py`:

```python
from pathlib import Path

from eq import paths


def test_repo_root_contains_decisions_file():
    assert (paths.REPO_ROOT / "DECISIONS.md").is_file()


def test_data_dirs_are_under_repo_root():
    assert paths.RAW_DIR.is_relative_to(paths.REPO_ROOT)
    assert paths.SNAPSHOT_DIR.is_relative_to(paths.REPO_ROOT)


def test_ensure_dirs_creates_them(tmp_path, monkeypatch):
    # Every directory ensure_dirs() touches must be patched, or the test
    # creates real directories under the repository root as a side effect.
    monkeypatch.setattr(paths, "RAW_DIR", tmp_path / "raw")
    monkeypatch.setattr(paths, "SNAPSHOT_DIR", tmp_path / "snapshots")
    monkeypatch.setattr(paths, "EVALUATION_DIR", tmp_path / "evaluation")
    paths.ensure_dirs()
    assert (tmp_path / "raw").is_dir()
    assert (tmp_path / "snapshots").is_dir()
    assert (tmp_path / "evaluation").is_dir()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_paths.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'eq'`

- [ ] **Step 3: Write minimal implementation**

Create `pyproject.toml`:

```toml
[project]
name = "eq"
version = "0.1.0"
description = "Publicly scored New Zealand seismicity forecast"
requires-python = ">=3.12"
dependencies = [
    "requests>=2.32",
    "pyarrow>=17",
    "duckdb>=1.1",
    "pandas>=2.2",
]

[project.optional-dependencies]
dev = [
    "pytest>=8.3",
    "dbt-duckdb>=1.10",
]

[build-system]
requires = ["setuptools>=68"]
build-backend = "setuptools.build_meta"

[tool.setuptools.packages.find]
where = ["src"]

[tool.pytest.ini_options]
testpaths = ["tests"]
pythonpath = ["src"]
```

Create `src/eq/__init__.py`:

```python
"""Publicly scored New Zealand seismicity forecast."""

__version__ = "0.1.0"
```

Create `src/eq/paths.py`:

```python
"""Canonical repository paths.

Every component resolves paths through this module so that no path string is
written twice. REPO_ROOT is found by walking upward until DECISIONS.md is seen,
which keeps the package importable from any working directory.
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
DUCKDB_PATH = DATA_DIR / "eq.duckdb"
DBT_DIR = REPO_ROOT / "dbt"


def ensure_dirs() -> None:
    """Create every data directory this project writes to."""
    for directory in (RAW_DIR, SNAPSHOT_DIR, EVALUATION_DIR):
        directory.mkdir(parents=True, exist_ok=True)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_paths.py -v`
Expected: 3 passed

- [ ] **Step 5: Commit**

```bash
git add pyproject.toml src/eq/__init__.py src/eq/paths.py tests/test_paths.py
git commit -m "Add package scaffolding and canonical paths"
```

---

### Task 2: GeoNet client with retry and backoff

GeoNet Quake Search resets connections under sustained querying. This was observed repeatedly during design measurement, so retry is a requirement rather than a precaution.

**Files:**
- Create: `src/eq/geonet.py`
- Test: `tests/test_geonet.py`

**Interfaces:**
- Consumes: nothing
- Produces: `eq.geonet.BBOX: str`, `eq.geonet.build_url(min_magnitude: float, start: date, end: date) -> str`, `eq.geonet.fetch_csv(url: str, *, session=None, max_attempts: int = 6, sleep=time.sleep) -> str`, `eq.geonet.GeoNetError(Exception)`

- [ ] **Step 1: Write the failing test**

Create `tests/test_geonet.py`:

```python
from datetime import date

import pytest

from eq import geonet


class FakeResponse:
    def __init__(self, text: str):
        self.text = text
        self.status_code = 200

    def raise_for_status(self) -> None:
        return None


class FlakySession:
    """Fails a fixed number of times, then succeeds."""

    def __init__(self, failures: int, payload: str = "publicid\nabc\n"):
        self.failures = failures
        self.payload = payload
        self.calls = 0

    def get(self, url, timeout=None):
        self.calls += 1
        if self.calls <= self.failures:
            raise ConnectionError("connection reset by peer")
        return FakeResponse(self.payload)


def test_build_url_contains_bbox_and_dates():
    url = geonet.build_url(3.0, date(2025, 1, 1), date(2026, 1, 1))
    assert geonet.BBOX in url
    assert "minmag=3.0" in url
    assert "startdate=2025-01-01T00:00:00" in url
    assert "enddate=2026-01-01T00:00:00" in url


def test_fetch_csv_returns_body_on_first_success():
    session = FlakySession(failures=0)
    body = geonet.fetch_csv("http://example.test", session=session, sleep=lambda _: None)
    assert body == "publicid\nabc\n"
    assert session.calls == 1


def test_fetch_csv_retries_then_succeeds():
    session = FlakySession(failures=3)
    body = geonet.fetch_csv("http://example.test", session=session, sleep=lambda _: None)
    assert body == "publicid\nabc\n"
    assert session.calls == 4


def test_fetch_csv_backs_off_exponentially():
    waits = []
    session = FlakySession(failures=3)
    geonet.fetch_csv("http://example.test", session=session, sleep=waits.append)
    assert waits == [5, 10, 20]


def test_fetch_csv_raises_after_exhausting_attempts():
    # Capture the sleeps rather than discarding them. fetch_csv deliberately
    # does not sleep after the final failed attempt, and only asserting on the
    # exception would let a regression reintroduce that wasted sleep unnoticed.
    waits = []
    session = FlakySession(failures=99)
    with pytest.raises(geonet.GeoNetError) as excinfo:
        geonet.fetch_csv(
            "http://example.test", session=session, max_attempts=3, sleep=waits.append
        )
    assert "3 attempts" in str(excinfo.value)
    assert "connection reset by peer" in str(excinfo.value)
    assert session.calls == 3
    assert waits == [5, 10]
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_geonet.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'eq.geonet'`

- [ ] **Step 3: Write minimal implementation**

Create `src/eq/geonet.py`:

```python
"""HTTP access to the GeoNet Quake Search catalogue export.

Quake Search resets connections under sustained querying, so every request goes
through exponential backoff. A failed pull raises rather than returning partial
data, because a partial catalogue silently corrupts everything downstream.
"""

from __future__ import annotations

import time
from datetime import date

import requests

BBOX = "163.60840,-49.18170,182.98828,-32.28713"
BASE_URL = "https://quakesearch.geonet.org.nz/csv"
TIMEOUT_SECONDS = 600
INITIAL_BACKOFF_SECONDS = 5


class GeoNetError(RuntimeError):
    """Raised when the catalogue cannot be retrieved."""


def build_url(min_magnitude: float, start: date, end: date) -> str:
    """Build a Quake Search CSV export URL for a half-open date interval."""
    return (
        f"{BASE_URL}?bbox={BBOX}"
        f"&minmag={min_magnitude}"
        f"&startdate={start.isoformat()}T00:00:00"
        f"&enddate={end.isoformat()}T00:00:00"
    )


def fetch_csv(
    url: str,
    *,
    session=None,
    max_attempts: int = 6,
    sleep=time.sleep,
) -> str:
    """Fetch CSV text, retrying with exponential backoff.

    Raises GeoNetError if every attempt fails. Never returns a partial body.
    """
    client = session if session is not None else requests.Session()
    last_error: Exception | None = None

    for attempt in range(max_attempts):
        try:
            response = client.get(url, timeout=TIMEOUT_SECONDS)
            response.raise_for_status()
            return response.text
        except Exception as error:
            last_error = error
            if attempt == max_attempts - 1:
                break
            sleep(INITIAL_BACKOFF_SECONDS * 2**attempt)

    raise GeoNetError(f"failed to fetch {url} after {max_attempts} attempts: {last_error}")
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_geonet.py -v`
Expected: 5 passed

- [ ] **Step 5: Commit**

```bash
git add src/eq/geonet.py tests/test_geonet.py
git commit -m "Add GeoNet client with exponential backoff"
```

---

### Task 3: Parse CSV to typed records

**Files:**
- Create: `src/eq/parse.py`
- Create: `tests/conftest.py`
- Test: `tests/test_parse.py`

**Interfaces:**
- Consumes: nothing
- Produces: `eq.parse.COLUMNS: tuple[str, ...]`, `eq.parse.normalise_longitude(value: float) -> float`, `eq.parse.parse_catalogue_csv(text: str) -> list[dict]`

Every record is a dict with keys: `publicid` (str), `origintime` (datetime, UTC), `modificationtime` (datetime, UTC), `longitude` (float, continuous convention), `latitude` (float), `magnitude` (float), `depth` (float), `magnitudetype` (str), `depthtype` (str), `evaluationstatus` (str), `evaluationmode` (str).

- [ ] **Step 1: Write the failing test**

Create `tests/conftest.py`:

```python
import pytest

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
```

Create `tests/test_parse.py`:

```python
from datetime import datetime, timezone

import pytest

from eq import parse


def test_normalise_longitude_leaves_positive_values_alone():
    assert parse.normalise_longitude(177.65) == pytest.approx(177.65)


def test_normalise_longitude_unwraps_antimeridian():
    assert parse.normalise_longitude(-179.5) == pytest.approx(180.5)


def test_parse_returns_one_record_per_row(sample_csv):
    records = parse.parse_catalogue_csv(sample_csv)
    assert len(records) == 2


def test_parse_types_and_values(sample_csv):
    first = parse.parse_catalogue_csv(sample_csv)[0]
    assert first["publicid"] == "2026p083320"
    assert first["origintime"] == datetime(2026, 1, 31, 19, 53, 16, 616000, tzinfo=timezone.utc)
    assert first["magnitude"] == pytest.approx(3.2135171596)
    assert first["depth"] == pytest.approx(35.0411077)
    assert first["depthtype"] == ""


def test_parse_preserves_depthtype_flag(sample_csv):
    second = parse.parse_catalogue_csv(sample_csv)[1]
    assert second["depthtype"] == "operator assigned"


def test_parse_applies_longitude_convention(sample_csv):
    second = parse.parse_catalogue_csv(sample_csv)[1]
    assert second["longitude"] == pytest.approx(180.5)


def test_parse_handles_empty_body():
    assert parse.parse_catalogue_csv("") == []


HEADER = (
    "publicid,origintime,modificationtime,longitude,latitude,magnitude,depth,"
    "magnitudetype,depthtype,evaluationstatus,evaluationmode\n"
)


def test_parse_skips_rows_missing_required_fields():
    text = HEADER + (
        "a,2026-01-01T00:00:00.000Z,2026-01-01T00:00:00.000Z,175.0,-41.0,,10.0,MLv,,confirmed,manual\n"
    )
    assert parse.parse_catalogue_csv(text) == []


def test_parse_raises_on_malformed_required_value():
    # Deliberately loud. An empty magnitude is something GeoNet legitimately
    # emits and is skipped, but a malformed one indicates a corrupted response,
    # and silently dropping it would quietly understate the catalogue.
    text = HEADER + (
        "a,2026-01-01T00:00:00.000Z,2026-01-01T00:00:00.000Z,175.0,-41.0,abc,10.0,MLv,,confirmed,manual\n"
    )
    with pytest.raises(ValueError):
        parse.parse_catalogue_csv(text)


def test_parse_handles_missing_modificationtime():
    text = HEADER + (
        "a,2026-01-01T00:00:00.000Z,,175.0,-41.0,3.5,10.0,MLv,,confirmed,manual\n"
    )
    record = parse.parse_catalogue_csv(text)[0]
    assert record["modificationtime"] is None
    assert record["magnitude"] == pytest.approx(3.5)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_parse.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'eq.parse'`

- [ ] **Step 3: Write minimal implementation**

Create `src/eq/parse.py`:

```python
"""Pure parsing of GeoNet catalogue CSV into typed records.

No IO happens here. Keeping parsing separate from fetching means the awkward
cases (the antimeridian, missing magnitudes, assigned depths) are testable
without touching the network.
"""

from __future__ import annotations

import csv
import io
from datetime import datetime, timezone

COLUMNS = (
    "publicid",
    "origintime",
    "modificationtime",
    "longitude",
    "latitude",
    "magnitude",
    "depth",
    "magnitudetype",
    "depthtype",
    "evaluationstatus",
    "evaluationmode",
)

REQUIRED = ("publicid", "origintime", "longitude", "latitude", "magnitude", "depth")


def normalise_longitude(value: float) -> float:
    """Put longitude on the continuous [163.6, 183.0] convention.

    New Zealand seismicity crosses the antimeridian, so GeoNet reports some
    events with negative longitude. Wrapping would put a discontinuity through
    the Kermadec arc, so negative values are unwrapped by adding 360 instead.
    """
    return value + 360.0 if value < 0 else value


def _parse_timestamp(raw: str) -> datetime | None:
    if not raw:
        return None
    return datetime.fromisoformat(raw.replace("Z", "+00:00")).astimezone(timezone.utc)


def parse_catalogue_csv(text: str) -> list[dict]:
    """Convert Quake Search CSV text into typed records.

    Rows missing any required field are skipped rather than guessed at.
    """
    if not text.strip():
        return []

    records: list[dict] = []
    for row in csv.DictReader(io.StringIO(text)):
        if any(not row.get(field) for field in REQUIRED):
            continue
        records.append(
            {
                "publicid": row["publicid"],
                "origintime": _parse_timestamp(row["origintime"]),
                "modificationtime": _parse_timestamp(row.get("modificationtime", "")),
                "longitude": normalise_longitude(float(row["longitude"])),
                "latitude": float(row["latitude"]),
                "magnitude": float(row["magnitude"]),
                "depth": float(row["depth"]),
                "magnitudetype": row.get("magnitudetype", ""),
                "depthtype": row.get("depthtype", ""),
                "evaluationstatus": row.get("evaluationstatus", ""),
                "evaluationmode": row.get("evaluationmode", ""),
            }
        )
    return records
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_parse.py -v`
Expected: 8 passed

- [ ] **Step 5: Commit**

```bash
git add src/eq/parse.py tests/conftest.py tests/test_parse.py
git commit -m "Add catalogue CSV parsing with longitude convention"
```

---

### Task 4: Atomic parquet writes

A failed pull must never leave partial data on disk. Writing to a temporary file in the same directory and then renaming makes the visible file either complete or absent, never half written.

**Files:**
- Create: `src/eq/storage.py`
- Test: `tests/test_storage.py`

**Interfaces:**
- Consumes: `eq.parse` record dicts
- Produces: `eq.storage.write_parquet_atomic(records: list[dict], destination: Path) -> Path`, `eq.storage.read_parquet(path: Path) -> list[dict]`

- [ ] **Step 1: Write the failing test**

Create `tests/test_storage.py`:

```python
from datetime import datetime, timezone

import pytest

from eq import storage


def make_records(n: int = 2) -> list[dict]:
    return [
        {
            "publicid": f"id{i}",
            "origintime": datetime(2026, 1, 1, tzinfo=timezone.utc),
            "modificationtime": datetime(2026, 2, 1, tzinfo=timezone.utc),
            "longitude": 175.0 + i,
            "latitude": -41.0,
            "magnitude": 3.5,
            "depth": 12.0,
            "magnitudetype": "MLv",
            "depthtype": "operator assigned",
            "evaluationstatus": "confirmed",
            "evaluationmode": "manual",
        }
        for i in range(n)
    ]


def test_write_then_read_round_trips(tmp_path):
    target = tmp_path / "events.parquet"
    storage.write_parquet_atomic(make_records(3), target)
    loaded = storage.read_parquet(target)
    assert len(loaded) == 3
    assert loaded[0]["publicid"] == "id0"
    assert loaded[0]["depthtype"] == "operator assigned"


def test_write_leaves_no_temp_files(tmp_path):
    target = tmp_path / "events.parquet"
    storage.write_parquet_atomic(make_records(), target)
    assert [p.name for p in tmp_path.iterdir()] == ["events.parquet"]


def test_failed_write_does_not_replace_existing_file(tmp_path, monkeypatch):
    target = tmp_path / "events.parquet"
    storage.write_parquet_atomic(make_records(1), target)
    original = target.read_bytes()

    def boom(*args, **kwargs):
        raise OSError("disk full")

    monkeypatch.setattr(storage.pq, "write_table", boom)
    with pytest.raises(OSError):
        storage.write_parquet_atomic(make_records(5), target)

    assert target.read_bytes() == original
    assert [p.name for p in tmp_path.iterdir()] == ["events.parquet"]


def test_write_rejects_empty_records(tmp_path):
    with pytest.raises(ValueError):
        storage.write_parquet_atomic([], tmp_path / "events.parquet")
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_storage.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'eq.storage'`

- [ ] **Step 3: Write minimal implementation**

Create `src/eq/storage.py`:

```python
"""Atomic parquet IO.

Every write lands in a temporary file beside the destination and is then
renamed. On every platform this project targets, rename within a directory is
atomic, so a reader sees either the previous complete file or the new complete
file. A crashed or failed write never leaves a partial catalogue behind.
"""

from __future__ import annotations

import os
from pathlib import Path

import pyarrow as pa
import pyarrow.parquet as pq


def write_parquet_atomic(records: list[dict], destination: Path) -> Path:
    """Write records to parquet atomically. Returns the destination path."""
    if not records:
        raise ValueError("refusing to write an empty catalogue")

    destination = Path(destination)
    destination.parent.mkdir(parents=True, exist_ok=True)
    temp_path = destination.with_name(destination.name + ".tmp")

    try:
        table = pa.Table.from_pylist(records)
        pq.write_table(table, temp_path, compression="zstd")
        os.replace(temp_path, destination)
    finally:
        if temp_path.exists():
            temp_path.unlink()

    return destination


def read_parquet(path: Path) -> list[dict]:
    """Read a parquet file back into record dicts."""
    return pq.read_table(Path(path)).to_pylist()
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_storage.py -v`
Expected: 4 passed

- [ ] **Step 5: Commit**

```bash
git add src/eq/storage.py tests/test_storage.py
git commit -m "Add atomic parquet writes"
```

---

### Task 5: Ingest orchestration and the outage guarantee

**Files:**
- Create: `src/eq/ingest.py`
- Test: `tests/test_ingest.py`

**Interfaces:**
- Consumes: `eq.geonet.build_url`, `eq.geonet.fetch_csv`, `eq.geonet.GeoNetError`, `eq.parse.parse_catalogue_csv`, `eq.storage.write_parquet_atomic`, `eq.paths`
- Produces: `eq.ingest.ingest_range(start: date, end: date, min_magnitude: float, destination: Path, *, fetch=geonet.fetch_csv) -> int`, `eq.ingest.snapshot_full_catalogue(today: date, *, fetch=geonet.fetch_csv) -> Path`

`ingest_range` returns the number of records written. `snapshot_full_catalogue` writes to `SNAPSHOT_DIR / "catalogue-YYYY-MM-DD.parquet"` and returns that path.

- [ ] **Step 1: Write the failing test**

Create `tests/test_ingest.py`:

```python
from datetime import date

import pytest

from eq import geonet, ingest, storage


def test_ingest_range_writes_records(tmp_path, sample_csv):
    target = tmp_path / "out.parquet"
    written = ingest.ingest_range(
        date(2026, 1, 1), date(2026, 2, 1), 3.0, target, fetch=lambda url: sample_csv
    )
    assert written == 2
    assert len(storage.read_parquet(target)) == 2


def test_ingest_range_passes_correct_url(tmp_path, sample_csv):
    seen = {}

    def spy(url):
        seen["url"] = url
        return sample_csv

    ingest.ingest_range(
        date(2025, 1, 1), date(2026, 1, 1), 3.5, tmp_path / "o.parquet", fetch=spy
    )
    assert "minmag=3.5" in seen["url"]
    assert "startdate=2025-01-01T00:00:00" in seen["url"]
    assert geonet.BBOX in seen["url"]


def test_outage_raises_and_writes_nothing(tmp_path):
    target = tmp_path / "out.parquet"

    def failing(url):
        raise geonet.GeoNetError("simulated outage")

    with pytest.raises(geonet.GeoNetError):
        ingest.ingest_range(date(2026, 1, 1), date(2026, 2, 1), 3.0, target, fetch=failing)

    assert not target.exists()
    assert list(tmp_path.iterdir()) == []


def test_outage_leaves_previous_file_intact(tmp_path, sample_csv):
    target = tmp_path / "out.parquet"
    ingest.ingest_range(
        date(2026, 1, 1), date(2026, 2, 1), 3.0, target, fetch=lambda url: sample_csv
    )
    before = target.read_bytes()

    def failing(url):
        raise geonet.GeoNetError("simulated outage")

    with pytest.raises(geonet.GeoNetError):
        ingest.ingest_range(date(2026, 2, 1), date(2026, 3, 1), 3.0, target, fetch=failing)

    assert target.read_bytes() == before


def test_empty_response_raises_rather_than_writing_empty_file(tmp_path):
    target = tmp_path / "out.parquet"
    header_only = "publicid,origintime,modificationtime,longitude,latitude,magnitude,depth\n"
    with pytest.raises(ingest.EmptyCatalogueError):
        ingest.ingest_range(
            date(2026, 1, 1), date(2026, 2, 1), 3.0, target, fetch=lambda url: header_only
        )
    assert not target.exists()


def test_snapshot_writes_dated_filename(tmp_path, monkeypatch, sample_csv):
    monkeypatch.setattr(ingest.paths, "SNAPSHOT_DIR", tmp_path)
    path = ingest.snapshot_full_catalogue(date(2026, 8, 4), fetch=lambda url: sample_csv)
    assert path.name == "catalogue-2026-08-04.parquet"
    assert path.is_file()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_ingest.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'eq.ingest'`

- [ ] **Step 3: Write minimal implementation**

Create `src/eq/ingest.py`:

```python
"""Catalogue ingest orchestration.

Two jobs live here. Delta ingest pulls a date range into parquet. The full
snapshot pulls the whole catalogue once a day and keeps it, which is the only
way to build the magnitude-revision-versus-time curve: GeoNet's per-event
history endpoint returns no revisions, so the curve can only be built forward
by snapshotting and diffing.

A failed or empty pull raises. It never writes, because a partial catalogue
silently corrupts every number downstream.
"""

from __future__ import annotations

from datetime import date
from pathlib import Path

from eq import geonet, parse, paths, storage

SNAPSHOT_START = date(2005, 1, 1)
SNAPSHOT_MIN_MAGNITUDE = 3.0

# Quake Search rejects oversized result sets with HTTP 400. Measured against
# the live service: 2005 to 2014 returns 33,206 rows and succeeds, 2005 to 2018
# is rejected. The full range is about 60,000 events, so it must be chunked.
SNAPSHOT_CHUNK_YEARS = 5


class EmptyCatalogueError(RuntimeError):
    """Raised when GeoNet returns a well formed response containing no events."""


def ingest_range(
    start: date,
    end: date,
    min_magnitude: float,
    destination: Path,
    *,
    fetch=geonet.fetch_csv,
) -> int:
    """Fetch one date range and write it to parquet. Returns records written."""
    url = geonet.build_url(min_magnitude, start, end)
    text = fetch(url)
    records = parse.parse_catalogue_csv(text)

    if not records:
        raise EmptyCatalogueError(f"no events returned for {start} to {end}")

    storage.write_parquet_atomic(records, Path(destination))
    return len(records)


def snapshot_full_catalogue(today: date, *, fetch=geonet.fetch_csv) -> Path:
    """Take the daily full-catalogue snapshot used to build the revision curve.

    The range is fetched in chunks because Quake Search rejects oversized
    result sets. Chunk boundaries are shared, so no time span is skipped, and
    an event returned by two adjacent chunks is deduplicated by publicid,
    keeping the latest modificationtime.

    Any chunk failing propagates, so a partial snapshot is never written.
    """
    paths.SNAPSHOT_DIR.mkdir(parents=True, exist_ok=True)
    destination = paths.SNAPSHOT_DIR / f"catalogue-{today.isoformat()}.parquet"

    accumulated: dict[str, dict] = {}
    for chunk_start, chunk_end in _chunk_ranges(
        SNAPSHOT_START, today, SNAPSHOT_CHUNK_YEARS
    ):
        url = geonet.build_url(SNAPSHOT_MIN_MAGNITUDE, chunk_start, chunk_end)
        for record in parse.parse_catalogue_csv(fetch(url)):
            pid = record["publicid"]
            existing = accumulated.get(pid)
            if existing is None:
                accumulated[pid] = record
                continue
            new_mod, old_mod = record["modificationtime"], existing["modificationtime"]
            if new_mod is not None and (old_mod is None or new_mod > old_mod):
                accumulated[pid] = record

    if not accumulated:
        raise EmptyCatalogueError(f"no events returned for {SNAPSHOT_START} to {today}")

    storage.write_parquet_atomic(list(accumulated.values()), destination)
    return destination
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_ingest.py -v`
Expected: 6 passed

- [ ] **Step 5: Commit**

```bash
git add src/eq/ingest.py tests/test_ingest.py
git commit -m "Add ingest orchestration with outage guarantees"
```

---

### Task 6: Daily revision diff

A full snapshot is 2.83 MB compressed, so committing one daily would cost about 1 GB per year and break the clone-and-reproduce requirement. Snapshots therefore stay local and ephemeral. **The committed artifact is the diff**, which is the actual content of the magnitude-revision-versus-time curve and costs a few KB per day.

**Files:**
- Create: `src/eq/revisions.py`
- Test: `tests/test_revisions.py`

**Interfaces:**
- Consumes: `eq.storage.read_parquet`, `eq.storage.write_parquet_atomic`, `eq.paths`
- Produces: `eq.revisions.TRACKED_FIELDS: tuple[str, ...]`, `eq.revisions.diff_catalogues(previous: list[dict], current: list[dict], observed_at: date) -> list[dict]`, `eq.revisions.write_daily_diff(previous_path, current_path, destination, observed_at: date) -> int`

Each diff record has: `publicid`, `observed_at` (the current snapshot's date), `change_kind` (`"new"`, `"revised"` or `"withdrawn"`), and for revisions the `field`, `old_value` and `new_value` as strings. One row per changed field, so a single event revised in both magnitude and depth produces two rows.

**`observed_at` is not optional and every row must carry it.** The full snapshots are local and disposable; this diff is the only committed artifact. A revision record without a date gives the revision-over-time curve no time axis, which defeats the entire purpose of the module. Pass the date in explicitly rather than parsing it out of a filename.

- [ ] **Step 1: Write the failing test**

Create `tests/test_revisions.py`:

```python
from datetime import date

import pytest

from eq import revisions, storage


def event(pid: str, magnitude: float = 3.5, depth: float = 12.0, status: str = "preliminary") -> dict:
    return {
        "publicid": pid,
        "origintime": "2026-01-01T00:00:00Z",
        "modificationtime": "2026-01-02T00:00:00Z",
        "longitude": 175.0,
        "latitude": -41.0,
        "magnitude": magnitude,
        "depth": depth,
        "magnitudetype": "MLv",
        "depthtype": "",
        "evaluationstatus": status,
        "evaluationmode": "manual",
    }


def test_no_changes_produces_no_rows():
    catalogue = [event("a"), event("b")]
    assert revisions.diff_catalogues(catalogue, catalogue) == []


def test_new_event_is_reported_as_new():
    rows = revisions.diff_catalogues([event("a")], [event("a"), event("b")])
    assert len(rows) == 1
    assert rows[0]["publicid"] == "b"
    assert rows[0]["change_kind"] == "new"


def test_magnitude_revision_is_reported():
    rows = revisions.diff_catalogues([event("a", magnitude=3.4)], [event("a", magnitude=3.6)])
    assert len(rows) == 1
    assert rows[0]["change_kind"] == "revised"
    assert rows[0]["field"] == "magnitude"
    assert rows[0]["old_value"] == "3.4"
    assert rows[0]["new_value"] == "3.6"


def test_multiple_field_changes_produce_one_row_each():
    rows = revisions.diff_catalogues(
        [event("a", magnitude=3.4, depth=12.0)],
        [event("a", magnitude=3.6, depth=33.0)],
    )
    assert {r["field"] for r in rows} == {"magnitude", "depth"}


def test_evaluation_status_change_is_tracked():
    rows = revisions.diff_catalogues(
        [event("a", status="preliminary")], [event("a", status="confirmed")]
    )
    assert rows[0]["field"] == "evaluationstatus"


def test_untracked_field_change_is_ignored():
    before = event("a")
    after = event("a")
    after["modificationtime"] = "2026-06-06T00:00:00Z"
    assert revisions.diff_catalogues([before], [after]) == []


def test_disappeared_event_is_reported():
    rows = revisions.diff_catalogues([event("a"), event("b")], [event("a")])
    assert len(rows) == 1
    assert rows[0]["publicid"] == "b"
    assert rows[0]["change_kind"] == "withdrawn"


def test_write_daily_diff_round_trips(tmp_path):
    previous = tmp_path / "prev.parquet"
    current = tmp_path / "curr.parquet"
    out = tmp_path / "diff.parquet"
    storage.write_parquet_atomic([event("a", magnitude=3.4)], previous)
    storage.write_parquet_atomic([event("a", magnitude=3.9)], current)

    written = revisions.write_daily_diff(previous, current, out)

    assert written == 1
    rows = storage.read_parquet(out)
    assert rows[0]["field"] == "magnitude"
    assert rows[0]["new_value"] == "3.9"


def test_write_daily_diff_with_no_changes_writes_nothing(tmp_path):
    previous = tmp_path / "prev.parquet"
    current = tmp_path / "curr.parquet"
    out = tmp_path / "diff.parquet"
    storage.write_parquet_atomic([event("a")], previous)
    storage.write_parquet_atomic([event("a")], current)

    written = revisions.write_daily_diff(previous, current, out)

    assert written == 0
    assert not out.exists()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_revisions.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'eq.revisions'`

- [ ] **Step 3: Write minimal implementation**

Create `src/eq/revisions.py`:

```python
"""Daily revision diffing.

GeoNet does not expose per-event revision history, so the only way to build a
magnitude-revision-versus-time curve is to snapshot the catalogue daily and diff
consecutive snapshots.

Full snapshots are far too large to commit daily, so they stay local and
ephemeral. The diff is what gets committed: it is the actual content of the
curve and costs a few kilobytes a day.
"""

from __future__ import annotations

from datetime import date
from pathlib import Path

from eq import storage

TRACKED_FIELDS = (
    "magnitude",
    "depth",
    "longitude",
    "latitude",
    "evaluationstatus",
)


def _format(value) -> str:
    """Render a value for stable comparison and storage."""
    if isinstance(value, float):
        return f"{value:g}"
    return str(value)


def diff_catalogues(previous: list[dict], current: list[dict]) -> list[dict]:
    """Compare two catalogue states. One row per new, withdrawn or changed field."""
    before = {row["publicid"]: row for row in previous}
    after = {row["publicid"]: row for row in current}
    rows: list[dict] = []

    for publicid, row in after.items():
        if publicid not in before:
            rows.append(
                {
                    "publicid": publicid,
                    "observed_at": observed_at,
                    "change_kind": "new",
                    "field": "",
                    "old_value": "",
                    "new_value": "",
                }
            )
            continue

        for field in TRACKED_FIELDS:
            old = _format(before[publicid].get(field))
            new = _format(row.get(field))
            if old != new:
                rows.append(
                    {
                        "publicid": publicid,
                        "observed_at": observed_at,
                        "change_kind": "revised",
                        "field": field,
                        "old_value": old,
                        "new_value": new,
                    }
                )

    for publicid in before:
        if publicid not in after:
            rows.append(
                {
                    "publicid": publicid,
                    "observed_at": observed_at,
                    "change_kind": "withdrawn",
                    "field": "",
                    "old_value": "",
                    "new_value": "",
                }
            )

    return rows


def write_daily_diff(
    previous_path: Path, current_path: Path, destination: Path, observed_at: date
) -> int:
    """Diff two snapshot files and write the result. Returns rows written.

    Writes nothing when there are no changes, so a quiet day leaves no file
    rather than an empty one.
    """
    rows = diff_catalogues(
        storage.read_parquet(previous_path),
        storage.read_parquet(current_path),
        observed_at,
    )
    if not rows:
        return 0
    storage.write_parquet_atomic(rows, Path(destination))
    return len(rows)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_revisions.py -v`
Expected: 9 passed

- [ ] **Step 5: Commit**

```bash
git add src/eq/revisions.py tests/test_revisions.py
git commit -m "Add daily catalogue revision diffing"
```

---

### Task 7: Command line entrypoint and first real pull

**Files:**
- Create: `src/eq/cli.py`
- Modify: `pyproject.toml` (add `[project.scripts]`)
- Test: `tests/test_cli.py`

**Interfaces:**
- Consumes: `eq.ingest`
- Produces: `eq.cli.main(argv: list[str] | None = None) -> int`

- [ ] **Step 1: Write the failing test**

Create `tests/test_cli.py`:

```python
from datetime import date

from eq import cli


def test_snapshot_command_invokes_ingest(monkeypatch, tmp_path):
    called = {}

    def fake_snapshot(today, **kwargs):
        called["today"] = today
        return tmp_path / "catalogue.parquet"

    monkeypatch.setattr(cli.ingest, "snapshot_full_catalogue", fake_snapshot)
    exit_code = cli.main(["snapshot", "--date", "2026-08-04"])
    assert exit_code == 0
    assert called["today"] == date(2026, 8, 4)


def test_range_command_invokes_ingest(monkeypatch, tmp_path):
    called = {}

    def fake_range(start, end, min_magnitude, destination, **kwargs):
        called.update(start=start, end=end, mag=min_magnitude, dest=destination)
        return 7

    monkeypatch.setattr(cli.ingest, "ingest_range", fake_range)
    exit_code = cli.main(
        [
            "range",
            "--start", "2025-01-01",
            "--end", "2026-01-01",
            "--min-magnitude", "3.0",
            "--out", str(tmp_path / "x.parquet"),
        ]
    )
    assert exit_code == 0
    assert called["start"] == date(2025, 1, 1)
    assert called["mag"] == 3.0


def test_unknown_command_returns_error():
    assert cli.main(["nonsense"]) != 0
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_cli.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'eq.cli'`

- [ ] **Step 3: Write minimal implementation**

Create `src/eq/cli.py`:

```python
"""Command line entrypoints for the ingest components."""

from __future__ import annotations

import argparse
from datetime import date, datetime, timezone

from eq import ingest


def _as_date(text: str) -> date:
    return datetime.strptime(text, "%Y-%m-%d").date()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="eq", description="Catalogue ingest")
    sub = parser.add_subparsers(dest="command", required=True)

    snap = sub.add_parser("snapshot", help="daily full-catalogue snapshot")
    snap.add_argument("--date", type=_as_date, default=None)

    rng = sub.add_parser("range", help="ingest a single date range")
    rng.add_argument("--start", type=_as_date, required=True)
    rng.add_argument("--end", type=_as_date, required=True)
    rng.add_argument("--min-magnitude", type=float, required=True)
    rng.add_argument("--out", required=True)

    try:
        args = parser.parse_args(argv)
    except SystemExit:
        return 2

    if args.command == "snapshot":
        today = args.date or datetime.now(timezone.utc).date()
        path = ingest.snapshot_full_catalogue(today)
        print(f"wrote {path}")
        return 0

    if args.command == "range":
        count = ingest.ingest_range(
            args.start, args.end, args.min_magnitude, args.out
        )
        print(f"wrote {count} records to {args.out}")
        return 0

    return 2


if __name__ == "__main__":
    raise SystemExit(main())
```

Add to `pyproject.toml` after the `[project.optional-dependencies]` block:

```toml
[project.scripts]
eq = "eq.cli:main"
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_cli.py -v`
Expected: 3 passed

- [ ] **Step 5: Take a real snapshot and verify it landed**

Run:

```bash
python -m eq.cli snapshot --date 2026-08-04
python -c "from eq import storage, paths; r = storage.read_parquet(paths.SNAPSHOT_DIR / 'catalogue-2026-08-04.parquet'); print(len(r), 'records'); print(r[0])"
```

Expected: tens of thousands of records printed, and one sample record showing a `depthtype` field.

- [ ] **Step 6: Commit**

```bash
git add src/eq/cli.py pyproject.toml tests/test_cli.py
git commit -m "Add ingest command line entrypoint"
```

Do **not** commit `data/`. Confirm `.gitignore` covers it before committing; if `data/` is not listed, add it in this commit.

---

### Task 8: dbt project on DuckDB with a staging model

**Files:**
- Create: `dbt/dbt_project.yml`
- Create: `dbt/profiles.yml`
- Create: `dbt/models/staging/stg_quakes.sql`
- Create: `dbt/models/staging/schema.yml`

**Interfaces:**
- Consumes: the parquet written by Task 5 at `data/snapshots/catalogue-*.parquet`
- Produces: dbt model `stg_quakes` with columns `publicid`, `origintime`, `modificationtime`, `longitude`, `latitude`, `magnitude`, `depth`, `magnitudetype`, `depthtype`, `evaluationstatus`, `evaluationmode`, `depth_is_assigned` (boolean)

- [ ] **Step 1: Install dbt**

Run: `python -m pip install "dbt-duckdb>=1.10"`
Expected: installs `dbt-core` and `dbt-duckdb`.

Verify: `dbt --version`

Note: `python -m dbt` does NOT work with dbt-core 1.12, which ships no `dbt/__main__.py`. Use the `dbt` console script. On Windows the Python Scripts directory is often not on PATH, in which case invoke the executable by its full path, or add that directory to PATH.

- [ ] **Step 2: Write the dbt project files**

Create `dbt/dbt_project.yml`:

```yaml
name: eq
version: "1.0.0"
config-version: 2
profile: eq

model-paths: ["models"]
test-paths: ["tests"]
target-path: "target"
clean-targets: ["target", "dbt_packages"]

models:
  eq:
    staging:
      +materialized: view
    marts:
      +materialized: table
```

Create `dbt/profiles.yml`:

```yaml
eq:
  target: dev
  outputs:
    dev:
      type: duckdb
      path: "../data/eq.duckdb"
      threads: 4
      settings:
        # Daily binning depends on this. A non UTC session shifts origin_date
        # for about half the catalogue, silently and with no error.
        TimeZone: 'UTC'
```

Create `dbt/models/staging/stg_quakes.sql`:

```sql
-- Staging view over the newest committed catalogue snapshot.
-- Deduplicates on publicid, keeping the most recently modified record, because
-- GeoNet revises events and a snapshot union can therefore carry an id twice.

with source as (

    select *
    from read_parquet('../data/snapshots/catalogue-*.parquet', union_by_name = true)

),

ranked as (

    select
        publicid,
        origintime,
        modificationtime,
        longitude,
        latitude,
        magnitude,
        depth,
        magnitudetype,
        depthtype,
        evaluationstatus,
        evaluationmode,
        row_number() over (
            partition by publicid
            order by modificationtime desc
        ) as recency_rank
    from source

)

select
    publicid,
    origintime,
    modificationtime,
    longitude,
    latitude,
    magnitude,
    depth,
    magnitudetype,
    depthtype,
    evaluationstatus,
    evaluationmode,
    depthtype = 'operator assigned' as depth_is_assigned
from ranked
where recency_rank = 1
```

Create `dbt/models/staging/schema.yml`:

```yaml
version: 2

models:
  - name: stg_quakes
    description: >
      Deduplicated GeoNet catalogue events. One row per publicid, carrying the
      most recently modified revision seen in any snapshot.
    columns:
      - name: publicid
        description: GeoNet event identifier
        tests:
          - unique
          - not_null
      - name: origintime
        tests:
          - not_null
      - name: magnitude
        tests:
          - not_null
      - name: depth
        tests:
          - not_null
      - name: depth_is_assigned
        description: >
          True when GeoNet fixed the depth to a convention rather than solving
          for it. About 42 percent of events above M3.5 carry an assigned depth,
          so this flag is load bearing and must never be dropped.
        tests:
          - not_null
```

- [ ] **Step 3: Run dbt and verify the model builds**

Run:

```bash
cd dbt && dbt build --profiles-dir . && cd ..
```

Expected: `stg_quakes` builds, and the four column tests pass.

- [ ] **Step 4: Commit**

```bash
git add dbt/dbt_project.yml dbt/profiles.yml dbt/models/staging/
git commit -m "Add dbt project and catalogue staging model"
```

Add `dbt/target/`, `dbt/dbt_packages/`, `dbt/logs/` to `.gitignore` in this commit if not already covered.

---

### Task 9: Data contract tests that fail on upstream change

These are the tests that make the pipeline trustworthy. Each one encodes a fact measured during design, so if GeoNet changes practice the build breaks rather than silently absorbing it.

**Files:**
- Create: `dbt/tests/assert_no_future_events.sql`
- Create: `dbt/tests/assert_magnitude_in_range.sql`
- Create: `dbt/tests/assert_longitude_convention.sql`
- Create: `dbt/tests/assert_depthtype_share.sql`
- Create: `dbt/tests/assert_catalogue_freshness.sql`
- Create: `dbt/tests/assert_no_duplicate_origins.sql`

**Interfaces:**
- Consumes: model `stg_quakes`
- Produces: six singular dbt tests. A dbt singular test passes when it returns zero rows.

- [ ] **Step 1: Write the tests**

Create `dbt/tests/assert_no_future_events.sql`:

```sql
-- An event dated in the future means a clock or parsing fault upstream.
select publicid, origintime
from {{ ref('stg_quakes') }}
where origintime > now()
```

Create `dbt/tests/assert_magnitude_in_range.sql`:

```sql
-- New Zealand has never recorded anything near M10, and negative magnitudes
-- below -1 indicate a parsing fault rather than a real microearthquake.
select publicid, magnitude
from {{ ref('stg_quakes') }}
where magnitude < -1.0 or magnitude > 10.0
```

Create `dbt/tests/assert_longitude_convention.sql`:

```sql
-- Longitude must be on the continuous [163.6, 183.0] convention. A negative
-- value means the antimeridian unwrap was skipped somewhere.
select publicid, longitude
from {{ ref('stg_quakes') }}
where longitude < 163.0 or longitude > 184.0
```

Create `dbt/tests/assert_no_duplicate_origins.sql`:

```sql
-- Two distinct ids at the identical instant and location is a duplication
-- fault, not two earthquakes.
select origintime, latitude, longitude, count(*) as n
from {{ ref('stg_quakes') }}
group by origintime, latitude, longitude
having count(*) > 1
```

Create `dbt/tests/assert_depthtype_share.sql`:

```sql
-- Measured on 2026-08-04: 42 percent of M3.5 and above events carry an
-- operator-assigned depth. This test does not assert that exact figure, it
-- asserts the share stays in a band wide enough to absorb normal variation and
-- narrow enough to catch GeoNet changing practice. If this fails, investigate
-- before adjusting the bounds: the depth data feeding stratum assignment has
-- changed character.
with share as (
    select
        count(*) filter (where depth_is_assigned) * 1.0 / nullif(count(*), 0) as assigned_share
    from {{ ref('stg_quakes') }}
    where magnitude >= 3.5
)
select assigned_share
from share
where assigned_share < 0.20 or assigned_share > 0.65
```

Create `dbt/tests/assert_catalogue_freshness.sql`:

```sql
-- The catalogue must contain a recent event. New Zealand produces several M3.0
-- events per day, so a gap of more than three days means ingest has stalled.
select max(origintime) as newest
from {{ ref('stg_quakes') }}
having max(origintime) < now() - interval 3 day
```

- [ ] **Step 2: Run the tests and verify they pass against real data**

Run:

```bash
cd dbt && dbt test --profiles-dir . && cd ..
```

Expected: all tests PASS.

- [ ] **Step 3: Verify a test actually fails when the contract is broken**

A test that has never failed is not known to work. Prove the freshness test fires.

Temporarily change `interval 3 day` to `interval 0 day` in `dbt/tests/assert_catalogue_freshness.sql`, then run:

```bash
cd dbt && dbt test --profiles-dir . --select assert_catalogue_freshness && cd ..
```

Expected: FAIL. With a zero day window the condition becomes "newest event is older than right now", which is always true for a catalogue of past events, so the test must return a row. Note the direction carefully: widening the interval makes the test PASS, because now() minus a large interval is far in the past. Narrowing it to zero is what makes it fail.

Now change it back to `interval 3 day` and run the same command.

Expected: PASS.

Record both outcomes before moving on. If the first run passed, the test is not wired up and no amount of later green builds means anything.

**A note on why this task matters more than it looks.** The whole project rests on failures being loud. A data contract test that cannot fail is worse than no test, because it manufactures false confidence. This step is the only proof that the contract layer is real.

- [ ] **Step 4: Commit**

```bash
git add dbt/tests/
git commit -m "Add catalogue data contract tests"
```

---

### Task 10: Clean event mart

**Files:**
- Create: `dbt/models/marts/fct_events.sql`
- Create: `dbt/models/marts/schema.yml`

**Interfaces:**
- Consumes: model `stg_quakes`
- Produces: model `fct_events`, the clean table every later phase reads. Columns are those of `stg_quakes` plus `origin_date` (date) and `magnitude_band` (text).

`fct_events` deliberately does **not** carry region membership or depth stratum. Those require the frozen grid and boundary, which Phase 2 produces.

- [ ] **Step 1: Write the model**

Create `dbt/models/marts/fct_events.sql`:

```sql
-- The clean event table. Everything downstream reads this and nothing reads
-- the raw parquet directly.
--
-- Region membership and depth stratum are deliberately absent. They depend on
-- the frozen grid and the fitted depth boundary, which Phase 2 produces. Adding
-- them here would couple this model to decisions that do not exist yet.

select
    publicid,
    origintime,
    -- Pin the timezone explicitly. origintime is TIMESTAMPTZ, and casting one
    -- to a date resolves through the SESSION timezone, which shifts the
    -- calendar date for roughly half the catalogue when that session is not
    -- UTC. This project bins events into daily forecast windows in UTC.
    cast(origintime at time zone 'UTC' as date) as origin_date,
    modificationtime,
    longitude,
    latitude,
    magnitude,
    depth,
    magnitudetype,
    depthtype,
    depth_is_assigned,
    evaluationstatus,
    evaluationmode,
    case
        when magnitude >= 5.0 then 'M5+'
        when magnitude >= 4.0 then 'M4-5'
        when magnitude >= 3.0 then 'M3-4'
        else 'below M3'
    end as magnitude_band
from {{ ref('stg_quakes') }}
```

Create `dbt/models/marts/schema.yml`:

```yaml
version: 2

models:
  - name: fct_events
    description: >
      Clean, deduplicated, validated earthquake events. The single table every
      later phase reads. Region membership and depth stratum are added in
      Phase 2, once the frozen grid and depth boundary exist.
    columns:
      - name: publicid
        tests:
          - unique
          - not_null
      - name: origintime
        tests:
          - not_null
      - name: origin_date
        tests:
          - not_null
      - name: magnitude
        tests:
          - not_null
      - name: depth_is_assigned
        tests:
          - not_null
      - name: magnitude_band
        tests:
          - not_null
          - accepted_values:
              values: ["M5+", "M4-5", "M3-4", "below M3"]
```

- [ ] **Step 2: Build and test**

Run:

```bash
cd dbt && dbt build --profiles-dir . && cd ..
```

Expected: `stg_quakes` and `fct_events` both build, every test passes.

- [ ] **Step 3: Sanity check the output against known figures**

Run:

```bash
python -c "
import duckdb
from eq import paths
con = duckdb.connect(str(paths.DUCKDB_PATH), read_only=True)
print(con.sql('select count(*) as events from fct_events').df())
print(con.sql(\"select magnitude_band, count(*) n from fct_events group by 1 order by 1\").df())
print(con.sql('select round(avg(depth_is_assigned::int), 3) as assigned_share from fct_events where magnitude >= 3.5').df())
"
```

Expected: `assigned_share` near 0.42, matching the figure measured during design and recorded in `DECISIONS.md` section D4. If it differs by more than a few points, stop and investigate before continuing.

- [ ] **Step 4: Commit**

```bash
git add dbt/models/marts/
git commit -m "Add clean event mart"
```

---

### Task 11: CI workflow and end-to-end verification

**Files:**
- Create: `.github/workflows/ci.yml`
- Create: `docs/runbook.md`

**Interfaces:**
- Consumes: everything above
- Produces: a CI job that runs pytest and the full dbt build on every push

- [ ] **Step 1: Write the workflow**

Create `.github/workflows/ci.yml`:

```yaml
name: ci

on:
  push:
    branches: [main]
  pull_request:
  workflow_dispatch:

jobs:
  test:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4

      - uses: actions/setup-python@v5
        with:
          python-version: "3.12"

      - name: Install
        run: |
          python -m pip install --upgrade pip
          python -m pip install -e ".[dev]"

      - name: Unit tests
        run: python -m pytest -v

      - name: Ingest a recent catalogue slice
        run: |
          START=$(date -u -d '30 days ago' +%Y-%m-%d)
          END=$(date -u -d 'tomorrow' +%Y-%m-%d)
          python -m eq.cli range \
            --start "$START" --end "$END" \
            --min-magnitude 3.0 \
            --out data/snapshots/catalogue-ci.parquet

      - name: dbt build
        working-directory: dbt
        run: dbt build --profiles-dir .
```

- [ ] **Step 2: Write the runbook**

Create `docs/runbook.md`:

```markdown
# Runbook

Plain instructions for running this pipeline by hand, written so that six
months from now nothing has to be reconstructed from memory.

## One-time setup

```bash
python -m venv .venv
.venv/Scripts/activate        # Windows
python -m pip install -e ".[dev]"
```

## Take a catalogue snapshot

```bash
python -m eq.cli snapshot
```

Writes `data/snapshots/catalogue-YYYY-MM-DD.parquet`. Safe to run repeatedly:
the same date overwrites atomically, and a failure leaves the previous file
untouched.

## Ingest a specific range

```bash
python -m eq.cli range --start 2025-01-01 --end 2026-01-01 \
  --min-magnitude 3.0 --out data/raw/2025.parquet
```

## Build and test the models

```bash
cd dbt
dbt build --profiles-dir .
```

## When a dbt test fails

Do not adjust the test bounds to make it pass. Every one of these tests encodes
a fact measured on 2026-08-04 and recorded in `DECISIONS.md`. A failure means
either the pipeline broke or GeoNet changed practice, and both need
understanding before anything is edited.

The most likely genuine failure is `assert_depthtype_share`. About 42 percent of
events above M3.5 carry an operator-assigned depth. If that share moves outside
20 to 65 percent, the depth data feeding stratum assignment has changed
character, which affects Phase 2 onwards.

## Regenerating any figure quoted in the documentation

```bash
python scripts/measurements/<script>.py
```

See `scripts/measurements/README.md` for what each one establishes.
```

- [ ] **Step 3: Run the full suite locally exactly as CI will**

Run:

```bash
python -m pytest -v
cd dbt && dbt build --profiles-dir . && cd ..
```

Expected: all pytest tests pass, all dbt models build, all dbt tests pass.

- [ ] **Step 4: Commit**

```bash
git add .github/workflows/ci.yml docs/runbook.md
git commit -m "Add CI workflow and runbook"
```

---

## Phase 1 Definition of Done

From the spec, section 8:

- [ ] Clean parquet is produced end to end
- [ ] Every dbt test passes
- [ ] The freshness check works, and has been observed failing when deliberately broken
- [ ] Ingest survives a simulated GeoNet outage without writing partial data, proven by `test_outage_raises_and_writes_nothing` and `test_outage_leaves_previous_file_intact`
- [ ] Daily full snapshots have started, so the revision curve can be built forward
- [ ] Consecutive snapshots diff into a committed revision record, since the snapshots themselves are too large to commit

## What Phase 2 picks up

The frozen grid and the fitted depth boundary, applied to `fct_events` to add
region membership and stratum. Then the time-invariant smoothed seismicity
baseline fitted on 2019 onwards, per the fitting-window treatment in the spec.
