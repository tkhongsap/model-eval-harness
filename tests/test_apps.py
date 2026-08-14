"""The binding table: what `--app` selects, and what it refuses to select.

The equivalence assertion below is the load-bearing one. `cli._SCORERS` was a hand-written
constant; `AppBinding.scorers` is derived from the contract's declared dimensions and the
application's label space. If those two ever disagree, the refactor that replaced one with
the other changed a number, and this file says so before any report does.
"""

from __future__ import annotations

import sys
from dataclasses import replace
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT / "src") not in sys.path:
    sys.path.insert(0, str(ROOT / "src"))

from evalgen import cli, flatten  # noqa: E402
from evalgen.apps import (  # noqa: E402
    BINDINGS,
    AppBindingError,
    binding,
    build_binding,
)
from evalgen.contracts import (  # noqa: E402
    RETENTION_APPLICATION,
    ContractReference,
    DimensionSpec,
)
from evalgen.decoding import decoding_schema  # noqa: E402
from evalharness.adapters import retention  # noqa: E402
from evalharness.labelspaces import RETENTION, LabelSpace  # noqa: E402
from evalharness.metrics import score_call_result, score_product, score_reason  # noqa: E402

_TESTSETS = ROOT / "tests" / "fixtures" / "testsets"

_IMPLEMENTATIONS = {
    "call_result": score_call_result,
    "reason": score_reason,
    "product": score_product,
}


def _build(spec=RETENTION_APPLICATION, *, label_space=RETENTION, implementations=None, **over):
    kwargs = dict(
        label_space=label_space,
        implementations=_IMPLEMENTATIONS if implementations is None else implementations,
        flatten_rows=flatten.to_rows,
        decoding_transform=decoding_schema,
        default_testset=_TESTSETS / "retention_v1.jsonl",
        default_gt=_TESTSETS / "retention_v1.gt.csv",
        cost_per_correct_dimension="call_result",
        report_title="RETENTION EVAL",
        diagnostics=frozenset({"judge"}),
    )
    kwargs.update(over)
    return build_binding(spec, **kwargs)


# --- the equivalence gate -------------------------------------------------------------

def test_the_retention_binding_reproduces_the_hand_written_scorer_table_exactly():
    """Derived from the contract must equal what was previously typed by hand.

    Compares the whole tuple -- dimension names, the function OBJECTS themselves, and the
    class tuples -- not just the names. A binding that named the three dimensions but
    paired `reason` with `score_product` would satisfy a names-only check and score two
    dimensions wrongly.
    """
    assert binding("retention").scorers == cli._SCORERS


def test_the_retention_binding_reproduces_every_other_constant_it_replaces():
    bound = binding("retention")
    assert bound.schema_path == cli.SCHEMA_PATH
    assert bound.default_testset == cli.DEFAULT_TESTSET
    assert bound.default_gt == cli.DEFAULT_GT
    assert bound.cost_per_correct_dimension == cli.COST_PER_CORRECT_DIMENSION
    assert bound.load_gt is retention.load_csv
    assert bound.flatten_rows is flatten.to_rows


def test_dimension_order_follows_the_contract_not_the_alphabet():
    """The spec's order is the report's order; sorting would silently re-rank it."""
    assert binding("retention").dimension_ids == ("call_result", "reason", "product")


def test_the_registry_holds_every_application_that_can_actually_run():
    assert set(BINDINGS) == {"retention"}


def test_an_unbound_application_is_refused_by_name():
    with pytest.raises(AppBindingError, match="no executable binding"):
        binding("mnp")


# --- the refusals ---------------------------------------------------------------------

def test_a_declared_dimension_with_no_scorer_is_refused():
    """Otherwise the contract claims a dimension the report silently omits."""
    spec = replace(
        RETENTION_APPLICATION,
        dimensions=RETENTION_APPLICATION.dimensions + (DimensionSpec("issue_type", "call"),),
    )
    with pytest.raises(AppBindingError, match="no scorer"):
        _build(spec)


def test_a_scorer_for_an_undeclared_dimension_is_refused():
    """The reverse, and worse: a number appears that the hashed contract never mentions."""
    with pytest.raises(AppBindingError, match="not declared"):
        _build(implementations={**_IMPLEMENTATIONS, "issue_type": score_call_result})


def test_a_dimension_with_an_empty_class_list_is_refused():
    """This is the guard that stops the concrete MNP defect.

    `labelspaces.MNP` carries `product=PRODUCT_CLASSES` while MNP's production scorer has
    no product dimension at all -- `calculate_confusion_matrix` returns two DataFrames,
    not three. When that is corrected to `product=()`, a binding that still declared
    `product` would score one-vs-rest over no classes: every weighted average 0/0, printed
    in the same shape as a real result. Refused here instead.
    """
    hollow = LabelSpace(
        app="retention",
        call_result=RETENTION.call_result,
        reason=RETENTION.reason,
        product=(),
    )
    with pytest.raises(AppBindingError, match="EMPTY class list"):
        _build(label_space=hollow)


def test_a_label_space_for_a_different_application_is_refused():
    """A binding is a spec plus its implementation; mismatched halves are not a binding."""
    other = LabelSpace(app="mnp", call_result=RETENTION.call_result,
                       reason=RETENTION.reason, product=RETENTION.product)
    with pytest.raises(AppBindingError, match="label space is for"):
        _build(label_space=other)


def test_a_missing_schema_file_is_refused_at_binding_time_not_at_call_time():
    spec = replace(
        RETENTION_APPLICATION,
        schema=ContractReference("path", "src/evalgen/schemas/no_such_app.json"),
    )
    with pytest.raises(AppBindingError, match="does not exist"):
        _build(spec)


def test_a_cost_dimension_outside_the_scored_set_is_refused():
    """The ratio would divide by a count the report never prints."""
    with pytest.raises(AppBindingError, match="cost-per-correct"):
        _build(cost_per_correct_dimension="issue_type")


def test_an_adapter_reference_of_the_wrong_kind_is_refused():
    spec = replace(
        RETENTION_APPLICATION,
        adapter=ContractReference("path", "src/evalharness/adapters/retention.py"),
    )
    with pytest.raises(AppBindingError, match="must be an identifier"):
        _build(spec)
