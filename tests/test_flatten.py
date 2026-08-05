"""Flattening, checked at the grain first and the values second.

`to_rows` is the one function in `evalgen` whose failure mode is a number. Every other
module either raises or returns something a reviewer can read: a prompt that failed to
assemble raises `PromptError`, a testset whose evidence drifted comes back as a list of
problems, a response that would not parse is an `Outcome` with a name. A grain error
comes back as a CSV with the right columns, the right ids and the wrong number of rows,
and the report it produces looks exactly like the report it should have produced.

So the assertions are ordered accordingly: **row counts before row contents**. Half of
this file would still pass against a `to_rows` that emitted one row per call, which is
precisely why the counts are asserted separately and against the real testset rather
than a hand-built stub. `RET-16` is one call with three products
(`tests/fixtures/testsets/retention_v1.jsonl`); if it ever stops being that, the guard
test at the top fails and says so, instead of the grain tests quietly agreeing with a
one-product fixture.

The two counts that carry the file:

  * a three-product call yields **3** rows;
  * a three-product call that FAILED yields **3** rows, not 0. Dropping a failed arm's
    rows leaves `Coverage.parse_failures` reading zero and `compare.check_coverage`
    with nothing to refuse, so the arm that failed more is compared as though it had
    not. See the module docstring of `evalgen/flatten.py` for the full accounting.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from evalgen.flatten import ROW_KEYS, named_no_product, to_rows  # noqa: E402
from evalgen.testsets import load_testset  # noqa: E402
from evalharness.labelspaces import RETENTION  # noqa: E402
from evalharness.metrics import (  # noqa: E402
    outer_join,
    score_call_result,
    score_product,
    score_reason,
)
from evalharness.records import from_row  # noqa: E402

TESTSET_PATH = ROOT / "tests" / "fixtures" / "testsets" / "retention_v1.jsonl"

# The three-product call, and a plain one-product call to contrast it with.
MULTI = "RET-16"
SINGLE = "RET-01"


@pytest.fixture(scope="module")
def items() -> dict:
    """The real testset, keyed by item id. Not a stub: the grain claim is about it."""
    testset = load_testset(TESTSET_PATH)
    return {item.item_id: item for item in testset.items}


def block(outcome: str | None = "save", **reasons: str) -> dict:
    """One product block in the shape the schema declares.

    `main`/`secondary`/`third` are objects with a `reason` and a `keyword`
    (`schemas/retention.json`), which is the detail `prepare_predictions` exists to
    unwrap (fact_checker.py:607-617). Building them as bare strings here would make
    every test below agree with a `to_rows` that never unwrapped anything.
    """
    out: dict = {}
    if outcome is not None:
        out["retention_outcome"] = outcome
    for column, reason in reasons.items():
        out[column] = {"reason": reason, "keyword": "ตัวอย่างคำพูด"}
    return out


def gt_records(item) -> list:
    """The item's ground truth as Records, on the same merge key the scorer uses."""
    return [
        from_row(
            {
                "call_id": item.call_id,
                "phone_number": item.phone_number,
                "product": row.get("product"),
                "call_result": row.get("call_result"),
                "main": row.get("main"),
                "secondary": row.get("secondary"),
                "third": row.get("third"),
                "parse_ok": True,
            }
        )
        for row in item.gt
    ]


# ---------------------------------------------------------------------------
# The fixture the grain claim rests on
# ---------------------------------------------------------------------------


def test_the_multi_product_item_really_is_a_three_product_call(items):
    """Guard the premise. Every count below is meaningless if this drifts.

    A testset edit that collapsed RET-16 to one product would leave the grain tests
    passing against a one-product call, proving nothing about the behaviour they are
    named after. This fails first and names the file to look in.
    """
    item = items[MULTI]
    products = [row.get("product") for row in item.gt]
    assert len(item.gt) == 3, (
        f"{MULTI} in {TESTSET_PATH.name} no longer has three ground-truth products "
        f"(found {products}). The grain tests in this file are named for a three-"
        "product call; point them at whichever item is now that call, or restore this one."
    )
    assert products == ["Postpaid", "TOL", "TVS"]
    assert len(items[SINGLE].gt) == 1


# ---------------------------------------------------------------------------
# Counts
# ---------------------------------------------------------------------------


def test_three_product_call_yields_three_rows(items):
    """The whole point of the module: one response in, one row per product out."""
    item = items[MULTI]
    payload = {
        "product": {
            "Postpaid": block("save", main="save cost"),
            "TOL": block("churn", main="save cost"),
            "TVS": block("unknown"),
        }
    }

    rows = to_rows(payload, item, parse_ok=True)

    assert len(rows) == 3, (
        "A call naming three products must flatten to three rows: the scorer merges on "
        "(call_id, phone_number, product) (fact_checker.py:1075), so one row per call "
        "silently deletes two thirds of this item from every dimension."
    )
    assert [row["product"] for row in rows] == ["Postpaid", "TOL", "TVS"]
    assert [row["call_result"] for row in rows] == ["save", "churn", "unknown"]


def test_parse_failure_on_a_three_product_call_still_yields_three_rows(items):
    """3, not 0. The failing arm keeps the same rows as the arm that answered.

    Both routes into failure are checked, because they arrive differently: a transport
    error or an empty completion carries no payload at all, while a `schema_violation`
    carries one that must not be read (`outcomes.Classified.payload`).
    """
    item = items[MULTI]
    unreadable = {"product": {"Postpaid": block("save", main="network")}}

    for label, rows in (
        ("no payload", to_rows(None, item, parse_ok=False)),
        ("payload present but parse_ok=False", to_rows(unreadable, item, parse_ok=False)),
    ):
        assert len(rows) == 3, (
            f"{label}: a failed three-product call emitted {len(rows)} row(s). Emitting "
            "fewer leaves Coverage.parse_failures under-counted (metrics.py:159) and "
            "check_coverage with no drift to refuse, so the arm that failed more is "
            "compared as though it had not."
        )
        assert [row["product"] for row in rows] == ["Postpaid", "TOL", "TVS"]
        assert all(row["call_result"] is None for row in rows), label
        assert all(row["parse_ok"] is False for row in rows), label


def test_single_product_call_yields_one_row(items):
    """The contrast case. A grain bug that multiplied rows would show up here."""
    item = items[SINGLE]
    payload = {"product": {"Postpaid": block("save", main="network")}}

    assert len(to_rows(payload, item, parse_ok=True)) == 1
    assert len(to_rows(None, item, parse_ok=False)) == 1


def test_parsed_payload_naming_no_product_falls_back_to_the_ground_truth_grain(items):
    """`parse_ok=True` and nothing to flatten still owes the call its rows.

    `classify(required_keys=("product", ...))` checks that the key is PRESENT, not that
    it holds anything (outcomes.py:344). An empty object, a null-only object and a
    `product` that is not an object at all all parse clean and name no product.

    `parse_ok` stays True on these rows. The response did parse; calling it a parse
    failure would re-derive a flag `outcomes.classify` owns, and would file a model
    that answered nothing under a defect it did not have.
    """
    item = items[MULTI]
    for label, payload in (
        ("empty product object", {"product": {}}),
        ("every block null", {"product": {"Postpaid": None, "TOL": None, "TVS": None}}),
        ("product is not an object", {"product": "Postpaid"}),
        ("no product key at all", {"recommendation": ""}),
    ):
        rows = to_rows(payload, item, parse_ok=True)
        assert len(rows) == 3, f"{label}: expected the 3-product grain, got {len(rows)}"
        assert all(row["call_result"] is None for row in rows), label
        assert all(row["parse_ok"] is True for row in rows), label


def test_null_product_block_is_skipped_but_its_siblings_are_not(items):
    """fact_checker.py:604-605 skips a null block. It does not abandon the call."""
    item = items[MULTI]
    payload = {
        "product": {
            "Postpaid": block("save", main="save cost"),
            "TOL": None,
            "TVS": block("unknown"),
        }
    }

    rows = to_rows(payload, item, parse_ok=True)

    assert [row["product"] for row in rows] == ["Postpaid", "TVS"]


# ---------------------------------------------------------------------------
# Contents
# ---------------------------------------------------------------------------


def test_identifiers_come_from_the_item_never_from_the_payload(items):
    """A model does not get to choose the key it is scored against.

    The payload below carries a plausible-looking `call_id` and `phone_number` at the
    top level. Honouring them would let a hallucinated id join a row nobody asked
    about, or — far likelier — miss the join entirely and be scored as an orphan while
    the real ground-truth row scores FN, counting one mistake twice.
    """
    item = items[SINGLE]
    payload = {
        "call_id": "9999",
        "phone_number": "0899999999",
        "product": {"Postpaid": block("save", main="network")},
    }

    (row,) = to_rows(payload, item, parse_ok=True)

    assert row["call_id"] == item.call_id == "5001"
    assert row["phone_number"] == item.phone_number == "0810000001"


def test_nested_reason_objects_are_unwrapped_to_their_reason(items):
    """`main` is `{"reason": ..., "keyword": ...}`, and only `.reason` is scored.

    Writing the object into the cell would survive `from_row`: `parse_reasons` calls
    `str()` and comma-splits (records.py:57), turning one block into labels like
    `"{'reason': 'network'"` that are in no vocabulary and score as wrong. The row
    would look populated and measure nothing.
    """
    item = items[SINGLE]
    payload = {
        "product": {
            "Postpaid": block("churn", main="network", secondary="save cost", third="other")
        }
    }

    (row,) = to_rows(payload, item, parse_ok=True)

    assert row["main"] == "network"
    assert row["secondary"] == "save cost"
    assert row["third"] == "other"
    assert from_row(row).reasons == frozenset({"network", "save cost", "other"})


def test_missing_or_malformed_reason_blocks_are_blank_not_a_crash(items):
    """Absent, null, a bare string, a list: all read as "no label supplied"."""
    item = items[SINGLE]
    payload = {
        "product": {
            "Postpaid": {
                "retention_outcome": "save",
                "secondary": None,
                "third": "network",  # a string where an object belongs
            }
        }
    }

    (row,) = to_rows(payload, item, parse_ok=True)

    assert row["main"] == ""
    assert row["secondary"] == ""
    assert row["third"] == ""
    assert from_row(row).reasons == frozenset()


def test_missing_retention_outcome_is_a_null_call_result_not_a_crash(items):
    """A block with reasons and no outcome is a wrong answer, not an exception.

    Production's bare `except` (fact_checker.py:632) would turn any raise here into a
    single null row for the WHOLE call, taking the other products with it. The miss has
    to stay local to the block that caused it.
    """
    item = items[SINGLE]
    payload = {"product": {"Postpaid": block(None, main="network")}}

    (row,) = to_rows(payload, item, parse_ok=True)

    assert row["call_result"] is None
    assert row["main"] == "network"
    assert from_row(row).call_result is None


def test_a_product_block_that_is_not_an_object_keeps_its_row(items):
    """One malformed block loses its own labels and nothing else.

    This is where the port is deliberately stricter than production: `.get` on a string
    raises, and production's `except` collapses the entire call to one null row
    (:632-646). Here `TOL` is malformed and `Postpaid` and `TVS` still score.
    """
    item = items[MULTI]
    payload = {
        "product": {
            "Postpaid": block("save", main="save cost"),
            "TOL": "churn",
            "TVS": block("unknown"),
        }
    }

    rows = to_rows(payload, item, parse_ok=True)

    assert [row["product"] for row in rows] == ["Postpaid", "TOL", "TVS"]
    assert rows[1]["call_result"] is None
    assert rows[1]["main"] == ""


def test_product_is_not_lowercased_here(items):
    """One normalisation site, not two.

    `records.from_row` -> `norm_text` folds case on every string column (records.py:96).
    Folding it here as well is not harmless duplication: the two sites drift, and the
    drift is invisible because both spellings still look like labels.
    """
    item = items[SINGLE]
    payload = {"product": {"Postpaid": block("save", main="network")}}

    (row,) = to_rows(payload, item, parse_ok=True)

    assert row["product"] == "Postpaid"
    assert from_row(row).product == "postpaid"


def test_unexpected_product_key_is_emitted_not_swallowed(items):
    """A product the schema forbids still gets a row, and becomes a visible orphan.

    `schemas/retention.json` sets `additionalProperties: false` on `product`, so
    `Prepaid` is a model inventing a key. Dropping it would make the model look like it
    said nothing about the call; keeping it makes the row an orphan prediction in
    `outer_join` (README.md:96-97), which is a fact about the model and belongs in the
    output. Note what it also does NOT do: it does not displace the ground-truth row,
    which still scores FN.
    """
    item = items[SINGLE]
    payload = {"product": {"Prepaid": block("save", main="network")}}

    (row,) = to_rows(payload, item, parse_ok=True)
    assert row["product"] == "Prepaid"

    joined = outer_join(gt_records(item), [from_row(row)])
    orphans = [pred for gt, pred in joined if gt is None]
    unmatched_gt = [gt for gt, pred in joined if pred is None]
    assert [o.product for o in orphans] == ["prepaid"]
    assert [g.product for g in unmatched_gt] == ["postpaid"]


# ---------------------------------------------------------------------------
# The contract with the scorer
# ---------------------------------------------------------------------------


def test_every_row_of_every_item_is_accepted_by_from_row(items):
    """The whole testset, both paths, through the real constructor.

    `from_row` reads `row["call_id"]` by subscript and would `KeyError` on a missing
    column, so this is a genuine check rather than a formality. Both paths are walked
    for all 20 items because the failure path builds its rows in a different function,
    and a missing key there would only ever surface on a run that already went wrong.
    """
    assert len(items) == 20

    for item in items.values():
        payloads = {"product": {row["product"]: block("save", main="network") for row in item.gt}}
        ok_rows = to_rows(payloads, item, parse_ok=True)
        failed_rows = to_rows(None, item, parse_ok=False)

        assert len(ok_rows) == len(item.gt)
        assert len(failed_rows) == len(item.gt)

        for row in ok_rows + failed_rows:
            assert tuple(row) == ROW_KEYS, (
                f"{item.item_id}: row keys {tuple(row)} are not the columns "
                f"records.from_row reads ({ROW_KEYS})."
            )
            record = from_row(row)  # must not raise
            assert record.call_id == item.call_id


def test_row_keys_are_exactly_what_the_scorer_reads():
    """The contract, spelled out once so a silent extra column cannot creep in."""
    assert ROW_KEYS == (
        "call_id",
        "phone_number",
        "product",
        "call_result",
        "main",
        "secondary",
        "third",
        "parse_ok",
    )


def test_failure_rows_are_credited_by_the_product_dimension(items):
    """KNOWN CONSEQUENCE, pinned here so it is measured rather than discovered.

    A failure row carries the ground-truth product name, because the product IS part of
    the merge key (fact_checker.py:1075) and a row without it joins with nothing.
    `metrics._group_products` then sees that product present in the prediction, so an
    arm that returned NOTHING scores a true positive on every product class of this
    call.

    That is the price of keeping the grain. `Coverage.parse_failures` reads 3 here, so
    on THIS route the credit is visibly unearned — but it is only half the accounting,
    and an earlier version of this docstring named it as though it were the whole
    safeguard. The other route reaches the same 1.000 with `parse_failures` at ZERO;
    `test_the_empty_product_arm_is_credited_too_and_parse_failures_stays_zero` measures
    it directly rather than leaving it to be discovered in a report.

    Fixing it means changing the merge key, which is a scorer decision;
    `src/evalharness/` is not this module's to edit. If that ever changes, change this
    assertion deliberately rather than letting it fail and be relaxed.
    """
    item = items[MULTI]
    gt = gt_records(item)
    pred = [from_row(row) for row in to_rows(None, item, parse_ok=False)]

    result = score_product(gt, pred, RETENTION.product)
    scored = {metric.label: metric for metric in result.classes}

    assert scored["postpaid"].tp == 1
    assert scored["tol"].tp == 1
    assert scored["tvs"].tp == 1
    assert result.weighted("recall") == 1.0

    assert result.coverage.parse_failures == 3, (
        "The count that keeps the credit above honest ON THIS ROUTE. If flatten emitted "
        "no rows for a failed call, this would read 0 and a total failure would be "
        "indistinguishable from a clean run."
    )


# ---------------------------------------------------------------------------
# The same credit, reached by the route that leaves Coverage untouched
# ---------------------------------------------------------------------------


def empty_product_payload() -> dict:
    """What an arm returns when it satisfies every required key and names nothing.

    `outcomes.classify(required_keys=("product", "call_event_detection",
    "recommendation"))` checks that a key is PRESENT, not that it holds anything, so
    this is `outcome='ok'` and `parse_ok=True`. Schema-legal too, in the port: nothing
    declared `minProperties` on `product`. That is the whole point of the route.
    """
    return {"product": {}, "call_event_detection": "x", "recommendation": ""}


def test_the_empty_product_arm_is_credited_too_and_parse_failures_stays_zero(items):
    """The second route to product recall 1.000, and the one Coverage cannot see.

    `test_failure_rows_are_credited_by_the_product_dimension` above pins the dead arm,
    whose rows carry `parse_ok=False` and therefore show up in
    `Coverage.parse_failures`. This test pins the arm that PARSED and still named no
    product. `to_rows` routes it to the same ground-truth skeleton — correctly, because
    re-deriving `parse_ok` is exactly what `flatten` refuses to do — so it collects the
    same true positive per ground-truth product while `parse_failures` reads ZERO.

    Measured over the whole 20-item pack rather than one item, because the claim being
    pinned is about a run: an arm answering nothing on every item is arithmetically
    indistinguishable from a perfect one on the product dimension alone.

    The two counters that DO separate them are asserted here beside it, so the blind
    spot is a measurement in the suite rather than a sentence in a docstring:

      * `call_result` and `reason` weighted recall, which are 0.000 and cannot be
        reached by answering nothing;
      * `flatten.named_no_product`, which is True on exactly this route.
    """
    gt = [record for item in items.values() for record in gt_records(item)]
    payload = empty_product_payload()
    pred = [
        from_row(row)
        for item in items.values()
        for row in to_rows(payload, item, parse_ok=True)
    ]

    product = score_product(gt, pred, RETENTION.product)
    call_result = score_call_result(gt, pred, RETENTION.call_result)
    reason = score_reason(gt, pred, RETENTION.reason)

    # The credit, unearned and identical to the dead arm's.
    assert product.weighted("recall") == 1.0
    assert product.weighted("f1") == 1.0

    # And nothing in Coverage moved. This is the assertion the old docstring's
    # safeguard would have failed: parse_failures is not sufficient.
    assert product.coverage.parse_failures == 0, (
        "An arm that named no product leaves NO trace in Coverage on this route. If "
        "this ever reads non-zero, flatten started re-deriving parse_ok and the two "
        "failure routes stopped being distinguishable in the way this suite claims."
    )
    assert call_result.coverage.parse_failures == 0
    assert reason.coverage.parse_failures == 0

    # The counters that actually separate "answered nothing" from "answered right".
    assert call_result.weighted("recall") == 0.0
    assert reason.weighted("recall") == 0.0

    # And the count added for this route specifically.
    assert sum(named_no_product(payload, parse_ok=True) for _ in items) == len(items)


def test_named_no_product_cannot_drift_from_the_route_to_rows_took(items):
    """The counter and the routing read the same predicate, on every shape.

    A counter that says 0 while the skeleton path is taken would be worse than no
    counter, because `report.ArmSummary.answered_nothing` prints it as a finding. So
    the invariant is asserted directly: `named_no_product` is True exactly when a
    `parse_ok=True` payload produced the ground-truth skeleton rather than its own rows.
    """
    item = items[MULTI]
    named_nothing = (
        {"product": {}},
        {"product": {"Postpaid": None, "TOL": None, "TVS": None}},
        {"product": None},
        {"product": "Postpaid"},
        {"product": []},
        {},
    )
    for payload in named_nothing:
        rows = to_rows(payload, item, parse_ok=True)
        assert named_no_product(payload, parse_ok=True) is True, payload
        assert len(rows) == len(item.gt), (
            f"{payload} took the skeleton route, so it must keep the call's grain."
        )
        assert all(row["parse_ok"] is True for row in rows), (
            "The response parsed. Reporting these as parse failures would re-derive a "
            "flag flatten does not own."
        )

    named_something = {"product": {"Postpaid": block("save", main="network")}}
    assert named_no_product(named_something, parse_ok=True) is False
    assert len(to_rows(named_something, item, parse_ok=True)) == 1

    # The failure routes already have a counter (`Coverage.parse_failures`); counting
    # them here as well would make one failure look like two.
    assert named_no_product(None, parse_ok=False) is False
    assert named_no_product({"product": {}}, parse_ok=False) is False
