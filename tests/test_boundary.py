"""The package boundary, enforced instead of asserted in prose.

AGENTS.md makes one claim about `src/evalharness/` that everything else in this
repository leans on: **"The scoring library makes no model calls at all. It scores
outputs produced elsewhere."** (AGENTS.md, "Service layers do not apply", and again
under Architecture: "the scoring library makes none, deliberately".)

That sentence is not a property of the code as written. It is a property of what the
code *imports*, and it stops being true the moment somebody adds `import openai` to a
module in `src/evalharness/` for a plausible-sounding reason. These two tests turn the
claim into something CI can fail on.

Two deliberate choices in how the check works:

  * **The AST is parsed, never imported.** Importing `evalharness` to inspect
    `sys.modules` would only see imports that actually executed, so a client tucked
    inside a function body or behind `if TYPE_CHECKING` would pass. Parsing sees every
    import statement whether or not it runs.
  * **The walk asserts it found something.** A scanner pointed at a moved or renamed
    directory silently examines zero files, and both assertions below become vacuously
    true. That is the failure `tests/test_requirements.py` documents: a gate that stops
    running is worse than no gate at all. The emptiness check lives inside the walker
    so neither test can forget it.

Known limit, stated rather than implied: a call routed through
`importlib.import_module("openai")` builds its module name at runtime and no static
walk can see it. This gate raises the cost of crossing the boundary by accident; it is
not a sandbox.
"""

from __future__ import annotations

import ast
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
EVALHARNESS = ROOT / "src" / "evalharness"

# Anything that can open a socket or speak HTTP, plus the SDKs that wrap them. A model
# call needs one of these; the scoring library needs none of them. `urllib` and `http`
# are stdlib and therefore free to add without touching requirements.txt, which is
# exactly why they belong on this list rather than a dependency review.
NETWORK_MODULES = frozenset({
    "openai",
    "httpx",
    "requests",
    "urllib",
    "urllib3",
    "socket",
    "http",
    "aiohttp",
    "grpc",
})

# The generator side of this repository. It calls models on purpose, so it may never
# become a dependency of the side that must not.
GENERATOR_PACKAGE = "evalgen"


def _import_sites() -> list[tuple[Path, int, str, int]]:
    """Every import statement under src/evalharness/, as (file, line, module, level).

    `module` is the dotted name exactly as written. `level` is 0 for an absolute
    import and >= 1 for a relative one (`from .records import Record`), a distinction
    the callers below need: a relative name resolves inside the package, so
    `from .http import x` would name "http" while importing nothing of the sort.

    Refuses to return an empty result. Both gates below are "nothing in this set
    appears", which is trivially satisfied by scanning no files at all, so a missing
    or renamed package must fail here rather than pass twice in silence.
    """
    assert EVALHARNESS.is_dir(), (
        f"{EVALHARNESS} does not exist, so both import gates in this file would scan "
        "zero files and pass without checking anything. Point EVALHARNESS at the "
        "scoring library's new location."
    )
    sites: list[tuple[Path, int, str, int]] = []
    for path in sorted(EVALHARNESS.rglob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    sites.append((path, node.lineno, alias.name, 0))
            elif isinstance(node, ast.ImportFrom):
                # `from . import x` leaves module None; the level still matters.
                sites.append((path, node.lineno, node.module or "", node.level))

    assert sites, (
        f"No import statements found under {EVALHARNESS}. Either the package is empty "
        "or this walk is broken; either way the gates below would prove nothing."
    )
    return sites


def _where(path: Path, lineno: int) -> str:
    return f"{path.relative_to(ROOT).as_posix()}:{lineno}"


def test_evalharness_imports_no_network_library():
    """AGENTS.md says the scoring library makes no model calls. This is what makes
    that sentence true, and it is only true while nothing here imports a client.

    The claim is load-bearing well beyond tidiness. It is why `src/evalharness/` needs
    no OpenRouter routing, no API key, and no network egress review, and why the one
    script that does call a model was deliberately kept outside `src/`. A single
    `import openai` in this package would falsify the architecture section without
    changing a line of prose, and nobody would notice until someone went looking.

    Reports the file and line of every offender, because "something imports httpx" is
    not actionable.
    """
    offenders = []
    for path, lineno, module, level in _import_sites():
        if level:
            continue  # relative: resolves inside evalharness, cannot be a client
        top = module.split(".")[0]
        if top in NETWORK_MODULES:
            offenders.append(f"  {_where(path, lineno)}: imports {module!r}")

    assert not offenders, (
        "src/evalharness/ imports a networking library:\n"
        + "\n".join(offenders)
        + "\n\nAGENTS.md states the scoring library makes no model calls at all. That "
        "claim is what excuses this package from OpenRouter routing, key handling and "
        "egress review. If a model call genuinely belongs in this repository, it goes "
        "in src/evalgen/ or scripts/, and the boundary stays where AGENTS.md says it is."
    )


def test_evalharness_never_imports_evalgen():
    """The dependency runs one way: the generator may read the scorer, never the reverse.

    `src/evalgen/` exists to CALL models, so it carries the `openai` client the test
    above forbids. If `evalharness` ever imported it, the network dependency would
    arrive transitively and the check above would keep passing while the claim it
    defends had already stopped being true.

    Same reason the smoke test never imports from `src/`: AGENTS.md documents that
    script as an exception precisely because it is independent, and an exception that
    quietly acquires a dependency is no longer an exception.
    """
    offenders = []
    for path, lineno, module, _level in _import_sites():
        if module.split(".")[0].startswith(GENERATOR_PACKAGE):
            offenders.append(f"  {_where(path, lineno)}: imports {module!r}")

    assert not offenders, (
        "src/evalharness/ imports the generator package:\n"
        + "\n".join(offenders)
        + f"\n\n{GENERATOR_PACKAGE} calls models and depends on the openai client, so "
        "this import would give the scoring library a network dependency at one "
        "remove, leaving test_evalharness_imports_no_network_library green while the "
        "claim it protects is already false. Move the shared code down into "
        "evalharness, or duplicate it; do not import upward."
    )
