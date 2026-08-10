"""Gates on the ported prompt: assembled correctly, and the example is followable.

Four failures this file exists to catch, all of which produce a prompt that still runs,
still returns JSON, and still scores — which is why none of them can be left to review.

1. **The Role line twice.** The wrapper (`retention.yml:2`) and the `prompt_v9_16`
   monolith (`prompt.py:4314`) both open with it. Substituting the monolith into
   `{user_prompt}` instead of its 4318-4409 body doubles the Role, the Objective, the
   Output Format block and the worked example. The resulting prompt is a different
   prompt than production's, and nothing about its output says so.
2. **An unsubstituted placeholder.** A prompt containing the literal `{user_prompt}` has
   no analysis rules in it at all; the model gets Role, Reminders and an example, and
   guesses the rest.
3. **An example the model cannot copy.** Production hides this behind forced function
   calling (`main.py:1109-1114`, `mode: ANY`); this harness passes the schema through
   `response_format`, so the example is live. See `prompts/PORT-NOTES.md` fixes (a)-(c).
4. **The transcript in the wrong turn.** If it lands in the system message the model is
   being instructed with the evidence rather than shown it, and the two arms stop being
   comparable to any normal chat deployment.

The schema check below is a small validator rather than a hardcoded key list, so it reads
the committed schema and fails when *either* side drifts. `jsonschema` is not a
dependency of this repository and adding one to check one document would cost more than
the forty lines.
"""

from __future__ import annotations

import difflib
import json
import re
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from evalgen.prompts import (  # noqa: E402
    AUDIO_TO_TRANSCRIPT,
    BODY_PATH,
    EXAMPLE_REASON_VALUES,
    PROMPTS,
    ROLE_MARKER,
    USER_PROMPT_PLACEHOLDER,
    VARIANTS,
    WRAPPER_PATH,
    Edit,
    Prompt,
    PromptError,
    Variant,
    build_base,
    build_messages,
    build_variant,
    get,
)
from evalgen.testsets import load_testset  # noqa: E402

SCHEMA_PATH = ROOT / "src" / "evalgen" / "schemas" / "retention.json"
TESTSET_PATH = ROOT / "tests" / "fixtures" / "testsets" / "retention_v1.jsonl"

# The full Role sentence, not the prefix `ROLE_MARKER`. A count of the prefix would also
# be 1 if the second copy had been reworded, and a reworded duplicate is still a
# duplicate.
ROLE_LINE = (
    "**Role**: You are a call center agent tasked with analyzing a transcript of a "
    "client's phone call to a call center service from a telecom company."
)


@pytest.fixture(scope="module")
def base() -> Prompt:
    return build_base()


@pytest.fixture(scope="module")
def schema() -> dict:
    return json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))


@pytest.fixture(scope="module")
def example(base: Prompt) -> dict:
    """The worked example, parsed out of the assembled prompt itself.

    Parsed from the prompt rather than from the asset file on purpose: the model reads
    the assembled text, so that is the text whose example has to be valid.
    """
    blocks = re.findall(r"```json\n(.*?)\n```", base.system_text, re.S)
    assert len(blocks) == 1, f"expected exactly one ```json block, found {len(blocks)}"
    return json.loads(blocks[0])


# --------------------------------------------------------------------- assembly


def test_role_line_appears_exactly_once(base: Prompt) -> None:
    """The headline gate. Two Role lines means the monolith was substituted, not its body."""
    assert base.system_text.count(ROLE_LINE) == 1
    assert base.system_text.count(ROLE_MARKER) == 1
    # Everything the wrapper owns is likewise single. A doubled prompt doubles all of it.
    assert base.system_text.count("**Objective**:") == 1
    assert base.system_text.count("Output Format:") == 1
    assert base.system_text.count("**Reminder:**") == 1
    assert base.system_text.count("```json") == 1


def test_body_asset_does_not_contain_the_role_line() -> None:
    """The same gate one level down, where the mistake is actually made.

    `retention_v9_16_body.txt` is `prompt.py:4318-4409`. The Role line is `:4314`. If it
    is in the asset, the extraction slice was wrong and every prompt built from it is
    wrong in a way `build_base()` alone cannot see.
    """
    body = BODY_PATH.read_text(encoding="utf-8")
    assert ROLE_MARKER not in body
    assert body.startswith("**Analysis Requirements**:")
    assert body.rstrip("\n").endswith(
        "5. recommendation: Suggestion how to keep client loyalty to brand"
    )


def test_placeholder_is_fully_substituted(base: Prompt) -> None:
    """No `{user_prompt}` survives, and the body it was replaced by is present."""
    assert USER_PROMPT_PLACEHOLDER in WRAPPER_PATH.read_text(encoding="utf-8")
    assert USER_PROMPT_PLACEHOLDER not in base.system_text
    assert "**Analysis Requirements**:" in base.system_text
    # A spot check from the middle and the end of the body, so a truncated substitution
    # cannot pass on its first line alone.
    assert "`down sell not success`" in base.system_text
    assert "5. recommendation: Suggestion how to keep client loyalty to brand" in base.system_text


def test_every_audio_reference_was_rewritten(base: Prompt) -> None:
    """The prompt must not promise an audio file this harness never sends.

    `build_base` already refuses a pair that does not fire exactly once; this checks the
    complement — that the list is complete, not merely accurate.
    """
    assert "audio" not in base.system_text.lower()
    for _before, after in AUDIO_TO_TRANSCRIPT:
        assert after in base.system_text


def test_sha_is_over_the_text_that_was_built(base: Prompt) -> None:
    """A row citing a prompt sha must be citing the text the model saw."""
    import hashlib

    assert base.sha == hashlib.sha256(base.system_text.encode("utf-8")).hexdigest()
    assert base.id == "v9_16_base"
    assert PROMPTS[base.id].sha == base.sha


def test_get_refuses_an_unknown_id() -> None:
    """A typo must never fall back to the base: two arms on one prompt measure nothing."""
    assert get("v9_16_base") is PROMPTS["v9_16_base"]
    with pytest.raises(PromptError) as exc:
        get("v9_16_bse")
    assert "v9_16_base" in str(exc.value)  # the message names the alternatives


def test_assets_are_utf8_lf_without_bom() -> None:
    """The corpus is Thai and the consumer is Windows. Both of these have bitten before."""
    for path in (WRAPPER_PATH, BODY_PATH, SCHEMA_PATH):
        raw = path.read_bytes()
        assert not raw.startswith(b"\xef\xbb\xbf"), f"{path.name} has a BOM"
        assert b"\r" not in raw, f"{path.name} has CRLF endings"
        raw.decode("utf-8")  # raises if it is not UTF-8


# ----------------------------------------------------------------- worked example


def test_example_json_parses(example: dict) -> None:
    """PORT-NOTES fix (a). The original raised `Expecting ',' delimiter` at char 586."""
    assert set(example) == {"product", "call_event_detection", "recommendation"}
    assert example["product"], "the example must show at least one product"


def test_example_shows_exactly_one_product(example: dict) -> None:
    """PORT-NOTES fix (c). Reminder #2 says only mention products discussed in the call.

    Product is part of the scored join key, so a model copying a three-product example
    emits rows no ground-truth row can match.
    """
    assert list(example["product"]) == ["Postpaid"]


def _validate(instance, node, path="$") -> list[str]:
    """Validate against the subset of JSON Schema this file actually uses.

    Supports: `type` (single or union), `enum`, `properties`, `required`, `items`,
    `additionalProperties: false`. Anything else is ignored rather than guessed at.
    """
    errors: list[str] = []
    types = node.get("type")
    if types is not None:
        allowed = types if isinstance(types, list) else [types]
        ok = {
            "object": lambda v: isinstance(v, dict),
            "array": lambda v: isinstance(v, list),
            "string": lambda v: isinstance(v, str),
            # bool before int: `isinstance(True, int)` is True in Python.
            "integer": lambda v: isinstance(v, int) and not isinstance(v, bool),
            "null": lambda v: v is None,
        }
        if not any(ok[t](instance) for t in allowed):
            errors.append(f"{path}: {type(instance).__name__} is not {allowed}")
            return errors
    if "enum" in node and instance not in node["enum"]:
        errors.append(f"{path}: {instance!r} is not one of {node['enum']}")
    if isinstance(instance, dict):
        props = node.get("properties", {})
        for key in node.get("required", []):
            if key not in instance:
                errors.append(f"{path}: missing required key {key!r}")
        if node.get("additionalProperties") is False:
            for key in instance:
                if key not in props:
                    errors.append(f"{path}: {key!r} is not an allowed property")
        for key, value in instance.items():
            if key in props:
                errors.extend(_validate(value, props[key], f"{path}.{key}"))
    if isinstance(instance, list) and "items" in node:
        for i, value in enumerate(instance):
            errors.extend(_validate(value, node["items"], f"{path}[{i}]"))
    return errors


def test_example_satisfies_the_committed_schema(example: dict, schema: dict) -> None:
    """PORT-NOTES fix (b), and the reason all three fixes matter.

    Both arms get this schema through `response_format`. If the example the prompt tells
    the model to copy does not satisfy it, a model that obeys the prompt produces a
    `schema_violation` — scored as a coverage loss for a mistake the prompt made. The
    original example emitted `"Phrase"` where the schema requires `"keyword"`.
    """
    assert _validate(example, schema) == []


def test_the_validator_can_fail(example: dict, schema: dict) -> None:
    """The validator asserts absence, which a broken validator satisfies for free.

    Three mutations, one per class of check the test above depends on. Without this, a
    `_validate` that returned `[]` unconditionally would make the schema gate vacuous —
    the failure mode `tests/test_requirements.py` documents for gates generally.
    """
    missing_required = json.loads(json.dumps(example))
    del missing_required["product"]["Postpaid"]["main"]["keyword"]
    assert _validate(missing_required, schema), "a missing required key must be caught"

    bad_enum = json.loads(json.dumps(example))
    bad_enum["product"]["Postpaid"]["main"]["reason"] = "Network"  # title case
    assert _validate(bad_enum, schema), "a value outside the enum must be caught"

    extra_product = json.loads(json.dumps(example))
    extra_product["product"]["Prepaid"] = extra_product["product"]["Postpaid"]
    assert _validate(extra_product, schema), "additionalProperties:false must be enforced"


# --------------------------------------------------------------------- messages


def test_build_messages_puts_the_transcript_in_the_user_turn(base: Prompt) -> None:
    """System carries the prompt, user carries the transcript, and nothing else moves.

    Run against the real eval set rather than a stub: the transcript is also where
    `testsets.validate` asserts every evidence span appears, so the text in the user turn
    has to be the text the labels were checked against, byte for byte.
    """
    item = load_testset(TESTSET_PATH).items[0]
    messages = build_messages(item, base)

    assert [m["role"] for m in messages] == ["system", "user"]
    assert messages[0]["content"] == base.system_text
    assert messages[1]["content"] == item.transcript_th

    # Verbatim, both ways: nothing wrapped around the transcript, and no transcript
    # leaking into the instructions.
    assert messages[1]["content"].strip() == item.transcript_th.strip()
    assert item.transcript_th not in messages[0]["content"]


def test_build_messages_is_stable_across_the_whole_eval_set(base: Prompt) -> None:
    """Twenty items, one prompt: only the user turn may differ between them."""
    items = load_testset(TESTSET_PATH).items
    assert len(items) == 20
    systems = {build_messages(i, base)[0]["content"] for i in items}
    assert len(systems) == 1
    users = [build_messages(i, base)[1]["content"] for i in items]
    assert users == [i.transcript_th for i in items]


# ------------------------------------------------------------------- v9_16_e1
#
# `v9_16_e1` blanks the worked example's `secondary` and `third` reason values and
# changes nothing else. The gate below is the experiment's validity condition, not a
# style check: an ablation that moves two things reports one number and cannot say which
# change produced it. A base-vs-variant run is only interpretable if the diff is exactly
# what the hypothesis names.
#
# `DECIDING_RULES` is an inventory of the 27 clauses that decide an output value:
#
#     11  reason classes            (labelspaces.REASON_CLASSES)
#      4  product classes           (labelspaces.PRODUCT_CLASSES)
#      4  retention_outcome classes (labelspaces.CALL_RESULT_CLASSES)
#      6  call_event_detection classes
#      2  optional-rank rules       (2.3 Secondary, 2.5 Third)
#     --
#     27
#
# The last two are in here because they are the rules governing the very slots the
# variant blanks: if blanking the EXAMPLE also disturbed the INSTRUCTION for those slots,
# the experiment would be testing the instruction and the example at once.
#
# Each entry is anchored to its definition site rather than being the bare backticked
# token, because four tokens appear twice in the prompt (`unknown` as both a product and
# an outcome, `post to pre`, `churn` and `save` inside the `undefined` clause) and a
# count of a bare token could not tell a rule from a cross-reference.
#
# This inventory is a subset check, and `test_only_the_example_reasons_differ` is the
# superset one: everything outside the single ```json block is asserted byte-identical
# between the two prompts, which covers every rule whether or not it is listed here.

DECIDING_RULES: tuple[str, ...] = (
    # 11 reason classes
    "`network`",
    "`promotion related`",
    "`device promotion related`",
    "`save cost`",
    "`contract end` ",
    "`sale upsell problem`",
    "`dissatisfied service`",
    "`post to pre`\n",
    "`customer reason`",
    "`down sell not success`",
    "`other`",
    # 4 product classes
    "`Postpaid`: ลูกค้า Mobile",
    "`TOL`: ลูกค้า True Online",
    "`TVS`: ลูกค้า True Vision",
    "`unknown`: Can't determine the product type",
    # 4 retention_outcome classes
    "`churn`\n",
    "`save`\n",
    "`unknown` (Conversation ends before making a final decision",
    "`undefined` (Conversation irrelevant to retention",
    # 6 call_event_detection classes
    "`Market-Driven Events (เหตุการณ์ทางการตลาด)`",
    "`Crisis & Emergency Events (เหตุการณ์วิกฤตหรือภัยพิบัติ)`",
    "`Campaign-Drvien Events (เหตุการณ์ด้านเคมเปญต่างๆของบริษัท)`",
    "`Technology & Service Events (เหตุการณ์ด้านเทคโนโลยี/บริการ)`",
    "`True-DTAC Merger(การรวมกิจการของ True และ ดีแทค)`",
    "`Emerging or Undefined Events (เหตุผลที่ยังไม่สามารถจัดกลุ่มได้)`",
    # 2 optional-rank rules -- the slots this variant blanks
    "2.3. Secondary: (Optional)",
    "2.5. Third: (Optional)",
)

# The two lines the variant is allowed to change, and only these.
EXPECTED_LINE_CHANGES: tuple[tuple[str, str], ...] = (
    ('                "reason": "save cost",', '                "reason": "",'),
    ('                "reason": "dissatisfied service",', '                "reason": "",'),
)


@pytest.fixture(scope="module")
def e1() -> Prompt:
    return PROMPTS["v9_16_e1"]


@pytest.fixture(scope="module")
def e1_example(e1: Prompt) -> dict:
    blocks = re.findall(r"```json\n(.*?)\n```", e1.system_text, re.S)
    assert len(blocks) == 1, f"expected exactly one ```json block, found {len(blocks)}"
    return json.loads(blocks[0])


def _example_span(text: str) -> tuple[int, int]:
    """Character bounds of the single ```json ... ``` block."""
    matches = list(re.finditer(r"```json\n.*?\n```", text, re.S))
    assert len(matches) == 1, f"expected exactly one ```json block, found {len(matches)}"
    return matches[0].span()


def test_e1_is_registered_and_distinct(base: Prompt, e1: Prompt) -> None:
    """Two ids, two shas, one file. A variant sharing the base's sha is not a variant.

    The registry gained `v9_16_q1` and then `v9_16_q2` on 2026-08-09 (EXPERIMENTS.md,
    Experiment 9 -- phase-two tuning authorised for the first time; q2 is iteration 2). The id list is asserted in full
    rather than loosened to a membership check: a prompt appearing in the registry
    without anyone noticing is exactly what this line is here to prevent, and every arm
    that ever ran cites its prompt id.
    """
    assert sorted(PROMPTS) == ["v9_16_base", "v9_16_e1", "v9_16_q1", "v9_16_q2"]
    assert len({PROMPTS[pid].sha for pid in PROMPTS}) == len(PROMPTS)
    assert get("v9_16_e1") is e1
    assert e1.id == "v9_16_e1"
    assert e1.sha != base.sha
    import hashlib

    assert e1.sha == hashlib.sha256(e1.system_text.encode("utf-8")).hexdigest()


def test_only_the_example_reasons_differ(base: Prompt, e1: Prompt) -> None:
    """THE gate. The only differing content is the two example reason values.

    Asserted three ways, because each catches a different way of being wrong:

      1. the line diff is exactly the two replacements named above -- no third line
         moved, and no line was added or deleted;
      2. every character OUTSIDE the worked example is byte-identical, which covers the
         rules, the Role block, the Reminders and the closing instruction in one
         statement rather than by enumeration;
      3. the two examples differ at exactly two JSON paths, so a change that happened to
         balance out at the line level still fails.
    """
    base_lines = base.system_text.splitlines(keepends=True)
    e1_lines = e1.system_text.splitlines(keepends=True)
    assert len(base_lines) == len(e1_lines)

    changed = [
        (b.rstrip("\n"), e.rstrip("\n"))
        for b, e in zip(base_lines, e1_lines, strict=True)
        if b != e
    ]
    assert changed == list(EXPECTED_LINE_CHANGES)

    # (2) everything outside the worked example is untouched, byte for byte.
    b_start, b_end = _example_span(base.system_text)
    e_start, e_end = _example_span(e1.system_text)
    assert base.system_text[:b_start] == e1.system_text[:e_start]
    assert base.system_text[b_end:] == e1.system_text[e_end:]

    # (3) and the difflib view agrees, so a bug in the zip above cannot pass alone.
    ops = [
        op
        for op in difflib.SequenceMatcher(None, base_lines, e1_lines).get_opcodes()
        if op[0] != "equal"
    ]
    assert all(op[0] == "replace" for op in ops), f"insert/delete in the diff: {ops}"
    assert sum(op[2] - op[1] for op in ops) == 2


def test_e1_example_differs_from_base_at_exactly_two_json_paths(
    example: dict, e1_example: dict
) -> None:
    """The structural form of the same claim: two leaves changed, nothing else."""
    diffs = dict(_leaf_diff(example, e1_example))
    assert diffs == {
        "$.product.Postpaid.secondary.reason": ("save cost", ""),
        "$.product.Postpaid.third.reason": ("dissatisfied service", ""),
    }


def _leaf_diff(a, b, path="$") -> list[tuple[str, tuple]]:
    """Every leaf path where two parsed JSON documents disagree.

    Structural differences (a key present on one side, a type change, a list that
    changed length) are reported as a difference at that path rather than recursed into,
    so a variant that dropped a key fails with the path that names it.
    """
    if type(a) is not type(b):
        return [(path, (a, b))]
    if isinstance(a, dict):
        if set(a) != set(b):
            return [(path, (sorted(a), sorted(b)))]
        out = []
        for key in a:
            out.extend(_leaf_diff(a[key], b[key], f"{path}.{key}"))
        return out
    if isinstance(a, list):
        if len(a) != len(b):
            return [(path, (len(a), len(b)))]
        out = []
        for i, (x, y) in enumerate(zip(a, b, strict=True)):
            out.extend(_leaf_diff(x, y, f"{path}[{i}]"))
        return out
    return [] if a == b else [(path, (a, b))]


def test_the_leaf_diff_can_fail(example: dict) -> None:
    """`_leaf_diff` asserts a small set; a broken one that returns [] passes for free."""
    assert _leaf_diff(example, example) == []
    mutated = json.loads(json.dumps(example))
    mutated["recommendation"] = "changed"
    assert _leaf_diff(example, mutated) == [("$.recommendation", (example["recommendation"], "changed"))]
    dropped = json.loads(json.dumps(example))
    del dropped["product"]["Postpaid"]["third"]
    assert _leaf_diff(example, dropped), "a dropped key must be caught"


def test_e1_example_still_parses(e1_example: dict) -> None:
    """The blanked example is still one JSON object of the shape the base showed."""
    assert set(e1_example) == {"product", "call_event_detection", "recommendation"}
    assert list(e1_example["product"]) == ["Postpaid"]
    postpaid = e1_example["product"]["Postpaid"]
    assert set(postpaid) == set(example_keys := {
        "main",
        "secondary",
        "third",
        "retention_outcome",
        "network_issue",
    })
    assert example_keys  # the shape is unchanged; only two leaf values moved


def test_e1_example_is_legal_under_the_grammar_that_is_actually_sent(
    e1_example: dict,
) -> None:
    """`""` must be reachable for the copying model, or the fix causes the defect it fixes.

    This is the load-bearing check, and it is against `cli.response_format()` — the
    schema the provider CONSTRAINS the model with — not against the committed port. If
    `""` were unreachable, every model that obeyed the blanked example would land in
    `schema_violation` and the experiment would be measuring the grammar rather than the
    example.

    It is reachable only because of `decoding.decoding_schema` deviation 2, which adds
    `""` to every `reason` enum. That deviation predates this variant and was added for
    the same underlying reason: `secondary`/`third` declare `required: [reason, keyword]`,
    so a model that enters the object must emit SOME reason, and without `""` in the enum
    the only reachable values are eleven real labels. `v9_16_e1` therefore depends on
    that deviation; a run of this prompt without it would be uninterpretable.
    """
    from evalgen.cli import response_format

    sent = response_format()["json_schema"]["schema"]
    reason = sent["properties"]["product"]["properties"]["Postpaid"]["properties"][
        "secondary"
    ]["properties"]["reason"]
    assert "" in reason["enum"], (
        "decoding deviation 2 is gone; the blanked example is now unreachable and "
        "v9_16_e1 would score its own grammar"
    )
    # `network_issue` is stripped by the same transform, so validate the part of the
    # example the grammar still describes.
    trimmed = json.loads(json.dumps(e1_example))
    trimmed["product"]["Postpaid"].pop("network_issue", None)
    assert _validate(trimmed, sent) == []


def test_e1_example_fails_the_committed_port_schema_at_exactly_the_blanked_slots(
    e1_example: dict, schema: dict
) -> None:
    """The port schema forbids `""`, and this pins where that costs and where it does not.

    `schemas/retention.json` is a line-by-line port of production's declaration and is
    NOT edited here — that would be a second change in a one-change experiment. It
    contradicts itself and has since before this variant: every `reason.description`
    says "Use empty string if not applicable" while the enum omits `""`.

    Asserting the failure rather than skipping the check keeps the contradiction visible
    and bounded: exactly two paths, both of them the ones this variant blanked. If a
    third path ever appears here, the variant broke something the port cares about.
    """
    errors = _validate(e1_example, schema)
    paths = sorted(err.split(":")[0] for err in errors)
    assert paths == [
        "$.product.Postpaid.secondary.reason",
        "$.product.Postpaid.third.reason",
    ]
    # And the base is clean against the same schema, so this is the variant's doing and
    # not a pre-existing breakage in the example.
    assert _validate(example_of(schema), schema) == []


def example_of(_schema: dict) -> dict:
    """The base's worked example, re-parsed. Kept as a function so the assertion above
    reads as "the base is clean" rather than depending on fixture ordering."""
    blocks = re.findall(r"```json\n(.*?)\n```", PROMPTS["v9_16_base"].system_text, re.S)
    return json.loads(blocks[0])


def test_e1_leaves_main_alone(base: Prompt, e1: Prompt, e1_example: dict) -> None:
    """`main` demonstrates the required shape and is explicitly out of scope.

    Checked on the parsed example AND on the raw text, because a substitution that hit
    the right JSON path by accident of anchoring is still an accident.
    """
    main = e1_example["product"]["Postpaid"]["main"]
    assert main["reason"] == EXAMPLE_REASON_VALUES["main"] == "network"
    assert main["keyword"] == "คำพูดของลูกค้าที่สื่อถึงเหตุผล"
    anchor = '"main": {\n                "reason": "network",'
    assert base.system_text.count(anchor) == 1
    assert e1.system_text.count(anchor) == 1

    # And the two slots that ARE in scope really are blank in e1 and filled in the base.
    for slot in ("secondary", "third"):
        assert e1_example["product"]["Postpaid"][slot]["reason"] == ""
        assert e1_example["product"]["Postpaid"][slot]["keyword"] == (
            "คำพูดของลูกค้าที่สื่อถึงเหตุผล"
        ), "only the reason value moves; the keyword placeholder is not in scope"


def test_all_27_deciding_rules_survive_in_both_prompts(base: Prompt, e1: Prompt) -> None:
    """Every rule that decides an output value is present exactly once in both prompts.

    Exactly once, not merely present: a rule appearing twice is the doubled-monolith
    accident that `test_role_line_appears_exactly_once` guards at the whole-prompt level,
    and it would reach the variant through the base without this.
    """
    assert len(DECIDING_RULES) == 27
    assert len(set(DECIDING_RULES)) == 27
    for prompt in (base, e1):
        for rule in DECIDING_RULES:
            assert prompt.system_text.count(rule) == 1, (
                f"{prompt.id}: deciding rule {rule!r} appears "
                f"{prompt.system_text.count(rule)} times, expected 1"
            )


def test_the_rules_inventory_covers_the_committed_label_spaces() -> None:
    """The inventory is checked against `labelspaces`, not just against itself.

    A hand-written list of rule strings is only as good as its completeness, and a rule
    quietly dropped from the list would make the gate above pass while the prompt lost a
    class. `labelspaces.py` is transcribed from production and is the authority on how
    many classes there are.
    """
    from evalharness.labelspaces import RETENTION

    assert len(RETENTION.reason) == 11
    assert len(RETENTION.product) == 4
    assert len(RETENTION.call_result) == 4
    # 11 + 4 + 4 + 6 call-event classes + 2 optional-rank rules
    assert 11 + 4 + 4 + 6 + 2 == len(DECIDING_RULES)
    for label in RETENTION.reason:
        assert any(f"`{label}`" in rule for rule in DECIDING_RULES), label
    # Product classes are cased differently in the two artifacts: the prompt writes
    # `Postpaid`/`TOL`/`TVS`, `labelspaces` holds the normalised forms the scorer merges
    # on. Fold before comparing rather than editing either side.
    folded = [rule.lower() for rule in DECIDING_RULES]
    for label in RETENTION.product:
        assert any(f"`{label}`" in rule for rule in folded), label
    for label in RETENTION.call_result:
        assert any(f"`{label}`" in rule for rule in folded), label


def test_the_role_block_still_appears_exactly_once_in_e1(e1: Prompt) -> None:
    """The base's headline gate, re-run on the variant rather than assumed to carry."""
    assert e1.system_text.count(ROLE_LINE) == 1
    assert e1.system_text.count(ROLE_MARKER) == 1
    assert e1.system_text.count("**Objective**:") == 1
    assert e1.system_text.count("Output Format:") == 1
    assert e1.system_text.count("**Reminder:**") == 1
    assert e1.system_text.count("```json") == 1
    assert USER_PROMPT_PLACEHOLDER not in e1.system_text
    assert "audio" not in e1.system_text.lower()


# ------------------------------------------------------- the variant mechanism itself


def test_build_variant_refuses_an_anchor_that_does_not_fire_once(base: Prompt) -> None:
    """An edit that silently did not apply ships the base under the variant's name.

    Both directions: zero matches means the ablation did not happen, more than one means
    it happened somewhere nobody reviewed. Either way the run's `prompt_id` would name a
    prompt that is not the text that was sent.
    """
    absent = Variant(
        id="v_absent",
        edits=(Edit(before="no such text in the prompt", after="x", why="test"),),
        notes="",
    )
    with pytest.raises(PromptError) as exc:
        build_variant(absent, base)
    assert "matched 0 times" in str(exc.value)

    doubled = Variant(
        id="v_doubled",
        edits=(Edit(before="Definition:", after="x", why="test"),),
        notes="",
    )
    with pytest.raises(PromptError) as exc:
        build_variant(doubled, base)
    assert "expected exactly" in str(exc.value)


def test_build_variant_refuses_a_no_op(base: Prompt) -> None:
    """A variant identical to its base is two arms on one prompt and must not build."""
    noop = Variant(
        id="v_noop",
        edits=(Edit(before="**Objective**:", after="**Objective**:", why="test"),),
        notes="",
    )
    with pytest.raises(PromptError) as exc:
        build_variant(noop, base)
    assert "identical" in str(exc.value)


def test_the_variant_is_data_not_a_forked_file() -> None:
    """One asset pair on disk, and every edit carries its justification.

    A second wrapper file would let the base and the variant drift by something other
    than the ablation, and the run comparing them could not attribute its own result.
    """
    assets = sorted(p.name for p in WRAPPER_PATH.parent.glob("*.txt"))
    assert assets == ["retention_v9_16_body.txt", "retention_wrapper.txt"]
    spec = VARIANTS["v9_16_e1"]
    assert len(spec.edits) == 2
    for edit in spec.edits:
        assert edit.why.strip(), "an edit without a stated reason is an unattributable change"
        assert edit.after.count('"reason": ""') == 1
    assert spec.notes.strip()


def test_the_variant_edits_are_built_from_the_named_example_values() -> None:
    """`EXAMPLE_REASON_VALUES` is the single source both the edits and `fabrication` use.

    Re-typing the three example values in a second place is how a fabrication counter
    ends up measuring against values the prompt no longer contains.
    """
    assert EXAMPLE_REASON_VALUES == {
        "main": "network",
        "secondary": "save cost",
        "third": "dissatisfied service",
    }
    wrapper = WRAPPER_PATH.read_text(encoding="utf-8")
    for slot, value in EXAMPLE_REASON_VALUES.items():
        assert f'"{slot}": {{\n                "reason": "{value}",' in wrapper


# ------------------------------------- phase-two: model-targeted variants (2026-08-09)


def test_a_variant_can_declare_its_target_models_phase_and_version():
    """The prompt manifest's own `phase_two_protocol` requires "a new prompt id with
    parent_id and target_models". `build_variant` used to hardcode all three, so that
    requirement was not expressible in code at all."""
    from evalgen.prompts import Edit, Variant, build_base, build_variant

    base = build_base()
    anchor = "**Role**: You are a call center agent"
    assert base.system_text.count(anchor) == 1
    variant = Variant(
        id="v9_16_probe",
        edits=(Edit(before=anchor, after=anchor + " (probe)", why="test fixture"),),
        notes="a probe variant, never registered",
        version="9.16-probe.1",
        target_models=("qwen/qwen3.6-27b",),
        phase="phase_two_tuned",
    )
    built = build_variant(variant, base)
    assert built.parent_id == base.id
    assert built.version == "9.16-probe.1"
    assert built.target_models == ("qwen/qwen3.6-27b",)
    assert built.phase == "phase_two_tuned"


def test_the_variant_defaults_reproduce_the_previous_hardcoded_behaviour():
    """`v9_16_e1` must not move: its sha, version, phase and target_models are pinned in
    prompts/manifest.json and quoted in four experiments."""
    from evalgen.prompts import PROMPTS, validate_manifest

    e1 = PROMPTS["v9_16_e1"]
    assert e1.version == "9.16-e1-harness.1"
    assert e1.phase == "historical_ablation"
    assert e1.target_models == ("*",)
    assert e1.sha == "143d34c97fbd943cbb4698a2eb9d1095778571860cda98030e94d2a68628d380"
    assert validate_manifest() == []


def test_a_variant_whose_declaration_drifts_from_the_catalogue_is_caught():
    """The safety net that makes the new fields safe: `validate_manifest` compares the
    executable registry against prompts/manifest.json on exactly these fields."""
    from dataclasses import replace

    from evalgen.prompts import PROMPTS, validate_manifest

    drifted = dict(PROMPTS)
    drifted["v9_16_e1"] = replace(PROMPTS["v9_16_e1"], target_models=("qwen/qwen3.6-27b",))
    problems = validate_manifest(registry=drifted)
    assert any("target_models" in problem for problem in problems), problems
    # and the undrifted registry still passes, so the check is not simply always failing
    assert validate_manifest(registry=PROMPTS) == []


# ------------------------------------------------ v9_16_q1: the phase-two Qwen prompt


def _q1_and_base():
    from evalgen.prompts import PROMPTS, build_base

    return PROMPTS["v9_16_q1"], build_base()


def test_q1_declares_itself_as_a_model_targeted_phase_two_child():
    """Requirement 1 of prompts/manifest.json's phase_two_protocol."""
    q1, base = _q1_and_base()
    assert q1.parent_id == base.id
    assert q1.target_models == ("qwen/qwen3.6-27b",)
    assert q1.phase == "phase_two_tuned"
    assert q1.sha != base.sha


def test_q1_deletes_and_rewrites_nothing_in_the_base():
    """Every edit is an append after an anchor, so the base text survives line for line.
    This is what makes the next test's claim checkable at all: if a base line could
    vanish, 'no scored instruction changed' would need a semantic argument instead of a
    set comparison."""
    q1, base = _q1_and_base()
    q1_lines = q1.system_text.split("\n")
    for line in base.system_text.split("\n"):
        assert line in q1_lines, f"the base line {line!r} is missing from v9_16_q1"


def test_q1_touches_no_scored_content():
    """The rule this variant ships under: it may constrain unscored free text and nothing
    else. Every label of every scored dimension must appear exactly as often as it does in
    the parent -- a class the tuned prompt mentions more or less often is a tuned rule,
    not a tuned format."""
    from evalharness.labelspaces import (
        CALL_RESULT_CLASSES,
        PRODUCT_CLASSES,
        REASON_CLASSES,
    )

    q1, base = _q1_and_base()
    for label in (*REASON_CLASSES, *CALL_RESULT_CLASSES, *PRODUCT_CLASSES):
        assert q1.system_text.count(label) == base.system_text.count(label), (
            f"the scored label {label!r} appears a different number of times in "
            "v9_16_q1 than in its parent"
        )


def test_every_line_q1_adds_is_about_an_unscored_field():
    """The added text is auditable: each new line has to sit under one of the three
    unscored-field instructions, and none may name a scored class."""
    from evalharness.labelspaces import CALL_RESULT_CLASSES, REASON_CLASSES

    q1, base = _q1_and_base()
    added = [
        line
        for line in q1.system_text.split("\n")
        if line not in base.system_text.split("\n")
    ]
    assert added, "a variant that adds nothing is two arms on one prompt"
    for line in added:
        for label in (*REASON_CLASSES, *CALL_RESULT_CLASSES):
            assert label not in line, f"added line names the scored class {label!r}: {line!r}"


def test_q1_constrains_exactly_the_three_measured_unscored_fields():
    q1, base = _q1_and_base()
    added = "\n".join(
        line
        for line in q1.system_text.split("\n")
        if line not in base.system_text.split("\n")
    )
    # recommendation: length and wording pinned
    assert "at most 20 words" in added
    # call_event_detection: a tie-break, so the enum choice stops moving
    assert "listed FIRST" in added
    # the quoted phrase behind `keyword`: contiguous, unmodified, repeatable
    assert "character for character" in added


def test_every_q1_edit_states_why_with_a_measured_number():
    """`Edit.why` is required by the dataclass; this asserts the reasons are grounded in
    the decomposition rather than being restatements of the change."""
    from evalgen.prompts import VARIANTS

    for edit in VARIANTS["v9_16_q1"].edits:
        assert edit.why.strip()
        assert any(char.isdigit() for char in edit.why), (
            f"an edit's reason cites no measurement: {edit.why!r}"
        )


def test_the_phase_one_manifest_is_untouched_by_the_phase_two_prompt():
    """`manifest.json`'s sha256 is pinned as an asset in two EXECUTED experiment plans.
    A phase-two entry added there would make those plans describe something other than
    what was run, so phase two is catalogued separately."""
    import hashlib
    import json as _json

    from evalgen.prompts import PHASE_TWO_MANIFEST_PATH, PROMPT_MANIFEST_PATH

    pinned = "d49d85055637156ba9cbc7f79e73a8ec4c0e844be6a10d295a033c19eef56206"
    assert hashlib.sha256(PROMPT_MANIFEST_PATH.read_bytes()).hexdigest() == pinned
    phase_one_ids = {
        entry["id"]
        for entry in _json.loads(PROMPT_MANIFEST_PATH.read_text(encoding="utf-8"))["prompts"]
    }
    assert not {"v9_16_q1", "v9_16_q2"} & phase_one_ids
    phase_two_ids = {
        entry["id"]
        for entry in _json.loads(PHASE_TWO_MANIFEST_PATH.read_text(encoding="utf-8"))["prompts"]
    }
    assert phase_two_ids == {"v9_16_q1", "v9_16_q2"}
