"""The labelling SPEC is pinned, and an edit to it says so in those words.

WHY THIS FILE EXISTS. The two prompt assets are the written definition of what `save`,
`churn`, `unknown` and `undefined` MEAN. Every ground-truth label in every pack is supposed to
be derivable from them, and on 2026-08-20 a corpus that contradicted them cost ~0.13 weighted
F1 on every arm before anyone noticed.

They were already protected, but only sideways. `validate_manifest()` shas the ASSEMBLED
prompt, so any byte change to either asset does fail the suite -- as:

    v9_16_base.sha256: manifest=968a2974... registry=1f0c3d5b...

That is a hash-drift message. It names the symptom. Someone who has just reworded the `save`
rule reads it as "the manifest needs regenerating", regenerates it, and the guard is gone --
which is exactly the wrong reaction, because rewording the `save` rule invalidates every
`save` label in every pack.

`src/evalgen/prompts/PORT-NOTES.md:12-13` has recorded both asset shas since the port. Nothing
in the repository read them. These tests do, and they fail with a message about the SPEC.

WHAT TO DO WHEN ONE OF THESE FAILS. Not "update the sha". The label definitions changed, so:
every pack's ground truth has to be re-derived against the new text, `VOCABULARIES.md` has to
be updated to match, and any published figure computed under the old definitions is stale.
Update the constant last, as the record that the rest was done.
"""

from __future__ import annotations

import hashlib
import re
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
PROMPTS = REPO / "src" / "evalgen" / "prompts"
PORT_NOTES = PROMPTS / "PORT-NOTES.md"

# Pinned 2026-08-21, matching PORT-NOTES.md. These are the bytes every ground-truth label in
# this repository was derived against.
SPEC_SHA = {
    "retention_wrapper.txt":
        "d3e5b4b36a2143ab86557bc882ba745fac6951d13f7628236b2f9379e39d96f0",
    "retention_v9_16_body.txt":
        "aca86f29771f3e28ca5d854f019c99c38494846eb04f6decc1ca59223217cb61",
}

# The four class definitions live in the body. If any of these strings stops appearing exactly
# once, the label space has been reworded even if someone re-pinned the sha.
CLASS_ANCHORS = (
    "- `churn`",
    "- `save`",
    "- `unknown` (Conversation ends before making a final decision",
    "- `undefined` (Conversation irrelevant to retention",
)


def _sha(name: str) -> str:
    return hashlib.sha256((PROMPTS / name).read_bytes()).hexdigest()


@pytest.mark.parametrize("name", sorted(SPEC_SHA))
def test_the_labelling_spec_has_not_changed(name):
    """The message is the point. Read it before touching the constant."""
    actual = _sha(name)
    assert actual == SPEC_SHA[name], (
        f"\n\nTHE LABELLING SPEC CHANGED: {name}\n"
        f"  pinned {SPEC_SHA[name]}\n"
        f"  actual {actual}\n\n"
        "This file defines what `save`, `churn`, `unknown` and `undefined` MEAN. Every\n"
        "ground-truth label in every pack was derived against these exact bytes, so an edit\n"
        "here silently invalidates them -- that is not hypothetical, it is what happened on\n"
        "2026-08-20 and it cost ~0.13 weighted F1 on every arm.\n\n"
        "Do NOT just update the constant. Re-derive the affected ground truth, update\n"
        "tests/fixtures/testsets/VOCABULARIES.md to match, recompute any published figure\n"
        "that used the old definitions, and update the constant LAST as the record that the\n"
        "rest was done.\n"
    )


def test_port_notes_still_records_the_same_shas():
    """The constant above and PORT-NOTES.md must not drift apart.

    Two records of the same fact are worse than one when they disagree: whichever a reader
    finds first becomes the answer. This pins them together.
    """
    notes = PORT_NOTES.read_text(encoding="utf-8")
    for name, expected in sorted(SPEC_SHA.items()):
        row = next((l for l in notes.splitlines() if f"`{name}`" in l and "sha" not in l), "")
        assert expected in row, (
            f"PORT-NOTES.md no longer records {expected[:16]}... for {name}. Its row reads:\n"
            f"  {row.strip() or '<no row found>'}\n"
            "PORT-NOTES.md and tests/test_spec_lock.py must agree; fix whichever is stale."
        )


@pytest.mark.parametrize("anchor", CLASS_ANCHORS)
def test_every_call_result_class_is_still_defined_exactly_once(anchor):
    """A sha catches ANY edit; this catches the edit that matters, by name.

    If someone re-pins the sha after a reword, the sha test goes quiet. This one does not:
    it asserts the four classes are still each defined, once, in the body.
    """
    body = (PROMPTS / "retention_v9_16_body.txt").read_text(encoding="utf-8")
    count = body.count(anchor)
    assert count == 1, (
        f"the `call_result` class definition {anchor!r} appears {count} times in "
        "retention_v9_16_body.txt; expected exactly 1. The label space has been reworded."
    )


def test_the_indecision_rule_still_says_save():
    """The single most load-bearing sentence in the spec.

    `unknown` was built out of indecision phrases in the audio corpus precisely because this
    rule was not read. asr-eval/tests/test_outcome_pools_match_spec.py asserts the corpus
    obeys it -- but that suite is not what CI runs on every PR, so the rule itself is pinned
    here too.
    """
    body = (PROMPTS / "retention_v9_16_body.txt").read_text(encoding="utf-8")
    line = next((l for l in body.splitlines() if "indecision" in l), "")
    assert line, "the indecision rule is gone from retention_v9_16_body.txt"
    assert "'save'" in line or "`save`" in line, (
        f"the indecision rule no longer routes to `save`:\n  {line.strip()}\n"
        "If this is deliberate, the audio corpus's `unknown` pool and every `save`/`unknown` "
        "label derived from it need re-deriving."
    )


def test_the_spec_is_utf8_lf_with_one_trailing_newline():
    """PORT-NOTES.md promises this shape, and the shas above only hold if it is kept.

    A CRLF round-trip through an editor changes every byte and would fail the sha test with
    no semantic change at all -- worth naming here so that failure is recognisable rather
    than alarming.
    """
    for name in sorted(SPEC_SHA):
        raw = (PROMPTS / name).read_bytes()
        assert b"\r\n" not in raw, f"{name} has CRLF line endings; PORT-NOTES.md pins LF"
        assert raw.endswith(b"\n"), f"{name} has no trailing newline"
        assert not raw.endswith(b"\n\n"), f"{name} has more than one trailing newline"
        assert not raw.startswith(b"\xef\xbb\xbf"), f"{name} has a UTF-8 BOM"
        raw.decode("utf-8")  # raises if not valid UTF-8


def test_the_manifest_decoding_matches_the_contract_it_describes():
    """`prompts/manifest.json`'s decoding fields describe PHASE ONE, not every experiment.

    Written down because the ambiguity nearly caused a wrong "fix". The manifest declares
    `top_p: 0.0`, and E21/E23 run `top_p: 1.0` -- which reads as a contradiction until you
    check what the field is scoped to. It is not:

      * retention-e5  and retention-e7  pin top_p 0 -- the phase-one text contract, which is
        what this manifest is the catalogue for.
      * retention-e23 pins top_p 1 DELIBERATELY, and says why in `workload.note`: 1 is what
        production's own generation_config sets
        (`config/sentiment_qa/qa_pipeline_fact_check.yml`, `topP: 1`).

    So the manifest is right, E23 is right, and making `validate_manifest()` enforce these
    fields against every run would break a documented, sourced deviation. What was actually
    missing is anything checking the manifest against the contract it DOES describe. That is
    this test.

    manifest.json's own bytes are sha-pinned by tests/test_prompts.py, so the fix could not
    have been an edit to it anyway.
    """
    import json

    manifest = json.loads((PROMPTS / "manifest.json").read_text(encoding="utf-8"))
    declared = {p["id"]: p for p in manifest["prompts"]}
    base = declared["v9_16_base"]

    for name in ("retention-e5", "retention-e7"):
        plan = json.loads(
            (REPO / "experiments" / f"{name}.plan.json").read_text(encoding="utf-8"))
        workload = plan["workload"]
        for field in ("temperature", "top_p", "reasoning_effort"):
            assert workload[field] == base[field], (
                f"{name}.plan.json workload.{field} = {workload[field]!r} but "
                f"prompts/manifest.json declares {base[field]!r} for v9_16_base. The manifest "
                "is the phase-one contract; e5 and e7 execute it and must agree with it."
            )

    # And the deviation stays deliberate rather than becoming folklore.
    e23 = json.loads(
        (REPO / "experiments" / "retention-e23.plan.json").read_text(encoding="utf-8"))
    assert e23["workload"]["top_p"] != base["top_p"], (
        "retention-e23 no longer deviates on top_p. If that is intended, delete the "
        "`workload.note` explaining the deviation too, so the plan stops claiming something "
        "that is no longer true."
    )
    assert "topP: 1" in (e23["workload"].get("note") or ""), (
        "retention-e23 deviates from the phase-one top_p but no longer cites the production "
        "config it is matching. An undocumented deviation is indistinguishable from a typo."
    )


def test_the_assembled_prompt_sha_is_still_what_the_plans_pin():
    """Ties the asset locks to the sha the experiment plans actually reference.

    The two asset files are inputs; `v9_16_base` is what a model receives. This asserts the
    assembly of the pinned inputs still produces the sha that retention-e23.plan.json:84 and
    prompts/manifest.json name, so the lock covers the whole chain rather than its ends.
    """
    from evalgen.prompts import get

    assert get("v9_16_base").sha == (
        "968a2974f0ce462e0f1ad815c9434252420a677766fa23775a69a691f3db4eee"
    ), "the assembled v9_16_base prompt changed even though both assets are byte-identical"
