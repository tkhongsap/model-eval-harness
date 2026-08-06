"""Gates on the committed packs themselves, applied to EVERY pack rather than to one.

Why this file exists
--------------------
`retention_v2.jsonl` had **zero** automated validation before it. That was verified
rather than assumed, on 2026-08-06, two ways:

  * `grep -rn "retention_v2" tests/ scripts/ src/ .github/` returns nothing outside the
    fixture directory itself. Ten test modules load `retention_v1.jsonl`
    (`test_cli.py:46`, `test_evidence.py:57`, `test_flatten.py:48`, `test_prompts.py:61`,
    `test_runner.py:66`, `test_report.py:683`, `test_fabrication.py:44`, and others);
    **no test module loads `retention_v2.jsonl`**.
  * `.github/workflows/ci.yml` ran exactly one check step, `pytest tests/ -q -rs`. It
    never ran `evalgen check`, so the one command that does read v2
    (`TESTING.md:303-306`) ran only when a human remembered to type it.

Experiments 3 and 4 were scored on that 100-item pack. So the file carrying the numbers
had no gate, while the 20-item file it extends had ten. This closes that, and closes it
for `retention_v1` too, since none of the seven properties below was asserted anywhere
for either pack.

Discovered, not listed
----------------------
`PACKS` globs `retention_v*.jsonl`. A `retention_v3` added tomorrow is gated the day it
lands, with no edit here — which is the whole failure this file exists to prevent, and
hardcoding two names would rebuild it one version later.

Globbing has its own vacuity failure: a glob that matches nothing parametrises to zero
cases and every test below "passes" without running.
`test_every_committed_pack_is_discovered` is the guard against that, and it is the
reason the glob is allowed to be the source of truth.

What is asserted, and what each one catches that `validate` does not
--------------------------------------------------------------------
`testsets.validate` is the existing checker and test 1 runs it. The other six exist
because `validate` does not ask those questions at all:

  2. reason spans are customer speech  -- `validate` never looks at turn structure.
  3. spans occur EXACTLY once          -- `validate` checks presence only
                                          (`testsets.py:431`, `span not in transcript`).
  4. turns are <= 120 characters       -- asserted NOWHERE in the repo before this file.
  5. every label class has support     -- `validate` checks labels are IN the space,
                                          never that the space is covered BY the pack.
  6. identifier invariants             -- `validate` checks item_id duplication and both
                                          patterns; it does not check that the scored
                                          key `(call_id, phone_number)` is unique.
  7. v2 extends v1 byte-for-byte       -- nothing checked the two packs' relationship.

Two private helpers are imported on purpose
-------------------------------------------
`evidence._turns` and `testsets._labels_in_row` are underscore-private and are imported
anyway. Both encode production semantics that this file must apply identically or not at
all: `_turns` splits on the two measured speaker prefixes, and `_labels_in_row` comma-
splits reason cells the way `fact_checker.py:877` does, which is why a cell reading
`"network, save cost"` is two labels and not one.

Re-implementing either would give the repo a second answer to a question it has already
answered, and the two copies would drift silently -- the exact defect
`testsets.validate` was written to catch, reintroduced in the file checking for it. A
private import that breaks loudly when the helper moves is the cheaper failure.

MEASURED, 2026-08-06, both packs
--------------------------------
Recorded here so a future reader can tell a real regression from a pack that grew:

    retention_v1  20 items   366 turns   22 gt rows   76 evidence spans
    retention_v2  100 items  1807 turns  108 gt rows  369 evidence spans

    speaker prefixes   366/366 and 1807/1807 lines prefixed; 0 unprefixed, 0 blank
    longest turn       107 codepoints in BOTH packs (RET-18, an agent greeting)
    reason spans       32 in v1, 152 in v2; agent-only 0 and 0; and 0 in either pack
                       appear in a customer turn AND an agent turn
    repeated spans     0 in v1, 1 in v2 (RET-85, allowlisted below)
    class support      all 19 retention classes covered in both packs
    call ids           5001-5020 and 5001-5100, all distinct
    phone numbers      100 distinct in v2, all in the original 08100000xx hundred of the
                       widened `PHONE_PATTERN` block, no nulls

The product and call_result dimensions are deliberately NOT held to test 2
-------------------------------------------------------------------------
`prompt.py:4382-4387` requires the *reason* phrase to be customer speech. It says
nothing of the kind about the product or the outcome, and the pack relies on that: 13
`ev_product` spans and 3 `ev_call_result` spans in v2 sit in agent turns only, and are
correct there. `RET-27`'s postpaid evidence is the agent saying
`แพ็กรายเดือนของพี่ได้เน็ตเยอะกว่า`; `RET-57`'s `unknown` outcome is the agent reporting
`สายหลุดไปแล้วครับ ยังไม่ได้ข้อสรุปจากลูกค้าเลยครับ` after the line dropped -- a call
whose outcome is unknown *because* the customer never said anything is a case where
demanding customer speech would be incoherent.

Widening test 2 to all three dimensions would fail 16 correct items and the fix would be
to rewrite the pack. The narrow assertion is the one production licenses.
"""

from __future__ import annotations

import functools
import hashlib
import sys
from collections import Counter
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from evalgen.evidence import _turns  # noqa: E402
from evalgen.testsets import (  # noqa: E402
    CALL_ID_PATTERN,
    PHONE_PATTERN,
    load_testset,
    validate,
)
from evalgen.testsets import _labels_in_row  # noqa: E402
from evalharness.labelspaces import RETENTION  # noqa: E402

TESTSETS = ROOT / "tests" / "fixtures" / "testsets"

# One app per file (`testsets.py:22-25`), and the glob names it. A pack for MNP would
# need its own discovery and its own label space, because the two apps reuse label names
# with different meanings and a citation does not carry across them.
APP = "retention"

PACKS: tuple[Path, ...] = tuple(sorted(TESTSETS.glob("retention_v*.jsonl")))
PACK_IDS: tuple[str, ...] = tuple(path.stem for path in PACKS)

# The packs Experiments 1-4 cite. Named here ONLY as the anti-vacuity floor for the
# glob: a typo in the pattern, a renamed directory, or a fixture that stopped being
# committed all produce an empty `PACKS`, and an empty parametrize list is a file full
# of tests that pass by never running.
REQUIRED_PACKS = frozenset({"retention_v1", "retention_v2"})

# The three retention label spaces, keyed the way `_labels_in_row` names its dimensions
# and the way `testsets._validate_gt_rows` (testsets.py:502-506) indexes them.
LABEL_SPACES: dict[str, tuple[str, ...]] = {
    "product": RETENTION.product,
    "call_result": RETENTION.call_result,
    "reason": RETENTION.reason,
}

# Codepoints, not bytes and not grapheme clusters. Thai is heavily combining, so the
# three counts differ a lot -- the longest turn in either pack is 107 codepoints and 305
# UTF-8 bytes -- and a limit that did not say which unit it meant would be three
# different limits depending on who read it. Codepoints, because the limit exists to
# keep a turn reviewable on one screen line.
MAX_TURN_CODEPOINTS = 120

# ---------------------------------------------------------------------------
# DATED ALLOWLIST -- declared 2026-08-06. One entry.
# ---------------------------------------------------------------------------
# Keyed `(pack, item_id, evidence_key)` -> the number of occurrences as measured on the
# date above. The count is part of the key's value rather than a bare "this one is
# exempt", so a span that goes from twice to three times still fails: the declaration
# covers a known state, not a whole item.
#
# `test_every_evidence_span_occurs_exactly_once` compares the measured violations to
# this table with `==`, in both directions. A NEW repeat fails because it is not
# declared. A declared repeat that gets FIXED also fails, saying the entry is now stale
# and should be deleted, so the allowlist cannot quietly outlive the defect.
#
# RET-85 / ev_product:tvs -- span `กล่องทรูวิชั่นส์` ("TrueVisions box"), 2 occurrences.
#
#   RET-85 is a `multislot` item carrying three products (TOL churn, TVS churn, Postpaid
#   save). Its TVS evidence appears on two agent turns: line 3, the agent enumerating
#   what the customer has (`ใช่ค่ะ มีเน็ตบ้าน กล่องทรูวิชั่นส์ แล้วก็เบอร์มือถือแบบรายเดือนค่ะ`),
#   and line 9, the agent asking whether it is still in use
#   (`รับทราบค่ะ แล้วกล่องทรูวิชั่นส์ล่ะคะ ยังใช้อยู่หรือเปล่าคะ`).
#
#   This is a SPECIFICITY defect, not a wrong label. `tvs` is the right product and both
#   occurrences support it. What is lost is the thing an evidence span is for: pinning
#   the label to one identifiable place in the transcript, so that a later edit to
#   either occurrence is visibly an edit to the evidence. `validate` cannot see it --
#   `testsets.py:431` asks `span not in item.transcript_th`, and presence is satisfied
#   by the first hit.
#
#   Declared rather than fixed because the fix is an edit to the pack, and the pack is
#   frozen: Experiments 3 and 4 were scored on these exact bytes. Narrowing the span to
#   `แล้วกล่องทรูวิชั่นส์ล่ะคะ` would resolve it and belongs to whoever cuts the next pack,
#   with the item id already written down here.
REPEATED_SPAN_ALLOWLIST: dict[tuple[str, str, str], int] = {
    ("retention_v2", "RET-85", "ev_product:tvs"): 2,
}


@functools.lru_cache(maxsize=None)
def _pack(path: Path):
    """Load one pack, once. `retention_v2.jsonl` is 518 KB and every test below reads it.

    Cached on the path rather than passed around as a fixture so the parametrize ids stay
    the pack names, which is what a failure report needs to say first.
    """
    return load_testset(path, app=APP)


def _evidence_spans(item):
    """`(key, span)` for one item, in sorted key order.

    Sorted so a failure list is stable across runs and diffable between two people
    looking at the same pack.
    """
    return [(key, item.evidence[key]) for key in sorted(item.evidence)]


def test_every_committed_pack_is_discovered():
    """The glob found the packs the experiments cite.

    Without this, every parametrised test in this file is vacuous the moment the glob
    stops matching -- pytest reports zero failures for zero cases, and the report looks
    identical to a clean run. `>=` rather than `==` on purpose: a new pack must be picked
    up automatically, which is the point of globbing, while the two that four experiments
    depend on can never silently drop out.
    """
    assert REQUIRED_PACKS <= set(PACK_IDS), (
        f"pack discovery found {sorted(PACK_IDS)} in {TESTSETS}, which is missing "
        f"{sorted(REQUIRED_PACKS - set(PACK_IDS))}. Every parametrised test in this file "
        "runs off this glob, so a pack that is not discovered is a pack with no gate at "
        "all -- the exact state this file was written to end."
    )


@pytest.mark.parametrize("pack", PACKS, ids=PACK_IDS)
def test_validate_reports_no_problem(pack: Path):
    """`testsets.validate` returns `[]`.

    The existing checker, run here so it runs in CI for every pack rather than for
    whichever one a human passed to `evalgen check`. It carries the verbatim-span check
    that `testsets.py` calls "THE check" (`testsets.py:414`), plus label-space
    membership, the rule citations and the gt column shape.
    """
    problems = validate(_pack(pack))
    assert problems == [], (
        f"{pack.name}: validate() reported {len(problems)} problem(s):\n  - "
        + "\n  - ".join(problems)
    )


@pytest.mark.parametrize("pack", PACKS, ids=PACK_IDS)
def test_every_transcript_line_carries_a_speaker_prefix(pack: Path):
    """Every line starts with the agent prefix or the customer prefix. No residue.

    This is the precondition that makes the next test mean anything.
    `evidence._turns` DROPS a line carrying neither prefix rather than guessing at it
    (`evidence.py:568-575`), which is the right call there -- but it means a pack of
    unprefixed lines yields two empty turn lists, and "this span is in no agent turn"
    becomes trivially true for every span.

    `evidence.py:116-118` records this as measured over 20 items and 366 lines. There are
    now 100 items and 1807 lines, and that claim had no gate. MEASURED again on
    2026-08-06: 366/366 and 1807/1807 prefixed, 0 unprefixed and 0 blank in either pack.
    """
    from evalgen.evidence import AGENT_PREFIX, CUSTOMER_PREFIX

    unprefixed: list[str] = []
    lines = 0
    for item in _pack(pack).items:
        for number, line in enumerate(item.transcript_th.split("\n"), start=1):
            lines += 1
            if not line.startswith((AGENT_PREFIX, CUSTOMER_PREFIX)):
                unprefixed.append(f"{item.item_id} line {number}: {line!r}")

    assert lines > 0, f"{pack.name} yielded no transcript lines at all"
    assert unprefixed == [], (
        f"{pack.name}: {len(unprefixed)} of {lines} transcript lines carry neither "
        f"{AGENT_PREFIX!r} nor {CUSTOMER_PREFIX!r}:\n  "
        + "\n  ".join(unprefixed[:20])
        + "\nevidence._turns drops these silently, so their text becomes unattributable "
        "and every span quoting them stops being checkable against a speaker."
    )


@pytest.mark.parametrize("pack", PACKS, ids=PACK_IDS)
def test_every_reason_span_is_customer_speech(pack: Path):
    """Reason evidence appears in a customer turn, and in no agent turn.

    `prompt.py:4382-4387` requires the reason phrase to be customer speech. A reason span
    quoted from the agent is a label supported by the wrong speaker -- typically the
    agent's own summary or counter-offer -- and it reads as evidence while being the one
    thing production says cannot serve as evidence.

    Both halves are asserted because they fail differently. Absent from every customer
    turn is the defect proper. Present in an agent turn as well means the phrase is not
    discriminating between the speakers even if a customer also said it, so the span
    cannot show whose statement carried the reason.

    MEASURED 2026-08-06: 32 reason spans in v1 and 152 in v2; zero agent-only in either,
    and zero appearing in both a customer and an agent turn. The stricter of the two
    readings was asserted because the stricter one already holds.

    Scoped to `ev_reason:*` deliberately -- see the module docstring. 16 product and
    call_result spans in v2 are agent-only and are correct.

    `#`-suffixed keys (`ev_reason:promotion related#2`, a second span for one label; 4 in
    v1, 37 in v2) match the prefix test and are held to the same rule, which is intended:
    a supporting span is evidence too.
    """
    agent_only: list[str] = []
    both_speakers: list[str] = []
    checked = 0

    for item in _pack(pack).items:
        agent_turns, customer_turns = _turns(item.transcript_th)
        for key, span in _evidence_spans(item):
            if not key.lower().startswith("ev_reason:"):
                continue
            checked += 1
            in_customer = any(span in turn for turn in customer_turns)
            in_agent = any(span in turn for turn in agent_turns)
            if not in_customer:
                agent_only.append(
                    f"{item.item_id} {key}: {span!r} "
                    f"({'agent turn only' if in_agent else 'no single turn at all'})"
                )
            elif in_agent:
                both_speakers.append(f"{item.item_id} {key}: {span!r}")

    assert checked > 0, (
        f"{pack.name}: no evidence key started with 'ev_reason:', so this test examined "
        "nothing. Either the pack asserts no reasons or the key format changed; both "
        "make the assertion below vacuous."
    )
    assert agent_only == [] and both_speakers == [], (
        f"{pack.name}: reason evidence must be customer speech (prompt.py:4382-4387).\n"
        f"not in any customer turn ({len(agent_only)}):\n  " + "\n  ".join(agent_only)
        + f"\nalso present in an agent turn ({len(both_speakers)}):\n  "
        + "\n  ".join(both_speakers)
        + "\nFix the transcript so the customer says it, or drop the label. Do not move "
        "the span onto the agent's line."
    )


@pytest.mark.parametrize("pack", PACKS, ids=PACK_IDS)
def test_every_evidence_span_occurs_exactly_once(pack: Path):
    """Each span appears exactly once in its own transcript, or is on the dated allowlist.

    `validate` checks presence and stops there (`testsets.py:431`). Presence is the weaker
    property: a span occurring twice satisfies it while failing to do the job an evidence
    span exists for, which is to name ONE place in the transcript that carries the label.
    With two occurrences an edit to either one is invisible to every check in the repo --
    the span still appears, `validate` still passes, and the label now points at text
    nobody chose.

    Compared with `==` against the allowlist rather than by filtering it out, so the table
    stays honest in both directions: an undeclared repeat fails as new, and a declared
    repeat that has been fixed fails as stale.

    MEASURED 2026-08-06: zero repeats in v1; exactly one in v2, allowlisted above.
    """
    measured: dict[tuple[str, str, str], int] = {}
    spans = 0
    for item in _pack(pack).items:
        for key, span in _evidence_spans(item):
            spans += 1
            occurrences = item.transcript_th.count(span)
            if occurrences != 1:
                measured[(pack.stem, item.item_id, key)] = occurrences

    expected = {key: count for key, count in REPEATED_SPAN_ALLOWLIST.items() if key[0] == pack.stem}

    assert spans > 0, f"{pack.name} carries no evidence spans, so this test examined nothing"
    assert measured == expected, (
        f"{pack.name}: evidence spans that do not occur exactly once.\n"
        f"  measured: {measured}\n"
        f"  declared: {expected}\n"
        "An entry in `measured` and not in `declared` is a NEW violation: an evidence span "
        "that no longer names one place in its transcript. Fix the span -- narrow it until "
        "it is unique -- or, if the pack is frozen, add a dated entry to "
        "REPEATED_SPAN_ALLOWLIST naming the item and saying why. An entry in `declared` and "
        "not in `measured` means the defect is gone and the allowlist entry is now stale; "
        "delete it. Do NOT relax this assertion to accommodate either case."
    )


@pytest.mark.parametrize("pack", PACKS, ids=PACK_IDS)
def test_every_turn_is_within_the_length_limit(pack: Path):
    """No transcript line exceeds `MAX_TURN_CODEPOINTS`.

    Asserted nowhere in this repo before this file, in code or in `validate`. The limit is
    what keeps a turn reviewable: every ground-truth label in both packs was defended by a
    human reading these lines, and a 400-character turn is one nobody re-reads when the
    label is questioned. It also keeps evidence spans short enough to stay unique, which
    is the property the previous test gates.

    MEASURED 2026-08-06: the longest turn in BOTH packs is the same 107-codepoint agent
    greeting in RET-18, 13 under the limit. The limit is therefore not currently binding
    on anything, which is the point -- it is a ceiling on future edits, and it is written
    as a named constant so raising it is a visible decision rather than a quiet drift.
    """
    over: list[str] = []
    turns = 0
    for item in _pack(pack).items:
        for number, line in enumerate(item.transcript_th.split("\n"), start=1):
            turns += 1
            if len(line) > MAX_TURN_CODEPOINTS:
                over.append(f"{item.item_id} line {number}: {len(line)} codepoints: {line!r}")

    assert turns > 0, f"{pack.name} yielded no turns"
    assert over == [], (
        f"{pack.name}: {len(over)} of {turns} turns exceed {MAX_TURN_CODEPOINTS} "
        "codepoints:\n  " + "\n  ".join(over[:20])
    )


@pytest.mark.parametrize("pack", PACKS, ids=PACK_IDS)
def test_every_label_class_has_support(pack: Path):
    """Every class in the retention label space is asserted by at least one gt row.

    `validate` checks the other direction -- that a label the pack uses is IN the space
    (`testsets.py:507-514`). Nothing checked that the space is COVERED by the pack, and
    the two are not the same guarantee at all.

    The failure this catches is specific and silent. A class no model gets right is a
    class whose items are tempting to remove, and removing the last one leaves a pack that
    still validates, still scores, and now reports a per-class accuracy over a class it no
    longer measures. The absence looks like a clean run. `down sell not success` has 1 item
    in v1 and 6 in v2; `sale upsell problem`, 1 and 10 -- these are the ones an edit
    reaches for.

    Labels are extracted through `testsets._labels_in_row`, so reason cells comma-split
    exactly as `fact_checker.py:877` splits them and a cell reading `"network, save cost"`
    counts as support for two classes rather than one non-existent class.

    MEASURED 2026-08-06: all 19 classes (4 product, 4 call_result, 11 reason) have support
    in both packs. Thinnest in v1: `other`, `post to pre`, `customer reason`,
    `device promotion related`, `sale upsell problem` and `down sell not success` at 1 item
    each, and `undefined` at 1.
    """
    support: Counter = Counter()
    for item in _pack(pack).items:
        for row in item.gt:
            for dimension, value in _labels_in_row(row):
                support[(dimension, value)] += 1

    unsupported = [
        f"{dimension}:{label}"
        for dimension, classes in LABEL_SPACES.items()
        for label in classes
        if support[(dimension, label.lower())] == 0
    ]

    assert unsupported == [], (
        f"{pack.name}: {len(unsupported)} label class(es) have no supporting gt row: "
        f"{unsupported}. A class with zero items is a class the pack silently stopped "
        "measuring while still reporting a per-class number for the dimension it lives in. "
        "Add an item for it rather than removing it from the label space -- the space is "
        "transcribed verbatim from production (labelspaces.py) and is not the pack's to "
        "trim."
    )


@pytest.mark.parametrize("pack", PACKS, ids=PACK_IDS)
def test_identifier_invariants(pack: Path):
    """Ids are unique and both synthetic patterns hold, including the scored key.

    Three of these overlap `validate` on purpose, because this file is what runs them for
    every pack in CI. The fourth does not exist anywhere else:

      * `item_id` unique -- `validate` checks it (`testsets.py:360-367`); it keys every
        per-item result, so a duplicate overwrites a scored row.
      * `call_id` matches `CALL_ID_PATTERN` and `phone_number` matches `PHONE_PATTERN`
        or is null -- `validate` checks both. They are the runtime guarantee that no real
        customer identifier is in the one dataset this repo commits. Both are referenced
        rather than retyped: `PHONE_PATTERN` was widened on 2026-08-06
        (`^08100000[0-9]{2}$` -> `^0810000[0-9]{3}$`, `testsets.py:135`) and a hardcoded
        copy here would now be telling a reader the pack is held to a rule it is not.
      * **`(call_id, phone_number)` unique -- checked NOWHERE else.** The scored row is
        `(call_id, phone_number, product)` (`fact_checker.py:1075`). Two items sharing a
        call id and a phone collide on that key for every product they have in common, and
        the collision is invisible: both items run, both score, and the merge silently
        answers one item's question with the other's row. Distinct `item_id`s do not
        prevent it, which is why `validate` passing is not enough.

    MEASURED 2026-08-06: v1 call ids 5001-5020, v2 5001-5100, all distinct; 20 and 100
    distinct phone numbers, all of them in the original `08100000xx` hundred that the
    widened block still contains; no nulls in either pack. A null phone
    stays legal here because `validate` allows it and production has the matching defect
    (`testsets.py:392-399`) -- the pair-uniqueness check below skips nulls rather than
    treating them as equal, since two null phones are two unknowns and not one value.
    """
    items = _pack(pack).items
    problems: list[str] = []

    duplicate_ids = [name for name, count in Counter(i.item_id for i in items).items() if count > 1]
    if duplicate_ids:
        problems.append(f"duplicate item_id(s): {sorted(duplicate_ids)}")

    for item in items:
        if not CALL_ID_PATTERN.fullmatch(item.call_id):
            problems.append(f"{item.item_id}: call_id {item.call_id!r} is outside 5000-5999")
        if item.phone_number is not None and not PHONE_PATTERN.fullmatch(item.phone_number):
            problems.append(
                f"{item.item_id}: phone_number {item.phone_number!r} is outside "
                f"{PHONE_PATTERN.pattern}"
            )

    keys = Counter((i.call_id, i.phone_number) for i in items if i.phone_number is not None)
    for key, count in sorted(keys.items()):
        if count > 1:
            owners = sorted(
                i.item_id for i in items if (i.call_id, i.phone_number) == key
            )
            problems.append(
                f"{owners} share the scored key {key}; rows for a product both claim "
                "collide on (call_id, phone_number, product) (fact_checker.py:1075)"
            )

    assert len(items) > 0, f"{pack.name} holds no items"
    assert problems == [], f"{pack.name}:\n  " + "\n  ".join(problems)


def test_v2_begins_with_v1_byte_for_byte():
    """`retention_v2`'s first 20 lines ARE `retention_v1`, byte for byte.

    `TESTING.md:317` states it as a property of the packs -- "`RET-01`...`RET-20`
    byte-identical to v1" -- and nothing checked it. It is the reason the two packs'
    results are comparable at all: Experiments 1 and 2 scored v1, Experiments 3 and 4
    scored v2, and per-item numbers for `RET-01`...`RET-20` are quoted across that
    boundary. If v2's copy of those items drifted by a single character, those
    comparisons would be between two different items wearing one id, and every check in
    this repo would still pass.

    Asserted as a sha over bytes rather than by comparing parsed items, because parsed
    equality would forgive exactly the edits that break provenance: reordered JSON keys,
    changed escaping, a different unicode normalisation. `testset_sha` hashes bytes for
    the same reason (`testsets.py:169-175`).

    Derived, not pinned to a literal. `sha256(v1) == sha256(v2's first N lines)` where N
    is v1's own line count, so the test states the RELATIONSHIP and stays true through a
    coordinated regeneration of both packs. A literal here would be a second freeze on
    v1's absolute bytes, which is a different claim than the one `TESTING.md` makes and
    would fail for a reason that has nothing to do with v2.

    MEASURED 2026-08-06: both sides are
    `c367a478d89bb047acc1ea5806fc36b75b0b9f561b62a264aa0c2188493b2b0f`, over 20 lines of
    v1 and the first 20 of v2's 100. Reassembly is exact including the trailing newline.
    """
    v1_path = TESTSETS / "retention_v1.jsonl"
    v2_path = TESTSETS / "retention_v2.jsonl"
    v1_bytes = v1_path.read_bytes()
    v2_bytes = v2_path.read_bytes()

    v1_lines = v1_bytes.split(b"\n")
    v2_lines = v2_bytes.split(b"\n")
    prefix_length = len([line for line in v1_lines if line.strip()])

    assert prefix_length > 0, "retention_v1.jsonl holds no lines"
    assert len(v2_lines) > prefix_length, (
        f"retention_v2.jsonl has fewer lines ({len(v2_lines)}) than retention_v1.jsonl's "
        f"{prefix_length}, so it cannot extend it"
    )

    prefix = b"\n".join(v2_lines[:prefix_length]) + b"\n"
    prefix_sha = hashlib.sha256(prefix).hexdigest()
    v1_sha = hashlib.sha256(v1_bytes).hexdigest()

    assert prefix_sha == v1_sha, (
        f"retention_v2.jsonl's first {prefix_length} lines hash to {prefix_sha}, but all "
        f"of retention_v1.jsonl hashes to {v1_sha}. The two packs have diverged on the "
        "items they share, so every per-item number quoted across Experiments 1-2 and "
        "3-4 is now a comparison between two different items with the same id. Work out "
        "which pack moved before changing anything here."
    )
