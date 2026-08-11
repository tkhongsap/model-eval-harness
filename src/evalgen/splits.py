"""The tune/holdout split, drawn deterministically and committed as a list of ids.

Why this exists
----------------
`src/evalgen/prompts/manifest.json`'s `phase_two_protocol` requires model-specific prompt
tuning to "use a development slice for tuning and preserve a locked holdout for
comparison". Nothing implemented that, and `EXPERIMENTS.md:401` records what happens
without it: *"Do not tune further on these 22 rows without a holdout. Every edit so far
has been selected using them, so the figures are an upper bound."*

Why a committed id list and not a field on the pack
-----------------------------------------------------
`testsets._item_from_line` refuses an unknown field, by design -- a misspelled field name
is silently dropped data. Adding `"split": "tune"` to the JSONL would therefore break
`load_testset` on all three frozen packs and invalidate every experiment that cites their
shas. The split is drawn from data the pack already carries and stored beside it.

The two rules, and the leakage each one closes
------------------------------------------------
1. **Whole blocks move together.** `retention_v3`'s `long_context` items are 3x and 10x
   dilations of six earlier items -- the same transcript, the same labels, at three
   lengths. Splitting a base from its dilations puts the same call on both sides, so a
   prompt tuned on the base is measured on a copy of itself. The pairing is read from the
   pack's own greppable convention in `mechanism` (`^dilation of RET-\\d+ at \\d+x:`),
   which is where it was deliberately encoded rather than in a twelfth field.

2. **Stratify by family.** A split drawn without it can hand tuning all the easy families
   and hold out only the hard ones, which flatters or punishes the result depending on
   which way it fell.

Deterministic, not random: the draw is a documented traversal, so a reviewer can check the
committed list by reading this file rather than by trusting a seed.

What this cannot fix
---------------------
The holdout is **fresh-eyes at best, not clean**. Every item ships an `expected_failure`
string naming the exact wrong answer a model gives, and the phase-one items were already
used to select the `v9_16_e1` edits. Any figure measured on this holdout is an upper
bound and must be reported as one.
"""

from __future__ import annotations

import re
from typing import Any, Iterable, Mapping, Sequence

__all__ = [
    "DILATION_PATTERN",
    "SplitError",
    "dilation_blocks",
    "draw_split",
    "split_problems",
]

# The convention `retention_v3` encoded its pairing in, rather than adding a field that
# would have broken `load_testset` on two frozen packs.
DILATION_PATTERN = re.compile(r"^dilation of (RET-\d+) at (\d+)x:")


class SplitError(ValueError):
    """Raised when a split cannot be drawn or does not hold its own invariants."""


def dilation_blocks(items: Sequence[Any]) -> dict[str, tuple[str, ...]]:
    """Map every item id to the block it must move with.

    A block is a base item and its dilations. Everything else is a block of one. Returned
    keyed by member id so a caller can ask "what must travel with this item" directly.
    """
    known = {item.item_id for item in items}
    families: dict[str, list[str]] = {}
    for item in items:
        match = DILATION_PATTERN.match(item.mechanism or "")
        if match is None:
            continue
        base = match.group(1)
        if base not in known:
            raise SplitError(
                f"{item.item_id} declares itself a dilation of {base}, which is not in "
                "this pack. The block cannot be kept together."
            )
        families.setdefault(base, []).append(item.item_id)

    # Multi-item blocks first, then singletons via `setdefault`, so a block member can
    # never be overwritten by a block of one and the order `items` arrives in does not
    # matter. Doing it the other way round needs a "have I already seen this as a
    # dilation?" scan of every family on every item, which is what this replaced.
    blocks: dict[str, tuple[str, ...]] = {}
    for base, dilations in families.items():
        members = (base, *sorted(dilations))
        for member in members:
            blocks[member] = members
    for item in items:
        blocks.setdefault(item.item_id, (item.item_id,))
    return blocks


def draw_split(items: Sequence[Any], *, tune_every: int = 3) -> dict[str, list[str]]:
    """Draw the tune/holdout split. Deterministic; no seed, no randomness.

    Blocks are ordered by `(family of the block's first item, item id)` and every
    `tune_every`-th block goes to tuning, which spreads the tune slice evenly through each
    family instead of taking a contiguous run of it.
    """
    if tune_every < 2:
        raise SplitError(
            f"tune_every={tune_every} would put every block (or none) in the tune slice; "
            "a holdout that is empty or the whole pack is not a holdout"
        )
    blocks = dilation_blocks(items)
    family_of = {item.item_id: item.family for item in items}

    seen: set[tuple[str, ...]] = set()
    ordered: list[tuple[str, ...]] = []
    for item in sorted(items, key=lambda i: (family_of[i.item_id], i.item_id)):
        members = blocks[item.item_id]
        if members in seen:
            continue
        seen.add(members)
        ordered.append(members)

    # A multi-item block is its own stratum, not a member of its base item's family.
    # A dilation block spans two families by construction -- the base sits in `clear` or
    # `multislot` or `thai_linguistic`, the dilations in `long_context` -- so striping it
    # inside the base's family decides `long_context`'s balance as a side effect. Drawn
    # that way the six blocks landed 4 tune / 2 holdout and put two thirds of
    # `long_context` in the tune slice. Given as its own stratum they land 2 / 4, and
    # both families come out near the intended third.
    strata: dict[str, list[tuple[str, ...]]] = {}
    for members in ordered:
        key = "dilation-block" if len(members) > 1 else family_of[members[0]]
        strata.setdefault(key, []).append(members)

    tune: list[str] = []
    holdout: list[str] = []
    for stratum in sorted(strata):
        for index, members in enumerate(strata[stratum]):
            (tune if index % tune_every == 0 else holdout).extend(members)
    return {"tune": sorted(tune), "holdout": sorted(holdout)}


def split_problems(
    split: Mapping[str, Iterable[str]], items: Sequence[Any]
) -> list[str]:
    """Every way a split can be wrong, as a list. Returns empty when it holds.

    A list rather than a raise so a caller can report all of them at once, matching
    `testsets.validate`.
    """
    problems: list[str] = []
    tune = list(split.get("tune", []))
    holdout = list(split.get("holdout", []))
    all_ids = {item.item_id for item in items}

    for name, ids in (("tune", tune), ("holdout", holdout)):
        if len(set(ids)) != len(ids):
            problems.append(f"{name} contains a duplicate id")
        unknown = sorted(set(ids) - all_ids)
        if unknown:
            problems.append(f"{name} names ids that are not in the pack: {unknown}")

    overlap = sorted(set(tune) & set(holdout))
    if overlap:
        problems.append(f"tune and holdout overlap on {overlap}")
    missing = sorted(all_ids - set(tune) - set(holdout))
    if missing:
        problems.append(f"items in neither slice: {missing}")
    if not tune or not holdout:
        problems.append("both slices must be non-empty")

    # The leakage rule: no block may straddle the split.
    blocks = dilation_blocks(items)
    tune_set = set(tune)
    for members in {blocks[item_id] for item_id in all_ids}:
        if len(members) == 1:
            continue
        sides = {member in tune_set for member in members}
        if len(sides) > 1:
            problems.append(
                f"dilation block {members} is split across tune and holdout: the same "
                "transcript would appear on both sides"
            )
    return problems
