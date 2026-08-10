"""How much of an arm's replicate instability the scorer can actually see.

What this measures
-------------------
The enterprise stability gate is defined as **exact structured response agreement across
replicates** (`docs/experiment7-results.md`), so it counts any byte that moves. The
production schema asks the model for `recommendation`, `keyword` and
`call_event_detection`, and **no metric reads any of them**. An arm can therefore fail the
stability gate on churn in free text while every label the scorer consumes holds still.

This module splits an arm's instability into the part the scorer sees and the part it does
not, and names which unscored field moved. The full specification, worked by hand before
this file existed, is `tests/fixtures/STABILITY-HAND-COMPUTED.md`.

The constraint this ships under
--------------------------------
Quoted from that fixture, because it is the reason this module is shaped the way it is:

    We already know from a 2026-08-09 measurement that scoring stability at the label
    level instead of the raw level would flip Qwen 27B's verdict. The direction of that
    change is known IN ADVANCE, which is exactly the situation this repository's rules
    exist to prevent. This diagnostic therefore CANNOT REPLACE the pre-registered gate in
    that comparison. It is an additional recorded number printed BESIDE the gate, and this
    paragraph is quoted in the module that computes it. Anyone proposing to make it the
    gate must do so on its merits, in writing, knowing that the answer is already known.

Accordingly: nothing here returns a verdict, a band, or a pass/fail, and
`tests/test_stability.py` asserts that the verdict path never imports it.

Why the fingerprint comes from the scorer's own code
------------------------------------------------------
`scored_fingerprint` calls `flatten.to_rows` and `records.from_row` rather than reading the
payload directly. RET-04 in the fixture is why: its replicates write the same label into
different slots (`main` only, versus `main`+`secondary`+`third`), and the scorer unions
those slots into a set (`fact_checker.py:873`), so it sees one answer three times. A
lookalike that compared slots would report instability the scorer does not have -- and
would report it in the direction that makes an arm look worse, which is the direction
nobody checks.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from typing import Any, Iterable, Mapping, Sequence

from evalharness.records import from_row
from evalgen.flatten import to_rows

__all__ = [
    "UNSCORED_FIELDS",
    "StabilityError",
    "ItemStability",
    "scored_fingerprint",
    "raw_fingerprint",
    "unscored_fields_that_differ",
    "classify_item",
    "decompose",
]

# The fields the production schema asks for and no metric reads. `keyword` is nested
# inside each product block's main/secondary/third, so it is compared separately from the
# scored parts of the same block.
UNSCORED_FIELDS: tuple[str, ...] = (
    "recommendation",
    "call_event_detection",
    "keyword",
)

_REASON_SLOTS: tuple[str, ...] = ("main", "secondary", "third")


class StabilityError(ValueError):
    """Raised on inputs this module cannot make sense of.

    Reserved for a caller mistake or a defect worth surfacing -- notably identical raw
    text that produces two different scored fingerprints, which means the flatten is not
    deterministic. Never raised for an ordinary bad model response: an unparseable
    replicate is a scored change, recorded, not an error.
    """


@dataclass(frozen=True)
class ItemStability:
    """One item's replicate agreement within one arm."""

    item_id: str
    replicates: int
    observable: bool
    raw_unstable: bool
    scored_unstable: bool
    unscored_changed: tuple[str, ...]

    @property
    def cosmetic_only(self) -> bool:
        """Instability the scorer cannot see. The number this module exists to report."""
        return self.raw_unstable and not self.scored_unstable


def raw_fingerprint(raw_content: str | None) -> str:
    """The model's response text, hashed. This is what the existing gate compares."""
    return hashlib.sha256((raw_content or "").encode("utf-8")).hexdigest()


def scored_fingerprint(
    payload: Mapping[str, Any] | None, item: Any, *, parse_ok: bool
) -> tuple:
    """Everything the scorer reads, and nothing else, via the scorer's own code path.

    Order-insensitive: the merge is on `(call_id, phone, product)` and the reason columns
    are unioned into a set, so neither row order nor slot assignment is information the
    scorer has. `parse_ok` is part of the fingerprint because a replicate that failed to
    parse is a different answer, not a cosmetic difference.
    """
    rows = to_rows(dict(payload) if payload is not None else None, item, parse_ok=parse_ok)
    return tuple(
        sorted(
            (
                record.product or "",
                record.call_result or "",
                tuple(sorted(record.reasons)),
                record.parse_ok,
            )
            for record in map(from_row, rows)
        )
    )


def _keyword_shape(payload: Mapping[str, Any] | None) -> str:
    """Every `keyword` in the payload, canonically ordered, as one comparable string."""
    products = (payload or {}).get("product") or {}
    shape: list[tuple[str, tuple[str, ...]]] = []
    if isinstance(products, Mapping):
        for name, block in sorted(products.items()):
            if not isinstance(block, Mapping):
                continue
            slots = []
            for slot in _REASON_SLOTS:
                cell = block.get(slot)
                slots.append(
                    str(cell.get("keyword", "")) if isinstance(cell, Mapping) else ""
                )
            shape.append((str(name), tuple(slots)))
    return json.dumps(shape, ensure_ascii=False, sort_keys=True)


def unscored_fields_that_differ(
    payloads: Sequence[Mapping[str, Any] | None],
) -> tuple[str, ...]:
    """Which unscored fields moved across replicates. Descriptive, never a classifier.

    Computed independently of `scored_unstable` and reported for scored-unstable items
    too: a `cosmetic_only` item is defined by its scored fingerprint holding still, not by
    which unscored field moved, and deciding the category from the attribution would make
    the attribution unfalsifiable.
    """
    changed: list[str] = []
    for field in UNSCORED_FIELDS:
        if field == "keyword":
            values = {_keyword_shape(payload) for payload in payloads}
        else:
            values = {
                json.dumps((payload or {}).get(field), ensure_ascii=False, sort_keys=True)
                for payload in payloads
            }
        if len(values) > 1:
            changed.append(field)
    return tuple(changed)


def classify_item(
    records: Sequence[Mapping[str, Any]], item: Any
) -> ItemStability:
    """Classify one item's replicates. Reproduces the fixture's decision table.

    `records` are that item's rows from one arm's run log, one per replicate.
    """
    if not records:
        raise StabilityError("classify_item needs at least one replicate record")
    item_id = str(records[0].get("item_id") or getattr(item, "item_id", ""))
    ordered = sorted(records, key=lambda record: record.get("replicate") or 1)
    payloads = [record.get("payload") for record in ordered]

    if len(ordered) < 2:
        # One replicate cannot show disagreement. Counted apart from `stable`, because
        # calling it stable lets a single-replicate run report zero instability.
        return ItemStability(
            item_id=item_id,
            replicates=len(ordered),
            observable=False,
            raw_unstable=False,
            scored_unstable=False,
            unscored_changed=(),
        )

    raw = {raw_fingerprint(record.get("raw_content")) for record in ordered}
    scored = {
        scored_fingerprint(
            record.get("payload"), item, parse_ok=bool(record.get("parse_ok"))
        )
        for record in ordered
    }
    raw_unstable = len(raw) > 1
    scored_unstable = len(scored) > 1
    if scored_unstable and not raw_unstable:
        # Fixture case c6. Identical text cannot parse two ways; if it did, the flatten is
        # non-deterministic and that is a defect to surface rather than a category to file.
        raise StabilityError(
            f"{item_id}: identical raw text produced {len(scored)} different scored "
            "fingerprints. The flatten is not deterministic; this is a defect, not an "
            "instability category."
        )
    return ItemStability(
        item_id=item_id,
        replicates=len(ordered),
        observable=True,
        raw_unstable=raw_unstable,
        scored_unstable=scored_unstable,
        unscored_changed=unscored_fields_that_differ(payloads),
    )


def decompose(
    records: Iterable[Mapping[str, Any]], items: Mapping[str, Any]
) -> dict[str, Any]:
    """Split one arm's instability into what the scorer sees and what it does not.

    `records` is that arm's run log; `items` maps `item_id` to the testset item, which the
    flatten needs to build the ground-truth-shaped skeleton for a failed response.

    Rates are over **observable** items, never over all items. The headline is
    `cosmetic_share`: of the instability the existing gate counts, how much of it the
    scorer cannot see.
    """
    grouped: dict[str, list[Mapping[str, Any]]] = {}
    for record in records:
        item_id = str(record.get("item_id") or "")
        if not item_id:
            raise StabilityError("a run record carries no item_id")
        grouped.setdefault(item_id, []).append(record)

    classified: list[ItemStability] = []
    for item_id, item_records in grouped.items():
        item = items.get(item_id)
        if item is None:
            raise StabilityError(
                f"run log names {item_id!r}, which is not in the testset passed in. Pass "
                "the same testset the run used."
            )
        classified.append(classify_item(item_records, item))

    observable = [entry for entry in classified if entry.observable]
    raw_unstable = [entry for entry in observable if entry.raw_unstable]
    scored_unstable = [entry for entry in observable if entry.scored_unstable]
    cosmetic = [entry for entry in observable if entry.cosmetic_only]

    attribution = {field: 0 for field in UNSCORED_FIELDS}
    for entry in cosmetic:
        for field in entry.unscored_changed:
            attribution[field] += 1

    total_observable = len(observable)
    return {
        "items": len(classified),
        "observable": total_observable,
        "not_observable": len(classified) - total_observable,
        "raw_unstable": len(raw_unstable),
        "scored_unstable": len(scored_unstable),
        "cosmetic_only": len(cosmetic),
        "raw_unstable_rate": (
            len(raw_unstable) / total_observable if total_observable else 0.0
        ),
        "scored_unstable_rate": (
            len(scored_unstable) / total_observable if total_observable else 0.0
        ),
        "cosmetic_only_rate": (
            len(cosmetic) / total_observable if total_observable else 0.0
        ),
        # The headline: of the instability the gate counts, the share the scorer cannot
        # see. `0.0` rather than `nan` on a perfectly stable arm, matching
        # `evidence._with_rates` and `judge.summarize_judgments`.
        "cosmetic_share_of_instability": (
            len(cosmetic) / len(raw_unstable) if raw_unstable else 0.0
        ),
        "unscored_fields_on_cosmetic_items": attribution,
        "scored_unstable_items": sorted(entry.item_id for entry in scored_unstable),
    }
