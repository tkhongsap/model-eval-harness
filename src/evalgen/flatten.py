"""One model payload becomes the rows the scorer merges on. This changes the grain.

`prepare_predictions` (fact_checker.py:603-631) is thirty lines and does one thing that
matters: it turns a **call-shaped** response into **product-shaped** rows. The model
answers once per call; the scorer merges on `(call_id, phone_number, product)`
(fact_checker.py:1075) and groups on `(call_id, phone_number)` for the product
dimension (:1080-1081). A call that mentions three products is three scored rows, and
`RET-16` in `tests/fixtures/testsets/retention_v1.jsonl` is exactly that call.

**Why this module is the one to distrust.** A grain error does not raise, does not log,
and does not look wrong. It produces a number. If this function emitted one row per
call instead of one per product, every arm would still score, the CSV would still have
the right columns, and the reason dimension would silently lose two thirds of RET-16.
`tests/test_flatten.py` therefore pins the row COUNT before it pins any value.

The port, line by line
----------------------
  * Iterate `payload["product"].keys()` (:603). The key IS the product label — the
    schema declares `Postpaid`, `TOL`, `TVS`, `unknown` (`schemas/retention.json`) and
    `additionalProperties: false`, but a model may still invent a key, and an invented
    key is emitted rather than dropped. It becomes an orphan prediction downstream
    (`metrics.outer_join`, README.md:96-97), which is a fact about the model. Dropping
    it would hide that fact and make the model look like it merely said nothing.
  * Skip a `None` product block (:604-605). The schema types `network_issue` as
    nullable but not the product blocks; production skips them anyway and so do we.
  * `main`/`secondary`/`third` are **nested objects**, not strings (:607-617). Each is
    `{"reason": ..., "keyword": ...}` and only `.reason` is scored. Reading the object
    itself would put `{'reason': 'network', ...}` in the cell, and `parse_reasons`
    would comma-split that into labels no vocabulary contains.
  * `retention_outcome` is renamed to `call_result` (:622). Same label, two names —
    `testsets.py` documents the same rename from the ground-truth side.

What this module deliberately does NOT do
-----------------------------------------
  * **It does not lowercase.** `records.from_row` -> `norm_text` already folds case on
    every string column. Two normalisation sites is one too many: they drift, and the
    one that drifts is invisible because both outputs still look like labels.
  * **It does not decide `parse_ok`.** That is `outcomes.classify`, and it is passed in.
    `Classified.payload` is populated even on `schema_violation`, and its docstring is
    explicit that it is for reading, not for scoring, unless `parse_ok` — so a payload
    arriving with `parse_ok=False` is not read here at all.
  * **It does not retry.** Nothing in this file re-prompts a model that answered badly.

Failure rows keep the grain
---------------------------
Production's `except` branch (:632-646) emits ONE row with `product=None` for the whole
call, whatever the call contained. We emit **one row per ground-truth product**, from
`item.gt`, with `call_result=None` and empty reasons. This is the row-level shape of
the deviation already on the closed list (CLAUDE.md, "Parse failures are scored rather
than dropped"), not a new one.

Be precise about what emitting zero rows would actually cost, because the obvious
answer is not quite right. All three denominators in `metrics.py` are ground-truth
driven — `score_call_result` filters on `g is not None`, `score_reason` counts the
whole outer join, `score_product` unions the GT groups — so dropping predictions does
**not** shrink them. What it does instead is worse for being quieter:

  * `Coverage.parse_failures` counts `pred` rows with `parse_ok=False`
    (metrics.py:159, :191, :240). Emit nothing and a run where the arm failed on every
    item reports **zero parse failures**.
  * `compare.check_coverage` refuses a comparison whose arms drifted apart, and it
    reads `items_scored`. With the failures dropped, `items_scored` does not move, the
    guard never fires, and the arm that failed more is compared as though it had not.

Measured on the 20-item testset, an arm that fails every item scores
`items_scored = 22 / 22 / 20` either way; dropping its rows only takes `items_joined`
and `parse_failures` from `22 / 22 / 22` to `0 / 0 / 0`.

KNOWN CONSEQUENCE, pinned by a test rather than left to be discovered
---------------------------------------------------------------------
A failure row carries the ground-truth product name, because the product IS part of the
merge key and a row without it joins with nothing. `metrics._group_products` therefore
sees that product present in the prediction, and an arm that answered NOTHING scores a
true positive on every product class. On the same 20-item run above, that arm reports
weighted recall and F1 of **0.000 on call_result, 0.000 on reason and 1.000 on
product**.

`compare.check_coverage` cannot catch it: coverage does not drift, because the rows are
all there. Fixing it properly means changing the merge key or teaching the scorer to
skip these rows, both of which are decisions inside `src/evalharness/`, which is not
this module's to edit.

**Two arms reach that 1.000, and only one of them leaves a trace in `parse_failures`.**
This is the correction to an earlier version of this docstring, which named
`Coverage.parse_failures` as the safeguard and was right about only half the cases:

  * `parse_ok=False` (transport error, refusal, empty, unparseable): the rows carry
    `parse_ok=False`, `Coverage.parse_failures` reads 22 on the 20-item pack, and the
    credit is visibly unearned.
  * `parse_ok=True` and the payload named no product -- `{"product": {}}`, every block
    null, `product` not an object, no `product` key at all: the rows carry
    `parse_ok=True`, because the response DID parse and re-deriving that flag here is
    exactly what this module refuses to do. MEASURED over the real testset: an arm
    answering `{"product": {}, "call_event_detection": "x", "recommendation": ""}` on
    every item is `outcome='ok'` for `classify(required_keys=(...))`, which checks
    presence and not content (`outcomes.py:344`), and scores **product weighted recall
    and F1 = 1.000 with `parse_failures = 0`** -- byte-identical product numbers to the
    dead arm, and nothing in `Coverage` moves at all.

So `parse_failures` is not sufficient, and the counters that actually separate these
three arms are:

  1. **call_result and reason recall**, which are 0.000 for both empty arms and cannot
     be reached by answering nothing;
  2. **the mechanism table** (`report.mechanism_table`), which compares whole answers
     per item rather than one dimension;
  3. **`named_no_product`**, added here for the second case specifically. It is the
     same predicate `to_rows` uses to choose the skeleton, so the count and the routing
     cannot drift, and `report.ArmSummary.answered_nothing` carries it into the report
     as its own line. "Answered nothing" and "answered wrong" are different findings
     and now print as different numbers.

Both routes stay asserted -- `test_failure_rows_are_credited_by_the_product_dimension`
for the dead arm, `test_the_empty_product_arm_is_credited_too_and_parse_failures_stays_zero`
for the one that leaves `Coverage` untouched -- so each is measured rather than met by
surprise in a report.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from evalharness.labelspaces import REASON_COLUMNS

if TYPE_CHECKING:  # pragma: no cover - a type name, never a runtime dependency
    from evalgen.testsets import TestItem

__all__ = ["ROW_KEYS", "named_no_product", "to_rows"]

# Exactly the keys `records.from_row` reads, in the order production writes them
# (fact_checker.py:619-626, minus the three provenance columns this harness records in
# its manifest instead). Named so a test can assert the contract rather than restate it.
ROW_KEYS: tuple[str, ...] = (
    "call_id",
    "phone_number",
    "product",
    "call_result",
    *REASON_COLUMNS,
    "parse_ok",
)


def to_rows(payload: dict | None, item: TestItem, *, parse_ok: bool) -> list[dict]:
    """Flatten one response into one row per product. Never returns an empty list.

    `call_id` and `phone_number` come from `item`, never from the payload. The payload
    is the thing under test; letting it supply its own merge key would let a model that
    hallucinated an id score against a row it invented, or — far likelier — drop out of
    the join entirely and look like an item nobody asked about.

    The non-empty guarantee is the contract the caller depends on: "every item emits
    exactly one row set per replicate, INCLUDING failures". Four routes lead to the
    ground-truth skeleton instead of the payload, and all four are cases where the
    payload named no product:

      1. `parse_ok` is False — the payload must not be read for scoring at all.
      2. `payload` is None — transport error, refusal, empty completion.
      3. `payload["product"]` is missing or is not an object.
      4. Every product block was `None`, or there were none.

    Cases 3 and 4 can happen with `parse_ok=True`: `classify(required_keys=("product",
    ...))` checks that a key is PRESENT, not that it holds anything usable. Those rows
    carry `parse_ok=True` unchanged, because the response did parse — reporting them as
    parse failures would re-derive a flag this module does not own, and would file a
    model that answered nothing under a defect it did not have.
    """
    if not parse_ok or payload is None:
        return _gt_skeleton(item, parse_ok=parse_ok)

    blocks = _product_blocks(payload)
    if not blocks:
        return _gt_skeleton(item, parse_ok=parse_ok)
    return [_row(item, name, block, parse_ok=parse_ok) for name, block in blocks]


def _product_blocks(payload: dict) -> list[tuple[Any, Any]]:
    """The product entries worth a row: an object under `product`, minus null blocks.

    Cases 3 and 4 of `to_rows` collapse to "this returned nothing". Written once and
    read from both `to_rows` and `named_no_product` so the row that gets emitted and
    the count that reports it cannot disagree -- a counter that says 0 while the
    skeleton path is taken would be worse than no counter, because it would be read.
    """
    products = payload.get("product")
    if not isinstance(products, dict):
        return []
    return [
        (name, block)
        for name, block in products.items()
        if block is not None  # fact_checker.py:604-605
    ]


def named_no_product(payload: dict | None, *, parse_ok: bool) -> bool:
    """The response parsed and still named no product. See KNOWN CONSEQUENCE above.

    True exactly when `to_rows` falls through to the ground-truth skeleton DESPITE
    `parse_ok` being True: an empty `product` object, one holding only nulls, a
    `product` that is not an object, or no `product` key at all.

    False for `parse_ok=False` and for a missing payload. Those already have a counter
    -- `Coverage.parse_failures` -- and counting them twice would make one failure look
    like two. This exists precisely because that counter reads ZERO on this route while
    the product dimension still pays out a true positive per ground-truth product.

    Deliberately NOT a row column. `ROW_KEYS` is the exact set `records.from_row` reads
    and `test_row_keys_are_exactly_what_the_scorer_reads` pins it; a column the scorer
    ignores would be a fifth thing to keep in step for no gain. The caller
    (`cli.arm_summary`) has the payloads in hand and counts them there.
    """
    if not parse_ok or payload is None:
        return False
    return not _product_blocks(payload)


def _row(item: TestItem, product: Any, block: Any, *, parse_ok: bool) -> dict:
    """One product block becomes one row (fact_checker.py:619-626).

    A block that is not an object still gets a row. Production would raise on
    `product_result.get(...)` and its bare `except` would collapse the ENTIRE call to a
    single null row (:632-646), losing the other products with it. Here the malformed
    block loses its own labels and nothing else: the product key is still what the
    model said, and the empty cells score as the misses they are.
    """
    if not isinstance(block, dict):
        return _blank_row(item, product, parse_ok=parse_ok, empty="")

    return {
        "call_id": item.call_id,
        "phone_number": item.phone_number,
        "product": product,
        # Absent means "no label", which `norm_text` also reads out of production's ""
        # (:622). None is this repository's spelling of it — `testsets._cell` and
        # `records.norm_text` both map blank to None — so the two sides of a comparison
        # write the missing outcome the same way.
        "call_result": block.get("retention_outcome"),
        **{column: _reason(block, column) for column in REASON_COLUMNS},
        "parse_ok": parse_ok,
    }


def _reason(block: dict, column: str) -> str:
    """Read `main`/`secondary`/`third` -> `.reason` (fact_checker.py:607-617).

    Returns "" for absent, null, non-object and non-string, exactly as production
    returns "" for the first two. The last two are where this is stricter than a
    faithful port: `str()`-ing a list or a dict into the cell would manufacture labels
    like `"['network'"` after `parse_reasons` comma-splits it (records.py:57), and a
    label in no vocabulary is noise dressed as a prediction. "" reads as "the model
    supplied no label here", which is what happened.
    """
    nested = block.get(column)
    if not isinstance(nested, dict):
        return ""
    reason = nested.get("reason")
    return reason if isinstance(reason, str) else ""


def _gt_skeleton(item: TestItem, *, parse_ok: bool) -> list[dict]:
    """One empty row per ground-truth product, so the call keeps its grain.

    `item.gt` is the authority on how many rows this call is worth: `testsets.py`
    defines it as "one row per product mentioned in the call", and `validate` refuses
    an item whose `gt` is empty or whose products repeat. Reading the count from
    anywhere else would let a failed response decide its own denominator.

    An item with no `gt` at all still emits one row, with `product=None` — production's
    own failure shape (:634-646). It is unscorable, and that is the point: a call that
    silently contributed nothing is indistinguishable from a call that was never run.
    """
    if not item.gt:
        return [_blank_row(item, None, parse_ok=parse_ok, empty=None)]
    return [
        _blank_row(item, gt_row.get("product"), parse_ok=parse_ok, empty=None)
        for gt_row in item.gt
    ]


def _blank_row(item: TestItem, product: Any, *, parse_ok: bool, empty: str | None) -> dict:
    """A row with the keys and the merge key filled in, and no labels.

    `empty` is "" on the payload path and None on the failure path, matching the two
    branches of production (:624-626 vs :639-641). Both normalise to "no label" through
    `parse_reasons`, so the distinction never reaches a score.

    **It is not observable anywhere today, and an earlier version of this docstring
    claimed otherwise** -- that it "survives only so a raw predictions CSV shows which
    branch wrote the row". No such CSV is written: no module under `src/` calls
    `csv.DictWriter` or `writerow`, `cli.replicate_records` is the only consumer of
    `to_rows` and pipes each dict straight into `from_row`, and `cli.write_run_log`
    writes `ItemResult` records, which never contain flattened rows. The distinction is
    discarded in memory one call later.

    It survives for fidelity to production's two branches and for nothing else. Kept
    rather than collapsed because a port that quietly merges two branches is harder to
    check against the source than one that keeps them, and this file's whole claim is
    that it can be read next to `fact_checker.py:603-646`. A reader who wants the
    branch on disk has to write the exporter first; believing the old sentence would
    have meant looking for an artifact that does not exist.
    """
    return {
        "call_id": item.call_id,
        "phone_number": item.phone_number,
        "product": product,
        "call_result": None,
        **{column: empty for column in REASON_COLUMNS},
        "parse_ok": parse_ok,
    }
