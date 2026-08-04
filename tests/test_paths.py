"""The runtime data-directory refusals. These must RAISE, not warn."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from evalharness.paths import ENV_DATA_DIR, UnsafeDataDir, data_dir, resolve  # noqa: E402


def test_unset_is_refused(monkeypatch):
    """No default. A default is a place people put things without deciding to."""
    monkeypatch.delenv(ENV_DATA_DIR, raising=False)
    with pytest.raises(UnsafeDataDir) as exc:
        data_dir()
    assert "no default" in str(exc.value)


def test_blank_is_refused(monkeypatch):
    monkeypatch.setenv(ENV_DATA_DIR, "   ")
    with pytest.raises(UnsafeDataDir):
        data_dir()


def test_inside_a_git_worktree_is_refused(monkeypatch, tmp_path):
    """The refusal that matters. .gitignore protects one checkout; this protects
    against pointing the harness anywhere under version control at all."""
    repo = tmp_path / "repo"
    (repo / "data").mkdir(parents=True)
    subprocess.run(["git", "init", "-q", str(repo)], check=True, capture_output=True)

    monkeypatch.setenv(ENV_DATA_DIR, str(repo / "data"))
    with pytest.raises(UnsafeDataDir) as exc:
        data_dir()
    msg = str(exc.value)
    assert "inside the git worktree" in msg
    assert "outside version control" in msg


def test_the_repo_itself_is_refused(monkeypatch):
    """Concretely: the tempting in-repo location must not work."""
    monkeypatch.setenv(ENV_DATA_DIR, str(ROOT))
    with pytest.raises(UnsafeDataDir) as exc:
        data_dir()
    assert "git worktree" in str(exc.value)


def test_missing_directory_is_refused(monkeypatch, tmp_path):
    monkeypatch.setenv(ENV_DATA_DIR, str(tmp_path / "nope"))
    with pytest.raises(UnsafeDataDir) as exc:
        data_dir()
    assert "does not exist" in str(exc.value)


def test_a_file_is_refused(monkeypatch, tmp_path):
    f = tmp_path / "a.csv"
    f.write_text("x")
    monkeypatch.setenv(ENV_DATA_DIR, str(f))
    with pytest.raises(UnsafeDataDir) as exc:
        data_dir()
    assert "not a directory" in str(exc.value)


def test_a_directory_outside_git_is_accepted(monkeypatch, tmp_path):
    outside = tmp_path / "true-eval-data"
    outside.mkdir()
    monkeypatch.setenv(ENV_DATA_DIR, str(outside))
    assert data_dir() == outside.resolve()
    assert resolve("gt.parquet") == outside.resolve() / "gt.parquet"
