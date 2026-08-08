"""The runtime data-directory refusals. These must RAISE, not warn."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

import evalharness.paths as paths_mod  # noqa: E402
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


def test_git_marker_refuses_even_when_git_is_unavailable(monkeypatch, tmp_path):
    """The filesystem evidence is sufficient; Git cannot fail the check open."""
    repo = tmp_path / "repo"
    data = repo / "data"
    data.mkdir(parents=True)
    (repo / ".git").mkdir()
    monkeypatch.setenv(ENV_DATA_DIR, str(data))
    monkeypatch.setattr(
        paths_mod.subprocess,
        "run",
        lambda *args, **kwargs: pytest.fail("marker detection must precede Git"),
    )

    with pytest.raises(UnsafeDataDir, match="inside the git worktree"):
        data_dir()


def test_linked_worktree_git_file_is_refused_without_running_git(monkeypatch, tmp_path):
    repo = tmp_path / "linked-worktree"
    data = repo / "data"
    data.mkdir(parents=True)
    (repo / ".git").write_text("gitdir: /private/control/path\n", encoding="utf-8")
    monkeypatch.setenv(ENV_DATA_DIR, str(data))
    monkeypatch.setattr(
        paths_mod.subprocess,
        "run",
        lambda *args, **kwargs: pytest.fail("a .git file must be detected directly"),
    )

    with pytest.raises(UnsafeDataDir, match="inside the git worktree"):
        data_dir()


def test_missing_git_fails_closed_for_an_unverified_directory(monkeypatch, tmp_path):
    outside = tmp_path / "unverified"
    outside.mkdir()
    monkeypatch.setenv(ENV_DATA_DIR, str(outside))

    def missing_git(*args, **kwargs):
        raise FileNotFoundError("git")

    monkeypatch.setattr(paths_mod.subprocess, "run", missing_git)
    with pytest.raises(UnsafeDataDir, match="Git could not be executed"):
        data_dir()


def test_git_timeout_fails_closed(monkeypatch, tmp_path):
    outside = tmp_path / "unverified"
    outside.mkdir()
    monkeypatch.setenv(ENV_DATA_DIR, str(outside))

    def timed_out(*args, **kwargs):
        raise subprocess.TimeoutExpired(cmd="git", timeout=5)

    monkeypatch.setattr(paths_mod.subprocess, "run", timed_out)
    with pytest.raises(UnsafeDataDir, match="timed out"):
        data_dir()


def test_dubious_ownership_fails_closed(monkeypatch, tmp_path):
    outside = tmp_path / "unverified"
    outside.mkdir()
    monkeypatch.setenv(ENV_DATA_DIR, str(outside))
    monkeypatch.setattr(
        paths_mod.subprocess,
        "run",
        lambda *args, **kwargs: subprocess.CompletedProcess(
            args=["git"],
            returncode=128,
            stdout="",
            stderr="fatal: detected dubious ownership in repository at '/private/repo'",
        ),
    )

    with pytest.raises(UnsafeDataDir, match="dubious ownership"):
        data_dir()


def test_explicit_not_a_repository_remains_safe(monkeypatch, tmp_path):
    outside = tmp_path / "true-eval-data"
    outside.mkdir()
    monkeypatch.setenv(ENV_DATA_DIR, str(outside))
    monkeypatch.setattr(
        paths_mod.subprocess,
        "run",
        lambda *args, **kwargs: subprocess.CompletedProcess(
            args=["git"],
            returncode=128,
            stdout="",
            stderr="fatal: not a git repository (or any parent up to mount point)",
        ),
    )

    assert data_dir() == outside.resolve()


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


def test_parent_traversal_is_refused(monkeypatch, tmp_path):
    outside = tmp_path / "true-eval-data"
    outside.mkdir()
    monkeypatch.setenv(ENV_DATA_DIR, str(outside))

    with pytest.raises(UnsafeDataDir, match="escapes"):
        resolve("..", "escaped.jsonl")


def test_absolute_child_is_refused(monkeypatch, tmp_path):
    outside = tmp_path / "true-eval-data"
    elsewhere = tmp_path / "elsewhere"
    outside.mkdir()
    elsewhere.mkdir()
    monkeypatch.setenv(ENV_DATA_DIR, str(outside))

    with pytest.raises(UnsafeDataDir, match="escapes"):
        resolve(str(elsewhere / "run.jsonl"))


def test_symlink_escape_is_refused(monkeypatch, tmp_path):
    outside = tmp_path / "true-eval-data"
    elsewhere = tmp_path / "elsewhere"
    outside.mkdir()
    elsewhere.mkdir()
    link = outside / "link"
    try:
        link.symlink_to(elsewhere, target_is_directory=True)
    except OSError:
        pytest.skip("symlink creation is unavailable in this environment")
    monkeypatch.setenv(ENV_DATA_DIR, str(outside))

    with pytest.raises(UnsafeDataDir, match="escapes"):
        resolve("link", "run.jsonl")
