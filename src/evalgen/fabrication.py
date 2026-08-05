"""How many reason labels a run invented, and how many of them the prompt handed it.

The measurement `v9_16_e1` exists to move
--------------------------------------
The worked example in `prompts/retention_wrapper.txt` fills all three reason slots --
`main: network`, `secondary: save cost`, `third: dissatisfied service`. Both arms of the
2026-08-04 pair emit those values into slots the ground truth leaves empty, at a rate no
transcript supports. This module counts that, so "the example poisons its own output" is
a number a second person can recompute rather than a claim.

MEASURED baseline, reproduced by `tests/test_fabrication.py` against the two committed
run directories:

    out/runs/20260804-222943Z-incumbent  (gemini-2.5-flash)  42 invented / 18 example (42%)
    out/runs/20260804-224050Z-candidate  (qwen3.6-27b)       30 invented / 19 example (63%)

`save cost` alone is 13 of Qwen's 30.

What "invented" means here, precisely
-------------------------------------
For each prediction row that joins a ground-truth row, and for each slot in
`FABRICATION_SLOTS` whose GROUND-TRUTH cell is blank, every label the model put in that
slot is counted. Labels are comma-split and lowercased exactly as
`evalharness.records.parse_reasons` does, so a cell reading `"network, save cost"` counts
as two -- the scorer would read it as two, and a fabrication counter that disagreed with
the scorer about what a label is would be measuring a third thing.

**`FABRICATION_SLOTS` is `secondary` and `third`, not all three.** Those are the slots
`v9_16_e1` blanks. `main` is counted too, but reported separately as
`main_slot_invented`, because the variant does not touch `main`'s example value and
folding it into the headline would mix a number the experiment can move with a number it
cannot. On the baseline runs `main` contributes 3 (Gemini) and 4 (Qwen), all of them the
label `other` and none of them an example value -- so the headline and the total tell the
same story here, but only by luck, and luck is not a reason to merge two counters.

Positional, not set-based, and the honest caveat
-------------------------------------------------
Blankness is read per slot, not per row. That is the right grain for this experiment --
the claim under test is that a filled example slot makes the model fill that slot -- but
it is NOT the grain the scorer uses. `records.parse_reasons` unions all three cells into
a set and discards rank, so a model that emits a genuinely correct label in the wrong
slot is counted here and not penalised there. `also_present_in_gt_row` is that number,
returned so the headline cannot be quietly over-read as "wrong labels".

Join key
--------
`(call_id, product)`, normalised through `records.norm_text`, **not** the scorer's
three-part `(call_id, phone_number, product)` key -- `run.jsonl` records `call_id` and no
phone number, and inventing one from the testset would make this module depend on a file
its signature does not take. The GT is checked for `(call_id, product)` uniqueness before
anything is counted and raises if it is not unique, so the shortcut cannot silently
double-count a row. Predictions naming a product with no ground-truth row cannot be asked
whether the GT left a slot blank; they are skipped and reported as
`orphan_product_rows` rather than dropped in silence. On both baseline runs that is 3 --
RET-18, where the GT product is `unknown` and both models answered `Postpaid`.

Records with `parse_ok=False` are skipped: `outcomes.Classified.payload` is populated
even on a schema violation and its docstring says that payload is for reading, not for
scoring. On the baseline runs this changes nothing -- no violation payload carried a
product block at all -- but the rule has to be stated, because a variant run with a
different violation count would otherwise move this number for a reason that is not
fabrication.
"""

from __future__ import annotations

import csv
import json
from collections import Counter
from pathlib import Path
from typing import Any

from evalgen.prompts import EXAMPLE_REASON_VALUES
from evalharness.labelspaces import REASON_COLUMNS
from evalharness.records import norm_text

__all__ = [
    "EXAMPLE_REASONS",
    "FABRICATION_SLOTS",
    "FabricationError",
    "fabrication_rate",
]

# The slots `v9_16_e1` blanks. See the module docstring for why `main` is not in here.
FABRICATION_SLOTS: tuple[str, ...] = ("secondary", "third")

# The three values the BASE example fills its slots with, normalised the way a scored
# label is normalised. Taken from `prompts.EXAMPLE_REASON_VALUES` rather than re-typed,
# and from the BASE rather than from whichever prompt a run used: scoring an e1 run
# against e1's own (blanked) example would report 0 example-derived labels by
# construction and would look like a win no matter what the model did.
EXAMPLE_REASONS: frozenset[str] = frozenset(
    norm_text(v) or "" for v in EXAMPLE_REASON_VALUES.values()
)


class FabricationError(ValueError):
    """Raised when the inputs cannot support the count being asked for.

    Loud rather than best-effort. Every failure this raises on -- a missing run log, a
    ground truth without the reason columns, a duplicated join key -- produces a number
    if ignored, and a fabrication rate computed off a broken join is indistinguishable
    from one computed off a good one once it reaches a report.
    """


def fabrication_rate(run_dir: str | Path, gt_path: str | Path) -> dict:
    """Count the reason labels a run put into ground-truth-blank slots.

    Returns a dict with the headline three, scoped to `FABRICATION_SLOTS`:

      * `invented` -- labels emitted into slots the GT leaves blank
      * `example_derived` -- how many of those are one of the base example's three values
      * `rate` -- `example_derived / invented`, and `0.0` when `invented` is 0 (a run
        that invented nothing has no rate; reporting `nan` would print as a defect)
      * `emitted` -- a `Counter` of every label counted, so the headline can be read

    plus the diagnostics the headline must not be read without: `main_slot_invented`,
    `also_present_in_gt_row`, `orphan_product_rows`, `blank_slots_offered`, and the
    record/row counts the join was built from.
    """
    run_dir = Path(run_dir)
    gt = _load_gt(Path(gt_path))

    invented = Counter()
    main_invented = Counter()
    elsewhere = 0
    orphans = 0
    rows = 0
    skipped = 0
    records = 0
    offered = 0

    for record in _read_records(run_dir):
        records += 1
        if not record.get("parse_ok"):
            skipped += 1
            continue
        call_id = str(record.get("call_id", "")).strip()
        for product, block in _product_blocks(record.get("payload")):
            gt_row = gt.get((call_id, norm_text(product)))
            if gt_row is None:
                orphans += 1
                continue
            rows += 1
            for slot in REASON_COLUMNS:
                if gt_row[slot]:  # the GT filled this slot; nothing here is invented
                    continue
                offered += 1 if slot in FABRICATION_SLOTS else 0
                for label in _labels(block, slot):
                    if slot in FABRICATION_SLOTS:
                        invented[label] += 1
                        if label in gt_row["all"]:
                            elsewhere += 1
                    else:
                        main_invented[label] += 1

    total = sum(invented.values())
    example_derived = sum(n for label, n in invented.items() if label in EXAMPLE_REASONS)
    meta = _read_meta(run_dir)

    return {
        "run_dir": str(run_dir),
        "run_id": meta.get("run_id"),
        "arm": meta.get("arm"),
        "model_requested": meta.get("model_requested"),
        "prompt_id": meta.get("prompt_id"),
        # headline, scoped to the slots v9_16_e1 changes
        "slots": FABRICATION_SLOTS,
        "example_values": sorted(EXAMPLE_REASONS),
        "invented": total,
        "example_derived": example_derived,
        "rate": (example_derived / total) if total else 0.0,
        "emitted": invented,
        # diagnostics -- see the module docstring for why each is separate
        "main_slot_invented": sum(main_invented.values()),
        "main_slot_emitted": main_invented,
        "also_present_in_gt_row": elsewhere,
        "blank_slots_offered": offered,
        "rows_joined": rows,
        "orphan_product_rows": orphans,
        "records_read": records,
        "records_skipped_not_parse_ok": skipped,
    }


def _read_records(run_dir: Path) -> list[dict]:
    """Every JSON object in `<run_dir>/run.jsonl`, in file order."""
    path = run_dir / "run.jsonl"
    try:
        raw = path.read_text(encoding="utf-8")
    except FileNotFoundError as exc:
        raise FabricationError(
            f"no run log at {path}. `fabrication_rate` reads the per-replicate records, "
            "not the run.json summary, because the summary carries no payloads."
        ) from exc
    records = []
    for number, line in enumerate(raw.splitlines(), start=1):
        if not line.strip():
            continue
        try:
            records.append(json.loads(line))
        except json.JSONDecodeError as exc:
            raise FabricationError(f"{path}:{number} is not JSON: {exc}") from exc
    if not records:
        raise FabricationError(f"{path} holds no records")
    return records


def _read_meta(run_dir: Path) -> dict:
    """`run.json` if it is there, `{}` if it is not. Provenance, never arithmetic."""
    path = run_dir / "run.json"
    if not path.exists():
        return {}
    try:
        meta = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return {}
    return meta if isinstance(meta, dict) else {}


def _load_gt(path: Path) -> dict[tuple[str, str | None], dict]:
    """The ground truth, keyed `(call_id, product)`, with each row's blank slots resolved.

    Each value carries the three normalised cells plus `all`, the set-union of every
    label in the row -- the same union `records.parse_reasons` builds, used only for the
    `also_present_in_gt_row` diagnostic.
    """
    try:
        text = path.read_text(encoding="utf-8")
    except FileNotFoundError as exc:
        raise FabricationError(f"no ground truth at {path}") from exc

    rows: dict[tuple[str, str | None], dict] = {}
    reader = csv.DictReader(text.splitlines())
    missing = [c for c in ("call_id", "product", *REASON_COLUMNS) if c not in (reader.fieldnames or [])]
    if missing:
        raise FabricationError(
            f"{path} is missing column(s) {missing}; a blank slot cannot be told from an "
            "absent one, and every label would count as invented."
        )
    for row in reader:
        key = (str(row["call_id"]).strip(), norm_text(row["product"]))
        if key in rows:
            raise FabricationError(
                f"{path} has two rows for {key}. This module joins on (call_id, product) "
                "because run.jsonl carries no phone number; a duplicated key would count "
                "one prediction against two ground truths."
            )
        cells = {slot: norm_text(row.get(slot)) for slot in REASON_COLUMNS}
        union: set[str] = set()
        for cell in cells.values():
            union.update(_split(cell))
        rows[key] = {**cells, "all": union}
    if not rows:
        raise FabricationError(f"{path} holds no ground-truth rows")
    return rows


def _product_blocks(payload: Any) -> list[tuple[Any, dict]]:
    """`payload["product"]` entries that are objects. Mirrors `flatten._product_blocks`.

    A null or non-object block carries no reason slots to fabricate into, so it is not a
    row here. `flatten.to_rows` still emits a row for a non-object block, because the
    scorer needs the grain; this module is counting labels, and there are none.
    """
    if not isinstance(payload, dict):
        return []
    products = payload.get("product")
    if not isinstance(products, dict):
        return []
    return [(name, block) for name, block in products.items() if isinstance(block, dict)]


def _labels(block: dict, slot: str) -> list[str]:
    """The labels in one `main`/`secondary`/`third` slot.

    Reads `<slot>.reason` and returns nothing for absent, null, non-object and
    non-string, exactly as `flatten._reason` does -- a list or dict stringified into a
    cell would manufacture labels no vocabulary contains. Comma-splitting and case
    folding follow `records.parse_reasons`.
    """
    nested = block.get(slot)
    if not isinstance(nested, dict):
        return []
    reason = nested.get("reason")
    if not isinstance(reason, str):
        return []
    return _split(reason)


def _split(cell: str | None) -> list[str]:
    """`"network, save cost"` -> `["network", "save cost"]` (records.py:57-60)."""
    if cell is None:
        return []
    return [part.strip().lower() for part in str(cell).split(",") if part.strip()]
