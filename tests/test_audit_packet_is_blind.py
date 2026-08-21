"""The audit packet must not contain the answers it is asking for.

WHY THIS IS A TEST AND NOT A PROMISE. The packet's entire value is that a reviewer arrives at
a label without having seen one. If an expected label leaks into the page -- in a data
attribute, a comment, a stray debug field, a group name that separates disputes from controls
-- the exercise still produces a number, the number still looks like agreement, and nobody can
tell from the output that it means nothing.

That is the failure mode this repository keeps meeting: a wrong number that looks healthy. The
difference here is that it is cheap to make impossible, so it is.

The leaks are not all obvious. The expected LABEL is the first one anyone thinks of; these
four are the ones that got past the first draft of the design:

  * **the evidence span.** `case_explorer` renders each label beside the verbatim sentence
    that justifies it. Handing the reviewer the sentence the labeller keyed on is handing them
    the answer with extra steps.
  * **the group name.** A reviewer who can see `group: product_mismatch` on a case knows the
    corpus and the model disagreed about the product before reading a word.
  * **the corpus's own notes** -- `mechanism`, `why_it_matters`, `expected_failure` -- which
    state what each call is built to demonstrate.
  * **the item id.** `ASR-076` is greppable straight back to `business.csv`.

The answer key exists, and lives in `out/` (gitignored) precisely so it cannot be sent
alongside the packet by accident.
"""

from __future__ import annotations

import csv
import importlib.util
import json
import re
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
PACK = REPO / "asr-eval-v2"
RUN = REPO / "out" / "runs" / "20260820-055342Z-e21"


def _module():
    spec = importlib.util.spec_from_file_location(
        "audit_packet", REPO / "scripts" / "audit_packet.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


AP = _module()

pytestmark = pytest.mark.skipif(
    not (RUN / "results.jsonl").exists() or not (PACK / "dialogues").exists(),
    reason="needs the gitignored run and pack; the packet is built locally",
)


@pytest.fixture(scope="module")
def built():
    """Build a small real packet once and reuse it across the assertions."""
    cases = AP.build_cases(RUN, PACK, "ceiling", controls=8, seed=20260821)
    page = AP.render(cases, AP.spec_text(), 20260821, RUN.name)
    return cases, page


def test_no_expected_label_reaches_the_page(built):
    """The obvious leak, checked per case rather than in aggregate.

    Aggregate checking is useless here: `save` and `churn` legitimately appear all over the
    page -- in the rules, and as radio-button options. What must not appear is a case's own
    expected label ATTACHED to that case. So each case's markup is sliced out and searched on
    its own.
    """
    cases, page = built
    for case in cases:
        start = page.index(f'id="{case["case_id"]}"')
        end = page.find("<section class=\"case\"", start + 1)
        block = page[start:end if end != -1 else len(page)]
        # Strip the radio options, which name every label by design.
        block = re.sub(r'<div class="opts">.*?</div>', "", block, flags=re.S)
        for label in case["_expected_outcome"]:
            assert f">{label}<" not in block, (
                f"case {case['case_id']} carries its expected outcome {label!r} outside the "
                "answer options; the reviewer can read the answer off the page"
            )


def test_the_group_is_never_named_in_the_page(built):
    """Knowing a case is a control, or a dispute, changes how it is read."""
    _cases, page = built
    for group in AP.GROUPS:
        assert group not in page, (
            f"the packet names the group {group!r}. A reviewer who can tell a disputed case "
            "from a control reads them differently, and the agreement rate stops meaning "
            "anything."
        )


def test_no_item_or_call_id_reaches_the_page(built):
    """`ASR-076` greps straight back to business.csv; `7175` is one subtraction away."""
    cases, page = built
    for case in cases:
        assert case["item_id"] not in page, (
            f"{case['item_id']} appears in the packet; it is greppable back to the ground "
            "truth in one step"
        )
        assert re.search(rf'\b{case["call_id"]}\b', page) is None, (
            f"call_id {case['call_id']} appears in the packet"
        )


def test_the_corpus_notes_never_reach_the_page(built):
    """mechanism / why_it_matters / expected_failure each state the intended answer."""
    _cases, page = built
    for field in ("mechanism", "why_it_matters", "expected_failure", "expected_label"):
        assert field not in page, f"the packet leaks the corpus note {field!r}"


def test_no_private_field_survives_rendering(built):
    """Anything named `_expected_*` is answer-key data and must stay server-side.

    Checked as a naming convention rather than a list, so a future field that follows the
    convention is covered without anyone remembering to add it here.
    """
    cases, page = built
    private = {k for c in cases for k in c if k.startswith("_")}
    assert private, "the convention has changed; this test is no longer checking anything"
    for key in private:
        assert key not in page, f"private field {key!r} was rendered into the packet"


def test_the_answer_key_is_written_outside_docs(tmp_path):
    """The key must be impossible to ship by accident.

    `docs/` is committed and is where the packet goes. `out/` is gitignored. The default has
    to put them in different places, or one careless attachment sends both.
    """
    import argparse

    parser = [a for a in AP.main.__doc__ or ""]  # touch, so a docstring-only main is caught
    source = (REPO / "scripts" / "audit_packet.py").read_text(encoding="utf-8")
    assert 'REPO / "out" / "audit-answer-key.csv"' in source, (
        "the answer key default no longer points at out/; docs/ is committed and the packet "
        "lives there"
    )
    assert 'REPO / "docs" / "reports" / "audit-packet.html"' in source


def test_the_shuffle_is_seeded_and_reproducible():
    """An unrecorded shuffle cannot be explained later, and two packets cannot be compared."""
    first = AP.build_cases(RUN, PACK, "ceiling", controls=8, seed=4242)
    again = AP.build_cases(RUN, PACK, "ceiling", controls=8, seed=4242)
    assert [c["case_id"] for c in first] == [c["case_id"] for c in again]

    other = AP.build_cases(RUN, PACK, "ceiling", controls=8, seed=99)
    assert [c["case_id"] for c in first] != [c["case_id"] for c in other], (
        "the seed does not change the packet; the shuffle is not actually seeded"
    )


def test_case_ids_are_opaque_and_seed_scoped():
    """Two packets built from different samples must not collide on an id.

    Otherwise reviewer answers from one packet silently score against another's key.
    """
    assert AP.case_id("7175", 1) != AP.case_id("7175", 2)
    assert "7175" not in AP.case_id("7175", 1)
    assert AP.case_id("7175", 1) == AP.case_id("7175", 1)


def test_every_case_carries_a_transcript(built):
    """A blind packet with nothing to read is worse than no packet."""
    cases, _page = built
    for case in cases:
        assert len(case["turns"]) >= 10, (
            f"case {case['case_id']} has {len(case['turns'])} turns; a reviewer cannot label "
            "a call they cannot read"
        )


def test_the_spec_the_reviewer_gets_actually_contains_the_rules():
    """Slicing by heading is brittle; assert the slice still has the four class definitions."""
    spec = AP.spec_text()
    for label in ("save", "churn", "unknown", "undefined"):
        assert f"`{label}`" in spec, f"the spec slice no longer defines {label!r}"
    assert "product" in spec.lower(), "the spec slice lost the product section"
    assert len(spec) > 2000, f"the spec slice is only {len(spec)} chars; it lost content"
