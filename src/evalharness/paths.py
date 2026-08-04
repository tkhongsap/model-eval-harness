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
    """Return the worktree root containing `path`, or None."""
    try:
        out = subprocess.run(
            ["git", "-C", str(path), "rev-parse", "--show-toplevel"],
            capture_output=True, text=True, timeout=5, check=False,
        )
    except (OSError, subprocess.SubprocessError):  # pragma: no cover
        return None
    root = out.stdout.strip()
    return Path(root).resolve() if out.returncode == 0 and root else None


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

    root = _git_toplevel(path)
    if root is not None:
        raise UnsafeDataDir(
            f"{ENV_DATA_DIR} resolves to {path}, which is inside the git worktree at "
            f"{root}. Refusing: a .gitignore rule protects one checkout and fails "
            "silently when it does not match. Use a directory outside version control."
        )
    return path


def resolve(*parts: str) -> Path:
    """Join a path under the data directory, applying every refusal first."""
    return data_dir().joinpath(*parts)
