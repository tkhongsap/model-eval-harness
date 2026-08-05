"""What counts as a parsed response, pinned case by case.

`outcomes.classify` is the only place `parse_ok` is decided, so these tests are the
only place it is checked. Two things are being defended:

  * **The classification itself.** An empty response from a reasoning model, a fenced
    object, prose-wrapped JSON and outright garbage are four different facts about a
    model, and collapsing any pair of them loses the diagnosis.
  * **The import boundary.** `outcomes.py` must run without `openai` installed. The
    root `requirements.txt` does not carry the SDK, so CI runs this suite in an
    environment where a stray import would be a hard failure rather than a style
    complaint. The last two tests prove it statically and dynamically.
"""

from __future__ import annotations

import ast
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from evalgen.outcomes import (  # noqa: E402
    REPAIR_FIRST_OBJECT,
    REPAIR_STRIP_BOM,
    REPAIR_STRIP_FENCE,
    Classified,
    classify,
    transport_error,
)
# Imported private on purpose: two tests below assert that the marker they use IS in
# this list, so that they discriminate the WINDOW rather than passing because the
# marker was quietly removed.
from evalgen.outcomes import _REFUSAL_MARKERS  # noqa: E402

OUTCOMES_PY = ROOT / "src" / "evalgen" / "outcomes.py"


# --- the four cases named in the brief ------------------------------------------------


def test_empty_content_with_length_finish_is_empty_length():
    """The measured failure mode: a reasoning model that never got to answer.

    On a one-word prompt, `gemini-3.6-flash` spent 85 of 86 completion tokens on
    reasoning and `qwen3.7-flash` spent 149 of 155 (commit 9e29034). With a small
    `max_tokens` the budget is gone before a single visible token is emitted, and the
    smoke test reported PASS on exactly this, twice, because its only criterion was
    that the HTTP call did not raise.

    Distinguishing it from `not_json` is the entire point: this is a run-configuration
    bug, and filing it under a parse failure blames the model for the harness's
    `max_tokens`.
    """
    result = classify("", "length")

    assert result.outcome == "empty_length"
    assert result.parse_ok is False
    assert result.truncated is True
    assert result.payload is None


def test_fenced_json_is_ok_and_records_the_repair():
    """Markdown fencing is presentation, not content, so unwrapping loses nothing.

    The repair is *recorded* rather than silently applied. A run where most items
    needed unwrapping and a run where none did are different results about how well a
    model follows a JSON instruction, and they must not look identical downstream.
    """
    content = '```json\n{"reason": "network", "retention_outcome": "churn"}\n```'

    result = classify(content, "stop")

    assert result.outcome == "ok"
    assert result.parse_ok is True
    assert result.payload == {"reason": "network", "retention_outcome": "churn"}
    assert REPAIR_STRIP_FENCE in result.repairs


def test_garbage_is_not_json():
    """Prose with no recoverable object. No repair can rescue it, and none pretends to."""
    result = classify("Sure! The customer seems unhappy about the network.", "stop")

    assert result.outcome == "not_json"
    assert result.parse_ok is False
    assert result.payload is None
    assert result.repairs == ()


def test_valid_json_is_ok_with_no_repairs():
    """The clean case, and the baseline the repair counts are read against.

    `repairs == ()` is asserted, not incidental: if well-formed output quietly
    accumulated a repair, the repair rate would stop meaning anything.
    """
    result = classify('{"reason": "save cost"}', "stop")

    assert result.outcome == "ok"
    assert result.parse_ok is True
    assert result.payload == {"reason": "save cost"}
    assert result.repairs == ()
    assert result.truncated is False


# --- the rest of the vocabulary --------------------------------------------------------


def test_empty_content_without_length_finish_is_empty_other():
    """Empty output that did NOT exhaust the budget is a different fact.

    Observed, not hypothetical: `qwen3.6-27b` returned an empty response on run 2 of
    three identical Thai round-trips at `MAX_TOKENS = 2000`
    (scripts/openrouter-smoketest/README.md:140-148). Merging it into `empty_length`
    would hide a model that stops for its own reasons behind a budget that was fine.
    """
    result = classify("", "stop")

    assert result.outcome == "empty_other"
    assert result.truncated is False


def test_none_content_is_empty_other():
    """The SDK returns None, not "", when a message carries no text at all."""
    assert classify(None, "stop").outcome == "empty_other"


def test_whitespace_only_content_is_empty():
    """A newline is not an answer."""
    assert classify("   \n\t  ", "stop").outcome == "empty_other"


def test_refusal_is_separated_from_not_json():
    """A model that declines is not a model that failed to format.

    Both are `parse_ok False`, so this distinction can never inflate coverage. It
    exists because the two demand opposite responses: a refusal points at the prompt
    or the content policy, a `not_json` points at decoding.
    """
    result = classify("I'm sorry, I cannot analyse this call recording.", "stop")

    assert result.outcome == "refusal"
    assert result.parse_ok is False


def test_thai_refusal_is_detected():
    """The corpus is Thai, so a Thai refusal must not be filed as malformed JSON."""
    assert classify("ขออภัย ไม่สามารถวิเคราะห์ข้อมูลนี้ได้", "stop").outcome == "refusal"


def test_thai_transcript_echo_inside_a_broken_answer_is_not_a_refusal():
    """The corpus contains the refusal markers, so WHERE they appear has to matter.

    MEASURED, on RET-01 with the un-transformed schema: `gemini-2.5-flash` degenerated
    into 23,529 characters of repetition inside `network_issue.sub_reason`, echoing the
    agent's own `ต้องขอโทษด้วยนะคะ` out of the transcript. The old rule -- any marker
    anywhere in the text -- reported it as the model REFUSING the task, when it was a
    token-budget and grammar pathology (`decoding.py`, deviation 1).

    This test is self-discriminating: the first assertion establishes that the old
    rule's premise still holds (the marker IS in the text), and the second shows the
    verdict is no longer `refusal`. It cannot pass by the marker having quietly been
    deleted from the list.
    """
    degenerate = '{"product": {"Postpaid": {"main": {"keyword": "ต้องขอโทษด้วยนะคะ' + "ก" * 500

    assert any(m in degenerate.lower() for m in _REFUSAL_MARKERS), (
        "the premise of this test is that a Thai politeness marker is present; without "
        "one it would pass against the old rule too and prove nothing"
    )
    result = classify(degenerate, "length")

    assert result.outcome == "not_json", (
        "a response that opened a JSON object and then ran out of budget is a decoder "
        "or grammar problem. Filed as `refusal` it becomes the report's headline and "
        "sends a reader to the prompt and the content policy instead."
    )
    assert result.truncated is True


def test_a_refusal_that_arrives_late_in_a_long_answer_is_not_believed():
    """The window, not the marker list. A decline happens up front or it is not one."""
    buried = "x" * 400 + " I'm sorry, I cannot analyse this call recording."

    assert any(m in buried.lower() for m in _REFUSAL_MARKERS)
    assert classify(buried, "stop").outcome == "not_json"


def test_provider_error_is_not_charged_to_the_model():
    """`finish_reason == "error"` is a fact about the route, not about the model.

    MEASURED on 2026-08-04: `google/gemini-2.5-flash` returned HTTP 200 with
    `finish_reason="error"`, 86 characters of a half-written object,
    `completion_tokens=0`, `cost=$0.00000`, and `choices[0].error` reading
    `{"code": 429, ..., "error_type": "rate_limit_exceeded"}`. A 429 delivered inside a
    200.

    The three assertions below are the three reasons it used to land in `not_json`:
    the content is not empty, it does not parse, and it carries no refusal marker.
    They are asserted rather than described so this test cannot start passing for a
    different reason than it was written for.
    """
    fragment = '{\n    "product": {\n        "Postpaid": {\n            "main": {\n     "reason'

    assert fragment.strip(), "not empty, so `_classify_empty` never fires"
    with pytest.raises(ValueError):
        json.loads(fragment)  # does not parse, under any loss-free repair
    assert not any(m in fragment.lower() for m in _REFUSAL_MARKERS)

    result = classify(fragment, "error")

    assert result.outcome == "provider_error", (
        "filed as not_json, a provider-side rate limit is charged to the model's Thai "
        "JSON competence, and the arm that happened to be throttled looks worse at the "
        "task."
    )
    assert result.parse_ok is False
    assert result.payload is None


def test_provider_error_wins_over_an_empty_body():
    """An abandoned generation with no content at all is still the route's failure.

    Ordered before the empty check on purpose: `empty_other` is documented as "a
    genuinely different fact about the model", and this is not a fact about the model.
    """
    assert classify("", "error").outcome == "provider_error"
    assert classify(None, "error").outcome == "provider_error"


def test_valid_json_containing_refusal_words_is_still_ok():
    """Refusal detection runs only after parsing has already failed.

    A transcript field can legitimately quote a customer saying "I cannot", and a
    keyword scan that ran before parsing would throw away a perfectly good answer.
    """
    result = classify('{"note": "the agent said I cannot help with that"}', "stop")

    assert result.outcome == "ok"
    assert result.payload is not None


def test_top_level_array_is_a_schema_violation_not_not_json():
    """Valid JSON of the wrong shape is a different diagnosis from unparseable output.

    The model demonstrably can emit JSON; it chose a different structure. That points
    at the prompt, so it must not be filed alongside decoder failures.
    """
    result = classify('[{"reason": "network"}]', "stop")

    assert result.outcome == "schema_violation"
    assert result.payload is None
    assert result.parse_ok is False


def test_missing_required_key_is_a_schema_violation_that_keeps_its_payload():
    """The next question a reviewer asks is "so what DID it emit". Keep the answer.

    Safe because `parse_ok` is already False, so the payload cannot reach a score.
    """
    result = classify(
        '{"reason": "network"}', "stop", required_keys=("reason", "retention_outcome")
    )

    assert result.outcome == "schema_violation"
    assert result.payload == {"reason": "network"}
    assert result.parse_ok is False


def test_all_required_keys_present_is_ok():
    """Presence is checked, not type or value. The label vocabulary is validated
    downstream against the class lists transcribed from production."""
    result = classify(
        '{"reason": "network", "retention_outcome": "save"}',
        "stop",
        required_keys=("reason", "retention_outcome"),
    )

    assert result.outcome == "ok"


# --- repairs, individually -------------------------------------------------------------


def test_prose_wrapped_json_is_recovered_and_recorded():
    """Chat models preface answers. Selecting the object discards only the preface."""
    content = 'Here is the analysis:\n{"reason": "contract end"}\nHope that helps!'

    result = classify(content, "stop")

    assert result.outcome == "ok"
    assert result.payload == {"reason": "contract end"}
    assert REPAIR_FIRST_OBJECT in result.repairs


def test_bom_is_stripped_and_recorded():
    """A byte-order mark carries no content, but `json.loads` rejects it outright."""
    result = classify('﻿{"reason": "network"}', "stop")

    assert result.outcome == "ok"
    assert REPAIR_STRIP_BOM in result.repairs


def test_repairs_accumulate_in_order():
    """A BOM in front of a fenced object needs both repairs, and both are reported."""
    result = classify('﻿```json\n{"reason": "other"}\n```', "stop")

    assert result.outcome == "ok"
    assert result.repairs == (REPAIR_STRIP_BOM, REPAIR_STRIP_FENCE)


def test_brace_inside_a_string_does_not_end_the_object():
    """The object scanner is string-aware, or it truncates at the wrong brace.

    A naive depth count stops at the `}` inside the quoted value and hands
    `json.loads` a fragment, which then gets reported as `not_json` -- a model blamed
    for a bug in the harness's own scanner.
    """
    content = 'Result: {"note": "customer said } was odd", "reason": "other"}'

    result = classify(content, "stop")

    assert result.outcome == "ok"
    assert result.payload == {"note": "customer said } was odd", "reason": "other"}


def test_escaped_quote_inside_a_string_is_handled():
    """Backslash escapes must not flip the in-string state."""
    content = '{"note": "he said \\"cancel\\" then }", "reason": "other"}'

    result = classify(content, "stop")

    assert result.outcome == "ok"
    assert result.payload["reason"] == "other"


def test_truncated_json_is_not_silently_completed():
    """Repairs are loss-free, so a cut-off object stays a failure.

    Balancing the braces here would invent a value the model never emitted, and it
    would score. The unbalanced object is reported as `not_json` with `truncated`
    True, which says both what happened and why.
    """
    result = classify('{"reason": "network", "retention_outcome": "sa', "length")

    assert result.outcome == "not_json"
    assert result.truncated is True
    assert result.payload is None


def test_thai_payload_survives_classification_exactly():
    """The evaluation data is Thai. A byte-for-byte round trip is the minimum bar.

    The smoke test already caught a model returning corrupted Thai; the classifier
    must not add corruption of its own on top of that.
    """
    thai = "ลูกค้าต้องการยกเลิกบริการเพราะสัญญาณไม่ดี"
    result = classify(f'```json\n{{"transcript": "{thai}"}}\n```', "stop")

    assert result.outcome == "ok"
    assert result.payload["transcript"] == thai


def test_complete_object_before_a_length_cutoff_is_still_ok_but_flagged():
    """Truncation after a complete object does not destroy the object.

    `truncated` is independent of the outcome so the risk is recorded without
    discarding a usable answer.
    """
    result = classify('{"reason": "network"}\nand then I would also', "length")

    assert result.outcome == "ok"
    assert result.truncated is True


# --- the flag itself -------------------------------------------------------------------


def test_parse_ok_is_true_for_ok_alone():
    """Every outcome in the vocabulary except `ok` must be False, by construction.

    Enumerated rather than trusted, because `parse_ok` is the one boolean the whole
    coverage comparison rests on.
    """
    failures = (
        "empty_length",
        "empty_other",
        "refusal",
        "not_json",
        "schema_violation",
        "transport_error",
        "provider_error",
    )
    for outcome in failures:
        assert (
            Classified(outcome=outcome, payload=None, repairs=(), truncated=False).parse_ok
            is False
        ), f"{outcome} must not count as parsed"

    assert Classified(outcome="ok", payload={}, repairs=(), truncated=False).parse_ok is True


def test_transport_error_factory():
    """A request that never produced a response still needs an outcome, in the same
    vocabulary, so it lands in the same coverage accounting."""
    result = transport_error()

    assert result.outcome == "transport_error"
    assert result.parse_ok is False
    assert result.payload is None


# --- the import boundary ---------------------------------------------------------------


def test_outcomes_module_declares_no_client_import():
    """Static check: no `openai` import anywhere in the file, running or not.

    The AST is parsed rather than the module inspected, for the reason
    `tests/test_boundary.py` gives about `src/evalharness/`: an import tucked inside a
    function body or behind `if TYPE_CHECKING` never executes, so `sys.modules` would
    not see it while the dependency is real.
    """
    tree = ast.parse(OUTCOMES_PY.read_text(encoding="utf-8"), filename=str(OUTCOMES_PY))

    offenders = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name.split(".")[0] == "openai":
                    offenders.append(node.lineno)
        elif isinstance(node, ast.ImportFrom):
            if not node.level and (node.module or "").split(".")[0] == "openai":
                offenders.append(node.lineno)

    assert not offenders, (
        f"src/evalgen/outcomes.py imports openai at line(s) {offenders}. The rule that "
        "decides parse_ok must stay importable where the SDK is not installed; the "
        "client belongs in client.py, which is the only module allowed to import it."
    )


def test_importing_outcomes_does_not_pull_in_the_sdk():
    """Dynamic check: the whole transitive import graph stays clean.

    The static test above reads one file. This one imports the module in a fresh
    interpreter and asserts `openai` never reached `sys.modules`, which also catches
    the SDK arriving through some future helper `outcomes.py` imports. Between them,
    the claim holds whether the dependency is direct or inherited.
    """
    probe = (
        "import sys\n"
        "import evalgen.outcomes\n"
        "assert 'openai' not in sys.modules, sorted(m for m in sys.modules if 'openai' in m)\n"
    )
    env = dict(os.environ)
    env["PYTHONPATH"] = str(ROOT / "src")

    completed = subprocess.run(
        [sys.executable, "-c", probe],
        capture_output=True,
        text=True,
        env=env,
    )

    assert completed.returncode == 0, (
        "Importing evalgen.outcomes loaded the openai SDK:\n"
        f"{completed.stdout}\n{completed.stderr}"
    )
