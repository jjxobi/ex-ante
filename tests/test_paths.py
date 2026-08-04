from pathlib import Path

from eq import paths


def test_repo_root_contains_decisions_file():
    assert (paths.REPO_ROOT / "DECISIONS.md").is_file()


def test_data_dirs_are_under_repo_root():
    assert paths.RAW_DIR.is_relative_to(paths.REPO_ROOT)
    assert paths.SNAPSHOT_DIR.is_relative_to(paths.REPO_ROOT)


def test_ensure_dirs_creates_them(tmp_path, monkeypatch):
    monkeypatch.setattr(paths, "RAW_DIR", tmp_path / "raw")
    monkeypatch.setattr(paths, "SNAPSHOT_DIR", tmp_path / "snapshots")
    paths.ensure_dirs()
    assert (tmp_path / "raw").is_dir()
    assert (tmp_path / "snapshots").is_dir()
