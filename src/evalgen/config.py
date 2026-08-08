"""Where the OpenRouter key comes from, and how it is found.

The behaviour here is copied from `scripts/openrouter-smoketest/smoketest.py`: same
search order, same accepted spellings, same `setdefault` precedence. Copied, not
imported, and that is a decision rather than an oversight.

**Why this duplicates the smoke test instead of importing it.** AGENTS.md documents
`scripts/openrouter-smoketest/` as the one deliberate exception to "no model calls in
this repository", and the reason it survives review is that it depends on nothing:
its own `requirements.txt`, its own `.env.example`, and **no import from `src/`**. An
import in either direction ends that. If `src/evalgen/` imported the script, the
script would stop being standalone exploratory tooling and become a library module
that happens to live in `scripts/`, which is exactly the drift the exception was
written to prevent. Roughly forty lines of duplication is the price of keeping a
documented boundary true, and it is a low price.

The two copies are allowed to diverge. The smoke test answers "does this key work at
all"; this module serves real generation runs. Neither is the other's contract.
"""

from __future__ import annotations

import os
from collections.abc import Mapping
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from evalgen.runtime import RuntimeSpec

PACKAGE_DIR = Path(__file__).resolve().parent
REPO_ROOT = PACKAGE_DIR.parent.parent

# Searched in order, playing the role the smoke test's (script dir, repo root) pair
# plays there: a package-local .env overrides the repo-root one rather than the other
# way round, because the first file to define a variable wins (see load_env_file).
# Both are gitignored by the bare `.env` rule in .gitignore, which matches at any depth.
ENV_FILES = (PACKAGE_DIR / ".env", REPO_ROOT / ".env")

# Accepted spellings for the key, in priority order. More than one exists because the
# obvious name is not the only obvious name: a key stored as OPEN_ROUTER_API is a
# perfectly reasonable guess, and failing with "not set" while the key sits in the
# file is a worse outcome than accepting a synonym.
API_KEY_VARS = ("OPENROUTER_API_KEY", "OPEN_ROUTER_API", "OPENROUTER_KEY")


def load_env_file(path: Path) -> bool:
    """Minimal .env loader, so this module needs no dependency beyond `openai`.

    Returns True if the file existed and was read, which lets a caller report which
    files it actually loaded instead of guessing.
    """
    if not path.exists():
        return False
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        # setdefault, not assignment: a real environment variable beats any file, and
        # the first file in ENV_FILES beats later ones. Assignment here would let a
        # stale committed default silently override an exported key.
        os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))
    return True


def find_api_key() -> tuple[str | None, str | None]:
    """Return (value, the variable name it came from), or (None, None).

    The variable name is returned so callers can print which spelling won. When three
    are accepted, "key loaded from OPEN_ROUTER_API" is the difference between a
    two-second diagnosis and a confusing one.
    """
    for name in API_KEY_VARS:
        value = os.environ.get(name, "").strip()
        if value:
            return value, name
    return None, None


def find_runtime_api_key(
    runtime: RuntimeSpec,
    *,
    environ: Mapping[str, str] | None = None,
) -> tuple[str | None, str]:
    """Resolve exactly the credential variable named by ``runtime``.

    Unlike :func:`find_api_key`, this function has no aliases: the selected variable
    is part of a runtime's fingerprint, so trying another spelling would make the
    connection behavior differ from the reviewed manifest.  The secret value is
    returned to the immediate client-construction caller only.  It is never copied
    into ``RuntimeSpec`` or any provenance object.
    """
    source = os.environ if environ is None else environ
    value = source.get(runtime.api_key_env, "").strip()
    return (value or None), runtime.api_key_env
