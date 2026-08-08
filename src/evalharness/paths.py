"""Where data may live, enforced at runtime rather than by convention.

`.gitignore` is the second line of defence, not the first. It protects one checkout
of one repository, and it is silently wrong the moment someone points the harness at
a path the rules do not cover. So the harness refuses:

  * if EVAL_HARNESS_DATA_DIR is unset (no default: a default is a place people put
    things without deciding to), and
  * if it resolves INSIDE any git worktree, because ground-truth workbooks carry
    customer phone numbers and verbatim call content, and git history is permanent.

This project's entire justification is data residency. A harness that made it easy
to commit customer data would undercut the argument it exists to support.
"""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

ENV_DATA_DIR = "EVAL_HARNESS_DATA_DIR"


class UnsafeDataDir(RuntimeError):
    """Raised when the configured data directory is missing or unsafe."""


def _git_toplevel(path: Path) -> Path | None:
    """Return the worktree root containing ``path``, or ``None`` when proven absent.

    A failed Git probe is not evidence that a directory is outside version control.
    Missing executables, timeouts, ownership refusals, and other fatal errors therefore
    fail closed.  The one accepted non-zero result is Git's ordinary, explicit
    ``not a git repository`` response.
    """
    try:
        out = subprocess.run(
            ["git", "-C", str(path), "rev-parse", "--show-toplevel"],
            capture_output=True, text=True, timeout=5, check=False,
        )
    except subprocess.TimeoutExpired as exc:
        raise UnsafeDataDir(
            f"cannot verify whether {path} is outside version control: the Git "
            "worktree check timed out. Refusing rather than treating an unknown "
            "location as safe."
        ) from exc
    except OSError as exc:
        raise UnsafeDataDir(
            f"cannot verify whether {path} is outside version control: Git could "
            f"not be executed ({type(exc).__name__}: {exc}). Refusing rather than "
            "treating an unknown location as safe."
        ) from exc
    except subprocess.SubprocessError as exc:  # pragma: no cover - defensive API guard
        raise UnsafeDataDir(
            f"cannot verify whether {path} is outside version control: the Git "
            f"worktree check failed ({type(exc).__name__}: {exc})."
        ) from exc
    root = out.stdout.strip()
    if out.returncode == 0:
        if not root:
            raise UnsafeDataDir(
                f"cannot verify whether {path} is outside version control: Git "
                "reported success without a worktree root."
            )
        return Path(root).resolve()

    diagnostic = "\n".join((out.stderr, out.stdout)).strip()
    if "not a git repository" in diagnostic.casefold():
        return None
    detail = diagnostic.splitlines()[0] if diagnostic else f"exit status {out.returncode}"
    raise UnsafeDataDir(
        f"cannot verify whether {path} is outside version control: Git refused or "
        f"failed the worktree check ({detail}). Refusing rather than treating an "
        "unknown location as safe."
    )


def _git_marker_root(path: Path) -> Path | None:
    """Find a ``.git`` directory/file in ``path`` or one of its ancestors.

    Linked worktrees and submodules use a ``.git`` *file*, while ordinary worktrees use
    a directory.  Checking the marker independently means a missing or ownership-blocked
    Git executable cannot turn an obvious worktree into an approved data directory.
    """
    for candidate in (path, *path.parents):
        marker = candidate / ".git"
        try:
            marker.lstat()
        except FileNotFoundError:
            continue
        except OSError as exc:
            raise UnsafeDataDir(
                f"cannot inspect {marker} while checking whether {path} is outside "
                f"version control ({type(exc).__name__}: {exc}). Refusing rather than "
                "treating an unknown location as safe."
            ) from exc
        return candidate
    return None


def data_dir() -> Path:
    """Resolve the data directory, refusing anything unsafe.

    Deliberately raises rather than falling back. A fallback is how customer data
    ends up somewhere nobody chose.
    """
    raw = os.environ.get(ENV_DATA_DIR)
    if not raw or not raw.strip():
        raise UnsafeDataDir(
            f"{ENV_DATA_DIR} is not set and has no default. Point it at a directory "
            "OUTSIDE any git repository, for example C:\\true-eval-data. Ground-truth "
            "workbooks carry customer phone numbers and call content; git history is "
            "permanent, so there is no safe in-repo location."
        )

    path = Path(raw).expanduser().resolve()
    if not path.exists():
        raise UnsafeDataDir(f"{ENV_DATA_DIR} points at {path}, which does not exist.")
    if not path.is_dir():
        raise UnsafeDataDir(f"{ENV_DATA_DIR} points at {path}, which is not a directory.")

    # The filesystem marker is the independent first line of defence. Git itself is
    # still consulted to cover worktrees discovered through configuration/environment
    # rather than a conventional ancestor marker.
    root = _git_marker_root(path) or _git_toplevel(path)
    if root is not None:
        raise UnsafeDataDir(
            f"{ENV_DATA_DIR} resolves to {path}, which is inside the git worktree at "
            f"{root}. Refusing: a .gitignore rule protects one checkout and fails "
            "silently when it does not match. Use a directory outside version control."
        )
    return path


def resolve(*parts: str) -> Path:
    """Resolve a path *inside* the approved data directory.

    Joining alone is not containment: ``resolve("..", "repo", "run.jsonl")`` and an
    absolute child both discard the safety property established by :func:`data_dir`.
    Resolve the final candidate (including symlinks/junctions where they exist) and
    require it to remain below the approved root.
    """
    root = data_dir()
    candidate = root.joinpath(*parts).resolve(strict=False)
    if candidate != root and root not in candidate.parents:
        raise UnsafeDataDir(
            f"resolved artifact path {candidate} escapes the approved data directory "
            f"{root}. Absolute children, '..' traversal, and symlink escapes are "
            "refused."
        )
    return candidate
