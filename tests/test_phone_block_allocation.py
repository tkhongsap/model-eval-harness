"""The synthetic phone block's allocation count, measured and held to what the docs claim.

Why this file exists
--------------------
`PHONE_PATTERN` (`testsets.py:135`) is one of the controls keeping customer identifiers out
of this repository, and `test_testset_pack.py` already proves every committed number matches
it. What nothing proved is the **accounting**: how many of the 1000 are spent and where.

That accounting went wrong. `CLAUDE.md`, `AGENTS.md`, `DEVLOG.md` and
`docs/testset-v2-plan.md` all read "100 in use, 900 free, all of them in the original
`08100000xx` hundred" until 2026-08-12, by which point 188 were in use and 88 sat outside
that hundred -- `retention_v3` and `retention_challenge_v1` had each drawn on the widening
without the count following them. Every value was still inside the sanctioned block, so
nothing leaked. What broke was the number that tells a reader whether the block is running
out or drifting, which is the only reason to write the number down at all.

So the census is computed here rather than asserted from memory, and the documents are held
to it. A count nothing verifies is a claim that quietly stops being true -- the same
argument `scripts/run_index.py --check` makes about `RUNS.md`.

The docs are matched on their **number**, not their prose, so rewording a paragraph does not
fail the build but changing the allocation without updating the count does.
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT / "src") not in sys.path:
    sys.path.insert(0, str(ROOT / "src"))

from evalgen.testsets import PHONE_PATTERN  # noqa: E402

TESTSETS = ROOT / "tests" / "fixtures" / "testsets"

# The block is `^0810000[0-9]{3}$` -- 1000 numbers.
BLOCK_CAPACITY = 1000

# Measured 2026-08-12. Update these ONLY together with the documents below, and only
# because a pack was deliberately added -- never to make this test pass.
EXPECTED_IN_USE = 188
EXPECTED_RANGES = (
    (0, 99),     # retention_v1, retention_v2, the four block_* fixtures
    (101, 138),  # retention_v3 phase two (RET-101..138)
    (201, 250),  # retention_challenge_v1 (RTC-*)
)

# Files that state the live figure. Each must contain it as a number.
DOCS_STATING_THE_COUNT = (
    ROOT / "CLAUDE.md",
    ROOT / "AGENTS.md",
    ROOT / "DEVLOG.md",
)


def _allocated() -> list[str]:
    """Every phone number any committed pack uses, across all packs, not one glob."""
    numbers: set[str] = set()
    for pack in sorted(TESTSETS.glob("*.jsonl")):
        for line in pack.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            phone = json.loads(line).get("phone_number")
            if phone is not None:
                numbers.add(phone)
    return sorted(numbers)


def _contiguous(tails: list[int]) -> tuple[tuple[int, int], ...]:
    runs: list[tuple[int, int]] = []
    start = previous = tails[0]
    for value in tails[1:]:
        if value != previous + 1:
            runs.append((start, previous))
            start = value
        previous = value
    runs.append((start, previous))
    return tuple(runs)


def test_every_allocated_number_is_inside_the_sanctioned_block():
    """The control itself. Not redundant with `test_testset_pack.py`: that one globs
    `retention_v*`, so it never sees `retention_challenge_v1` or the `block_*` fixtures."""
    outside = [n for n in _allocated() if not PHONE_PATTERN.fullmatch(n)]
    assert outside == [], (
        f"{len(outside)} committed phone number(s) fall outside {PHONE_PATTERN.pattern}: "
        f"{outside[:5]}. Widening the block is a reviewed change to a data-safety control."
    )


def test_the_allocation_census_matches_what_the_documents_claim():
    """188 in use / 812 free, in three contiguous ranges, and the docs say so."""
    allocated = _allocated()
    assert len(allocated) == EXPECTED_IN_USE, (
        f"{len(allocated)} numbers are allocated, but this test and the project "
        f"documentation say {EXPECTED_IN_USE}. If a pack was added deliberately, update "
        f"EXPECTED_IN_USE, EXPECTED_RANGES and the count in "
        f"{', '.join(p.name for p in DOCS_STATING_THE_COUNT)} together."
    )

    tails = sorted(int(n[7:]) for n in allocated)
    assert _contiguous(tails) == EXPECTED_RANGES, (
        f"the allocated ranges moved: {_contiguous(tails)} against {EXPECTED_RANGES}"
    )

    free = BLOCK_CAPACITY - len(allocated)
    assert free == 812, f"expected 812 free, measured {free}"


def test_the_documents_state_the_measured_count():
    """Matched on the number, not the prose. Rewording is free; drifting is not.

    This is the assertion that would have caught the 2026-08-12 defect: all four documents
    still read "100 in use" long after the widening had been used twice.
    """
    in_use = len(_allocated())
    free = BLOCK_CAPACITY - in_use
    stale = []
    for doc in DOCS_STATING_THE_COUNT:
        text = doc.read_text(encoding="utf-8")
        # The live figure has to appear as "<in_use> in use" or "**188 ... are in use**".
        states_current = re.search(rf"\b{in_use}\b[^.\n]{{0,40}}(in use|are used)", text) or re.search(
            rf"(in use|are used)[^.\n]{{0,40}}\b{in_use}\b", text
        )
        if not states_current:
            stale.append(doc.name)
    assert stale == [], (
        f"{stale} do not state the measured allocation ({in_use} in use, {free} free). "
        "The count is a data-safety control's own bookkeeping: correct the document rather "
        "than relaxing this test."
    )
