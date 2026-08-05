"""Entry point for the evalgen CLI. Everything real lives in `src/evalgen/cli.py`.

`sys.path` is edited here rather than solved with an install because this repository
has no `pyproject.toml` (AGENTS.md, "Open items"), so there is nothing to `pip install
-e`. Every test module bootstraps itself the same way -- `sys.path.insert(0, ROOT /
"src")` -- and a script that used a different mechanism would be a second answer to a
question the suite has already answered.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from evalgen.cli import main  # noqa: E402

if __name__ == "__main__":
    raise SystemExit(main())
