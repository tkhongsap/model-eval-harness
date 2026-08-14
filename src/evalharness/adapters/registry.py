"""Resolve an adapter named by an application contract, and refuse anything else.

Why resolution rather than a table
----------------------------------
`ApplicationSpec.adapter` (`src/evalgen/contracts.py`) already names its loader as a
string -- ``"evalharness.adapters.retention:load_csv"`` -- and that string is already
inside `application_contract_sha`, which every run records and `load_run` verifies. So
the adapter a run used is *already* provenance. What was missing was anything that reads
it: `cli.py` imported the concrete `retention.load_csv` directly, which meant the hashed
reference described the loader and a separate line chose it.

A second app-keyed dict here would have the same defect in a new place. Two tables can
disagree, and the one that decides behaviour would be the one nobody hashed. So this
module resolves *the hashed string itself*: if the reference and the loader disagree, the
run does not start.

Why the string and not the object
---------------------------------
`ContractReference` lives in `src/evalgen/contracts.py`, and
`tests/test_boundary.py::test_evalharness_never_imports_evalgen` forbids importing it
here -- correctly, because that import would run the boundary backwards. The caller in
`evalgen` unpacks the reference and passes the identifier; this module knows only how to
turn an identifier into a callable, and how to refuse.

What it refuses, and why each one
---------------------------------
* **A module outside `evalharness.adapters.`** -- the reference is read from a JSON
  contract, and an unrestricted `import_module` on a contract-supplied string is an
  arbitrary-import primitive. ``"os:system"`` is a valid identifier by shape.
* **A missing attribute** -- otherwise a typo resolves to `None` at the call site and the
  first symptom is a run that loaded no ground truth.
* **A non-callable attribute** -- same reason, one step later.
* **A malformed identifier** -- exactly one colon, both halves non-empty.

Nothing here validates that the loader returns `Record`s. That is not knowable without
calling it, and calling an arbitrary function to find out is the thing this module exists
to avoid; `cli`'s preflight already fails loudly on a ground truth it cannot use.
"""

from __future__ import annotations

import importlib
from typing import Any, Callable

__all__ = ["AdapterError", "ADAPTER_MODULE_PREFIX", "resolve_adapter"]

# Adapters are the one place app-specific parsing is allowed to live
# (`adapters/__init__.py`), so the allowlist is that package and nothing else.
ADAPTER_MODULE_PREFIX = "evalharness.adapters."


class AdapterError(ValueError):
    """Raised when an application contract names an adapter that cannot be trusted."""


def resolve_adapter(identifier: str) -> Callable[..., Any]:
    """Turn ``"evalharness.adapters.retention:load_csv"`` into that function.

    `identifier` is the ``value`` of an ``ApplicationSpec.adapter`` reference whose
    ``kind`` is ``"identifier"``. The caller checks the kind; this checks everything else.
    """
    if not isinstance(identifier, str) or identifier.count(":") != 1:
        raise AdapterError(
            f"adapter reference {identifier!r} must be exactly "
            '"<module>:<attribute>", for example '
            '"evalharness.adapters.retention:load_csv"'
        )

    module_name, _, attribute = identifier.partition(":")
    if not module_name or not attribute:
        raise AdapterError(
            f"adapter reference {identifier!r} has an empty module or attribute"
        )

    if not module_name.startswith(ADAPTER_MODULE_PREFIX):
        raise AdapterError(
            f"adapter module {module_name!r} is outside {ADAPTER_MODULE_PREFIX!r}. "
            "Application contracts are data, and resolving an arbitrary module named by "
            "data would let a contract import anything. Put the loader in "
            "src/evalharness/adapters/ and name it there."
        )

    try:
        module = importlib.import_module(module_name)
    except ImportError as exc:
        raise AdapterError(
            f"adapter module {module_name!r} cannot be imported: {exc}"
        ) from exc

    try:
        resolved = getattr(module, attribute)
    except AttributeError as exc:
        raise AdapterError(
            f"adapter module {module_name!r} has no attribute {attribute!r}"
        ) from exc

    if not callable(resolved):
        raise AdapterError(
            f"{identifier!r} resolved to {type(resolved).__name__}, which is not callable"
        )
    return resolved
