"""The adapter registry: resolve what the contract already hashed, refuse the rest.

`ApplicationSpec.adapter` has named its loader since the contract layer was written, and
that name is inside `application_contract_sha`. Nothing read it -- `cli.py` imported
`retention.load_csv` directly, so the hashed reference described the loader while a
separate line chose it. These tests cover the module that closes that gap, and each
refusal is here because the alternative failure is silent.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT / "src") not in sys.path:
    sys.path.insert(0, str(ROOT / "src"))

from evalgen.contracts import RETENTION_APPLICATION  # noqa: E402
from evalharness.adapters import retention  # noqa: E402
from evalharness.adapters.registry import (  # noqa: E402
    ADAPTER_MODULE_PREFIX,
    AdapterError,
    resolve_adapter,
)


def test_the_committed_retention_contract_resolves_to_the_loader_it_names():
    """The point of the whole module: the hashed string IS the loader.

    Reads the reference off `RETENTION_APPLICATION` rather than retyping the identifier,
    so if the contract's adapter value is ever edited this test follows it instead of
    passing against a string nothing uses.
    """
    reference = RETENTION_APPLICATION.adapter
    assert reference.kind == "identifier"
    assert resolve_adapter(reference.value) is retention.load_csv


def test_a_resolved_adapter_actually_loads_the_committed_ground_truth():
    """Resolution is worthless if the thing it returns cannot be called."""
    loader = resolve_adapter(RETENTION_APPLICATION.adapter.value)
    rows = loader(ROOT / "tests" / "fixtures" / "retention_gt.csv")
    assert rows and all(row.call_id for row in rows)


@pytest.mark.parametrize(
    "identifier",
    [
        "os:system",
        "subprocess:run",
        "builtins:eval",
        "evalgen.cli:main",
        "evalharness.records:from_row",
    ],
)
def test_a_module_outside_the_adapters_package_is_refused(identifier):
    """A contract is data. Resolving an arbitrary module named by data is an
    arbitrary-import primitive, and `"os:system"` is a well-formed identifier."""
    with pytest.raises(AdapterError, match="outside"):
        resolve_adapter(identifier)


def test_a_missing_attribute_is_refused_rather_than_returning_none():
    """`getattr` with a default would make a typo surface as a run that loaded no
    ground truth, which scores as an arm that answered nothing."""
    with pytest.raises(AdapterError, match="no attribute"):
        resolve_adapter("evalharness.adapters.retention:load_csvv")


def test_a_non_callable_attribute_is_refused():
    with pytest.raises(AdapterError, match="not callable"):
        resolve_adapter("evalharness.adapters.registry:ADAPTER_MODULE_PREFIX")


def test_an_unimportable_module_inside_the_package_is_refused():
    with pytest.raises(AdapterError, match="cannot be imported"):
        resolve_adapter("evalharness.adapters.no_such_app:load_csv")


@pytest.mark.parametrize(
    "identifier",
    [
        "evalharness.adapters.retention.load_csv",  # dot, not colon
        "evalharness.adapters.retention:",           # empty attribute
        ":load_csv",                                  # empty module
        "evalharness.adapters.retention:a:b",        # two colons
        "",
        None,
        42,
    ],
)
def test_a_malformed_reference_is_refused(identifier):
    with pytest.raises(AdapterError):
        resolve_adapter(identifier)


def test_the_prefix_is_the_adapters_package_and_nothing_broader():
    """A prefix of `"evalharness."` would admit every scoring module; `""` would admit
    everything. Pinned so widening it is a visible edit to a test, not a quiet one."""
    assert ADAPTER_MODULE_PREFIX == "evalharness.adapters."


def test_the_prefix_check_is_not_a_substring_match():
    """`"evilevalharness.adapters.x"` must not pass by containing the prefix."""
    with pytest.raises(AdapterError, match="outside"):
        resolve_adapter("evil.evalharness.adapters.retention:load_csv")
