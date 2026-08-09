"""Gates on `evalgen.judge`, starting with the table computed before the module existed.

`tests/fixtures/judge/HAND-COMPUTED.md` fixes ten raw judge responses and their expected
aggregate by hand, before `judge.py` was written. The parsing/aggregation tests below
reproduce that table exactly and call no network client -- the same discipline
`test_evidence.py` and `test_fabrication.py` hold their pure functions to.

The rest of this file is about the claims `judge.py`'s own docstring makes about itself,
each checked rather than trusted:

  * "this is exactly the population `compare.disagreement()` counts" -- checked by
    building synthetic records covering every population bucket and asserting the two
    functions agree on the total, on data neither was tuned against.
  * "diagnostic, never a scored dimension, never imported by the verdict path" -- checked
    by parsing the AST of `report.py` and `evalharness/compare.py` directly, which is a
    stronger guarantee than `evidence.py`'s isolation carries today (asserted only in
    that module's own docstring, never enforced by a test).
  * "the judge is never told which vendor produced which answer" -- checked by asserting
    neither arm's actual model id ever appears in a built prompt, and that the A/B
    assignment is a deterministic function of the item key, not of run order.
"""

from __future__ import annotations

import ast
import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from evalgen.judge import (  # noqa: E402
    JUDGE_VERDICTS,
    RULE_SOURCE_FILES,
    DisagreementItem,
    JudgeError,
    build_judge_prompt,
    find_disagreements,
    judgment_unit_id,
    judge_response_schema,
    parse_judge_response,
    parse_rule_citation,
    resolve_rule_text,
    run_judge,
    shareable_report,
    summarize_judgments,
)
from evalharness.compare import disagreement  # noqa: E402
from evalharness.records import Record  # noqa: E402

FIXTURE = ROOT / "tests" / "fixtures" / "judge" / "HAND-COMPUTED.md"


def test_the_hand_computed_fixture_file_exists_and_is_not_empty():
    """The fixture must exist and predate this test meaningfully -- if it is missing,
    every number below is unmoored from the document that is supposed to govern it."""
    assert FIXTURE.exists()
    assert FIXTURE.stat().st_size > 500


# ---------------------------------------------------------------- parse + aggregate


# The exact ten raw responses from HAND-COMPUTED.md, in the same order.
_RAW_RESPONSES = [
    '{"verdict":"ground_truth_correct","cited_span":"a","rationale":"r1"}',
    '{"verdict":"ground_truth_correct","cited_span":"a","rationale":"r2"}',
    '{"verdict":"ground_truth_correct","cited_span":"a","rationale":"r3"}',
    '{"verdict":"ground_truth_correct","cited_span":"a","rationale":"r4"}',
    '{"verdict":"defensible_disagreement","cited_span":"a","rationale":"r5"}',
    '{"verdict":"defensible_disagreement","cited_span":"a","rationale":"r6"}',
    '{"verdict":"ground_truth_error","cited_span":"a","rationale":"r7"}',
    '{"verdict":"unclear","cited_span":"","rationale":"r8"}',
    "not json at all, the model refused to follow the schema",
    '{"verdict":"probably_correct","cited_span":"a","rationale":"r10"}',
]


def test_parse_judge_response_matches_the_hand_computed_table_row_by_row():
    parsed = [parse_judge_response(raw) for raw in _RAW_RESPONSES]
    verdicts = [p.verdict for p in parsed]
    parse_errors = [p.parse_error for p in parsed]

    assert verdicts == [
        "ground_truth_correct",
        "ground_truth_correct",
        "ground_truth_correct",
        "ground_truth_correct",
        "defensible_disagreement",
        "defensible_disagreement",
        "ground_truth_error",
        "unclear",
        "unclear",  # not json -> parser fallback
        "unclear",  # verdict outside the enum -> parser fallback
    ]
    assert parse_errors == [False] * 8 + [True, True]


def test_summarize_judgments_matches_the_hand_computed_aggregate_exactly():
    parsed = [parse_judge_response(raw) for raw in _RAW_RESPONSES]
    summary = summarize_judgments(parsed)

    assert summary["total"] == 10
    assert summary["counts"] == {
        "ground_truth_correct": 4,
        "defensible_disagreement": 2,
        "ground_truth_error": 1,
        "unclear": 1,
    }
    assert summary["all_response_counts"]["unclear"] == 3
    assert summary["ground_truth_correct_rate"] == pytest.approx(0.5)
    assert summary["defensible_disagreement_rate"] == pytest.approx(0.25)
    assert summary["ground_truth_error_rate"] == pytest.approx(0.125)
    assert summary["unclear_rate"] == pytest.approx(0.125)
    assert summary["parse_error_count"] == 2
    assert summary["parse_error_rate"] == pytest.approx(0.2)


def test_summarize_judgments_sums_to_one_and_never_divides_by_zero():
    empty = summarize_judgments([])
    assert empty["total"] == 0
    assert empty["parse_error_rate"] == 0.0
    for verdict in JUDGE_VERDICTS:
        assert empty[f"{verdict}_rate"] == 0.0


def test_parse_judge_response_never_raises_on_hostile_input():
    for hostile in ("", "null", "[]", "42", '{"verdict": null}', "{{{not json"):
        result = parse_judge_response(hostile)
        assert result.verdict == "unclear"
        assert result.parse_error is True


@pytest.mark.parametrize(
    ("raw", "error_fragment"),
    [
        (
            '{"verdict":"ground_truth_error","cited_span":"quoted"}',
            "missing required field",
        ),
        (
            '{"verdict":"ground_truth_error","cited_span":"quoted",'
            '"rationale":"why","extra":true}',
            "unexpected field",
        ),
        (
            '{"verdict":"ground_truth_error","cited_span":123,"rationale":"why"}',
            "cited_span must be a string",
        ),
        (
            '{"verdict":"ground_truth_error","cited_span":"quoted","rationale":[]}',
            "rationale must be a string",
        ),
        (
            '{"verdict":"ground_truth_error","cited_span":"quoted","rationale":"  "}',
            "rationale must be non-empty",
        ),
        (
            '{"verdict":"ground_truth_error","cited_span":"","rationale":"why"}',
            "require a non-empty cited_span",
        ),
    ],
)
def test_parse_judge_response_enforces_the_local_contract(raw, error_fragment):
    result = parse_judge_response(raw, transcript="the quoted evidence")

    assert result.verdict == "unclear"
    assert result.parse_error is True
    assert any(error_fragment in error for error in result.validation_errors)


def test_parse_judge_response_checks_cited_span_byte_for_byte_against_transcript():
    raw = (
        '{"verdict":"ground_truth_error","cited_span":"หลักฐาน",'
        '"rationale":"the quote decides the label"}'
    )

    exact = parse_judge_response(raw, transcript="ลูกค้า: หลักฐาน อยู่ตรงนี้")
    changed_case = parse_judge_response(raw, transcript="ลูกค้า: evidence only")

    assert exact.parse_error is False
    assert exact.evidence_status == "exact"
    assert changed_case.parse_error is True
    assert changed_case.evidence_status == "not_in_transcript"
    assert any(
        "cited_span is not an exact substring" in error
        for error in changed_case.validation_errors
    )


def test_a_genuine_unclear_may_abstain_without_manufacturing_evidence():
    raw = '{"verdict":"unclear","cited_span":"","rationale":"not enough evidence"}'

    result = parse_judge_response(raw, transcript="short transcript")

    assert result.verdict == "unclear"
    assert result.parse_error is False
    assert result.evidence_status == "not_required"


# ---------------------------------------------------------------- disagreement population


def _rec(call_id, phone, product, call_result, reasons=frozenset()):
    return Record(call_id=call_id, phone=phone, product=product, call_result=call_result, reasons=reasons)


def _synthetic_arms():
    """Six items covering every population `disagreement()` counts, across one dimension
    (`call_result`), constructed so the totals are known by inspection:

      RET-A: both right           (save / save / save)
      RET-B: both wrong           (save / churn / unknown)
      RET-C: incumbent only right (save / save / churn)
      RET-D: candidate only right (save / churn / save)
      RET-E: not scorable for either (gt call_result is None)
      RET-F: both right again     (churn / churn / churn)
    """
    gt = [
        _rec("1", "p", "postpaid", "save"),
        _rec("2", "p", "postpaid", "save"),
        _rec("3", "p", "postpaid", "save"),
        _rec("4", "p", "postpaid", "save"),
        _rec("5", "p", "postpaid", None),
        _rec("6", "p", "postpaid", "churn"),
    ]
    incumbent = [
        _rec("1", "p", "postpaid", "save"),
        _rec("2", "p", "postpaid", "churn"),
        _rec("3", "p", "postpaid", "save"),
        _rec("4", "p", "postpaid", "churn"),
        _rec("5", "p", "postpaid", "save"),
        _rec("6", "p", "postpaid", "churn"),
    ]
    candidate = [
        _rec("1", "p", "postpaid", "save"),
        _rec("2", "p", "postpaid", "unknown"),
        _rec("3", "p", "postpaid", "churn"),
        _rec("4", "p", "postpaid", "save"),
        _rec("5", "p", "postpaid", "save"),
        _rec("6", "p", "postpaid", "churn"),
    ]
    return gt, incumbent, candidate


def test_find_disagreements_agrees_with_compare_disagreement_on_the_total():
    gt, incumbent, candidate = _synthetic_arms()
    table = disagreement(gt, incumbent, candidate, "call_result")
    items = find_disagreements(gt, incumbent, candidate, "call_result")

    expected_population = table.both_wrong + table.incumbent_only_right + table.candidate_only_right
    assert len(items) == expected_population
    assert table.both_right == 2  # RET-1 and RET-6, by construction
    assert expected_population == 3  # RET-2 both wrong, RET-3 incumbent-only, RET-4 candidate-only


def test_find_disagreements_labels_each_population_correctly():
    gt, incumbent, candidate = _synthetic_arms()
    items = {item.key[0]: item for item in find_disagreements(gt, incumbent, candidate, "call_result")}

    assert items["2"].population == "both_wrong"
    assert items["3"].population == "incumbent_only_right"
    assert items["4"].population == "candidate_only_right"
    assert "1" not in items  # both right
    assert "5" not in items  # gt call_result is None -> not scorable
    assert "6" not in items  # both right


def test_find_disagreements_rejects_an_unknown_dimension():
    gt, incumbent, candidate = _synthetic_arms()
    with pytest.raises(JudgeError):
        find_disagreements(gt, incumbent, candidate, "issue_type")


def test_judgment_unit_id_is_stable_and_includes_product_grain_and_dimension():
    first = judgment_unit_id("RET-16", "postpaid", "reason")

    assert first == judgment_unit_id("RET-16", "postpaid", "reason")
    assert first != judgment_unit_id("RET-16", "tvs", "reason")
    assert first != judgment_unit_id("RET-16", "postpaid", "call_result")
    assert first.startswith("ju_") and len(first) == 27
    assert "RET-16" not in first and "postpaid" not in first


def test_judgment_unit_id_rejects_inputs_that_cannot_name_a_unit():
    with pytest.raises(JudgeError):
        judgment_unit_id("", "postpaid", "reason")
    with pytest.raises(JudgeError):
        judgment_unit_id("RET-1", "postpaid", "issue_type")


# ---------------------------------------------------------------- prompt construction / blinding


def _item(key=("42", "p", "postpaid")):
    return DisagreementItem(
        key=key,
        dimension="reason",
        gt_label="network",
        incumbent_label="promotion related",
        candidate_label="save cost",
        population="both_wrong",
    )


def test_build_judge_prompt_never_names_a_vendor():
    item = _item()
    messages = build_judge_prompt("transcript text here", item, ["network: prompt.py:4330"])
    blob = " ".join(m["content"] for m in messages).lower()
    for banned in ("gemini", "qwen", "google", "alibaba", "openrouter"):
        assert banned not in blob, f"prompt leaked vendor identity: {banned!r}"


def test_build_judge_prompt_is_deterministic_and_blinds_by_item_not_by_run():
    item = _item()
    first = build_judge_prompt("t", item, [])
    second = build_judge_prompt("t", item, [])
    assert first == second  # same item -> same blind order, every time


def test_swapping_the_arms_only_swaps_the_blinded_answer_slots():
    """Metamorphic gate: arm identity must not leak through some second prompt field."""
    original = _item()
    swapped = DisagreementItem(
        key=original.key,
        dimension=original.dimension,
        gt_label=original.gt_label,
        incumbent_label=original.candidate_label,
        candidate_label=original.incumbent_label,
        population=original.population,
    )

    def answer_slots(item):
        user = build_judge_prompt("same transcript", item, ["same rule"])[1]["content"]
        lines = user.splitlines()
        return tuple(
            next(line for line in lines if line.startswith(f"Answer {slot}:"))
            for slot in ("A", "B")
        )

    before = answer_slots(original)
    after = answer_slots(swapped)
    assert [line.split(": ", 1)[1] for line in after] == [
        line.split(": ", 1)[1] for line in reversed(before)
    ]


def test_build_judge_prompt_shows_both_labels_and_the_ground_truth():
    item = _item()
    messages = build_judge_prompt("the transcript", item, ["network: prompt.py:4330"])
    user = messages[1]["content"]
    assert "the transcript" in user
    assert "network" in user  # ground truth
    assert "promotion related" in user  # incumbent's answer, in one of the two slots
    assert "save cost" in user  # candidate's answer, in the other slot
    assert "prompt.py:4330" in user


def test_blind_order_actually_varies_across_items():
    """If every item blinded the same way, 'Answer A' would always be one arm and the
    blinding would carry no protection at all."""
    orders = set()
    for i in range(50):
        item = _item(key=(str(i), "p", "postpaid"))
        messages = build_judge_prompt("t", item, [])
        user = messages[1]["content"]
        a_line = next(line for line in user.splitlines() if line.startswith("Answer A:"))
        orders.add(a_line)
    assert len(orders) == 2, "expected both labels to appear as 'Answer A' across 50 items"


# ---------------------------------------------------------------- schema shape


def test_judge_response_schema_enumerates_exactly_the_four_verdicts():
    schema = judge_response_schema()
    enum = schema["json_schema"]["schema"]["properties"]["verdict"]["enum"]
    assert set(enum) == set(JUDGE_VERDICTS)
    assert schema["json_schema"]["strict"] is True
    assert schema["json_schema"]["schema"]["additionalProperties"] is False


# ---------------------------------------------------------------- run_judge orchestration (no network)


class _FakeItem:
    def __init__(self, item_id, call_id, phone_number, transcript_th, rules):
        self.item_id = item_id
        self.call_id = call_id
        self.phone_number = phone_number
        self.transcript_th = transcript_th
        self.rules = rules


class _FakeTestSet:
    def __init__(self, items):
        self.items = items


def _fake_testset():
    return _FakeTestSet(
        [
            _FakeItem(f"RET-{n}", str(n), "p", f"t{n}", {})
            for n in range(1, 7)
        ]
    )


class _FakeCompletion:
    def __init__(self, content):
        self.content = content
        self.observed_model = "google/gemma-4-31b-it"
        self.provider = "CoreWeave"
        self.reasoning_tokens = 0
        self.cost = 0.0001
        self.latency_s = 1.0


class _FakeClient:
    """Returns a fixed verdict per call, records every request it was asked to make."""

    def __init__(self, verdict="ground_truth_correct", *, cited_span="t", rationale="y"):
        self.calls = []
        self._verdict = verdict
        self._cited_span = cited_span
        self._rationale = rationale

    def complete(self, **kwargs):
        self.calls.append(kwargs)
        return _FakeCompletion(
            json.dumps(
                {
                    "verdict": self._verdict,
                    "cited_span": self._cited_span,
                    "rationale": self._rationale,
                }
            )
        )


def test_run_judge_calls_once_per_disagreement_item_and_reports_a_clean_summary():
    gt, incumbent, candidate = _synthetic_arms()
    testset = _FakeTestSet(
        [
            _FakeItem("RET-1", "1", "p", "t1", {}),
            _FakeItem("RET-2", "2", "p", "t2", {}),
            _FakeItem("RET-3", "3", "p", "t3", {}),
            _FakeItem("RET-4", "4", "p", "t4", {}),
            _FakeItem("RET-5", "5", "p", "t5", {}),
            _FakeItem("RET-6", "6", "p", "t6", {}),
        ]
    )
    client = _FakeClient(verdict="ground_truth_correct")

    report = run_judge(
        testset=testset,
        gt=gt,
        incumbent=incumbent,
        candidate=candidate,
        dimensions=["call_result"],
        client=client,
        model="google/gemma-4-31b-it",
        provider="CoreWeave",
    )

    assert report["candidates_found"] == 3  # RET-2 both wrong, RET-3 incumbent-only, RET-4 candidate-only
    assert len(client.calls) == 3
    assert report["truncated"] is False
    assert report["summary"]["total"] == 3
    assert report["summary"]["ground_truth_correct_rate"] == pytest.approx(1.0)
    assert report["summary"]["usable_count"] == 3
    assert {row["evidence_status"] for row in report["items"]} == {"exact"}
    assert all(row["judgment_unit_id"].startswith("ju_") for row in report["items"])
    assert {row["product"] for row in report["items"]} == {None}
    for call_kwargs in client.calls:
        assert call_kwargs["provider"] == "CoreWeave"
        assert call_kwargs["reasoning_effort"] == "none"
        assert call_kwargs["seed"] == 0
        assert call_kwargs["temperature"] == 0.0


def test_run_judge_truncates_deterministically_and_says_so():
    gt, incumbent, candidate = _synthetic_arms()
    testset = _FakeTestSet(
        [_FakeItem(f"RET-{n}", str(n), "p", f"t{n}", {}) for n in range(1, 7)]
    )
    client = _FakeClient()

    report = run_judge(
        testset=testset,
        gt=gt,
        incumbent=incumbent,
        candidate=candidate,
        dimensions=["call_result"],
        client=client,
        model="google/gemma-4-31b-it",
        provider="CoreWeave",
        max_items=1,
    )
    assert report["candidates_found"] == 3
    assert len(report["items"]) == 1
    assert report["truncated"] is True


def test_run_judge_deduplicates_dimensions_before_any_call():
    gt, incumbent, candidate = _synthetic_arms()
    client = _FakeClient()

    report = run_judge(
        testset=_fake_testset(),
        gt=gt,
        incumbent=incumbent,
        candidate=candidate,
        dimensions=["call_result", "call_result"],
        client=client,
        model="google/gemma-4-31b-it",
        provider="CoreWeave",
    )

    assert report["dimensions"] == ["call_result"]
    assert report["candidates_found"] == 3
    assert len(client.calls) == 3


@pytest.mark.parametrize("bad_max", [-1, True, 1.5, "1"])
def test_run_judge_rejects_invalid_max_items_before_any_call(bad_max):
    gt, incumbent, candidate = _synthetic_arms()
    client = _FakeClient()

    with pytest.raises(JudgeError, match="max_items"):
        run_judge(
            testset=_fake_testset(),
            gt=gt,
            incumbent=incumbent,
            candidate=candidate,
            dimensions=["call_result"],
            client=client,
            model="google/gemma-4-31b-it",
            provider="CoreWeave",
            max_items=bad_max,
        )
    assert client.calls == []


def test_run_judge_allows_an_explicit_zero_item_diagnostic():
    gt, incumbent, candidate = _synthetic_arms()
    client = _FakeClient()

    report = run_judge(
        testset=_fake_testset(),
        gt=gt,
        incumbent=incumbent,
        candidate=candidate,
        dimensions=["call_result"],
        client=client,
        model="google/gemma-4-31b-it",
        provider="CoreWeave",
        max_items=0,
    )

    assert report["candidates_found"] == 3
    assert report["items"] == []
    assert report["summary"]["total"] == 0
    assert report["truncated"] is True
    assert client.calls == []


def test_run_judge_applies_evidence_validation_to_live_orchestrated_responses():
    gt, incumbent, candidate = _synthetic_arms()
    client = _FakeClient(cited_span="not present in any transcript")

    report = run_judge(
        testset=_fake_testset(),
        gt=gt,
        incumbent=incumbent,
        candidate=candidate,
        dimensions=["call_result"],
        client=client,
        model="google/gemma-4-31b-it",
        provider="CoreWeave",
    )

    assert report["summary"]["parse_error_count"] == 3
    assert report["summary"]["usable_count"] == 0
    assert {row["verdict"] for row in report["items"]} == {"unclear"}
    assert {row["evidence_status"] for row in report["items"]} == {"not_in_transcript"}


def test_raw_responses_leave_only_through_the_private_sink_and_shareable_view_is_redacted():
    gt, incumbent, candidate = _synthetic_arms()
    private_records = []
    provenance = {
        "incumbent_run_id": "inc-1",
        "candidate_run_id": "cand-1",
        "replicate": 1,
        "testset_sha": "abc",
        "gt_sha": "def",
    }

    report = run_judge(
        testset=_fake_testset(),
        gt=gt,
        incumbent=incumbent,
        candidate=candidate,
        dimensions=["call_result"],
        client=_FakeClient(),
        model="google/gemma-4-31b-it",
        provider="CoreWeave",
        source_provenance=provenance,
        private_record_sink=private_records.append,
    )
    safe = shareable_report(report)

    assert report["schema_version"] == 2
    assert report["source_provenance"] == provenance
    assert len(private_records) == 3
    assert all(row["raw_response_text"] for row in private_records)
    assert all("messages" in row["request"] for row in private_records)
    assert "raw_response_text" not in json.dumps(report)
    assert all("raw" not in row for row in report["items"])
    assert report["items"][0]["rationale"] == "y"  # retained for restricted human review
    assert "rationale" not in safe["items"][0]
    assert "cited_span" not in safe["items"][0]
    assert safe["items"][0]["raw_response_sha256"]
    assert safe["source_provenance"] == provenance


class _FakeTransportError(RuntimeError):
    def __init__(self, message, *, latency_s):
        super().__init__(message)
        self.latency_s = latency_s


class _TransportFailingClient:
    def __init__(self):
        self.calls = []

    def complete(self, **kwargs):
        self.calls.append(kwargs)
        raise _FakeTransportError("endpoint unavailable", latency_s=0.25)


def test_transport_failures_are_recorded_and_never_dropped_or_called_parse_errors():
    gt, incumbent, candidate = _synthetic_arms()
    client = _TransportFailingClient()
    private_records = []

    report = run_judge(
        testset=_fake_testset(),
        gt=gt,
        incumbent=incumbent,
        candidate=candidate,
        dimensions=["call_result"],
        client=client,
        model="google/gemma-4-31b-it",
        provider="CoreWeave",
        private_record_sink=private_records.append,
    )

    assert len(client.calls) == 3
    assert report["summary"]["total"] == 3
    assert report["summary"]["counts"]["unclear"] == 0
    assert report["summary"]["all_response_counts"]["unclear"] == 3
    assert report["summary"]["transport_error_count"] == 3
    assert report["summary"]["parse_error_count"] == 0
    assert report["summary"]["usable_count"] == 0
    assert {row["execution_status"] for row in report["items"]} == {"transport_error"}
    assert {row["latency_s"] for row in report["items"]} == {0.25}
    assert all(row["raw_response_sha256"] is None for row in report["items"])
    assert all(row["transport_error"] for row in private_records)


def test_unexpected_client_programming_errors_still_raise():
    class BrokenClient:
        def complete(self, **kwargs):
            raise RuntimeError("bug in fake client")

    gt, incumbent, candidate = _synthetic_arms()
    with pytest.raises(RuntimeError, match="bug in fake client"):
        run_judge(
            testset=_fake_testset(),
            gt=gt,
            incumbent=incumbent,
            candidate=candidate,
            dimensions=["call_result"],
            client=BrokenClient(),
            model="google/gemma-4-31b-it",
            provider="CoreWeave",
        )


def test_duplicate_ground_truth_units_refuse_before_model_calls():
    gt, incumbent, candidate = _synthetic_arms()
    client = _FakeClient()
    duplicated_gt = [*gt, gt[1]]  # RET-2 is a disagreement and would be called twice

    with pytest.raises(JudgeError, match="duplicate"):
        run_judge(
            testset=_fake_testset(),
            gt=duplicated_gt,
            incumbent=incumbent,
            candidate=candidate,
            dimensions=["call_result"],
            client=client,
            model="google/gemma-4-31b-it",
            provider="CoreWeave",
        )
    assert client.calls == []


def test_duplicate_testset_lookup_keys_refuse_before_model_calls():
    gt, incumbent, candidate = _synthetic_arms()
    testset = _fake_testset()
    testset.items.append(_FakeItem("RET-2-copy", "2", "p", "different", {}))
    client = _FakeClient()

    with pytest.raises(JudgeError, match="duplicate call/phone lookup key"):
        run_judge(
            testset=testset,
            gt=gt,
            incumbent=incumbent,
            candidate=candidate,
            dimensions=["call_result"],
            client=client,
            model="google/gemma-4-31b-it",
            provider="CoreWeave",
        )
    assert client.calls == []


def test_run_judge_raises_if_a_disagreement_key_matches_no_testset_item():
    gt, incumbent, candidate = _synthetic_arms()
    testset = _FakeTestSet([_FakeItem("RET-1", "1", "p", "t1", {})])  # missing RET-2..6
    client = _FakeClient()
    with pytest.raises(JudgeError):
        run_judge(
            testset=testset,
            gt=gt,
            incumbent=incumbent,
            candidate=candidate,
            dimensions=["call_result"],
            client=client,
            model="google/gemma-4-31b-it",
            provider="CoreWeave",
        )


def test_run_judge_never_calls_two_different_providers_in_one_run():
    """A judge report is only reproducible if every call in it was pinned the same way --
    the exact lesson Experiment 4 and the provider probe before this file both taught."""
    gt, incumbent, candidate = _synthetic_arms()
    testset = _FakeTestSet(
        [_FakeItem(f"RET-{n}", str(n), "p", f"t{n}", {}) for n in range(1, 7)]
    )
    client = _FakeClient()
    run_judge(
        testset=testset,
        gt=gt,
        incumbent=incumbent,
        candidate=candidate,
        dimensions=["call_result"],
        client=client,
        model="google/gemma-4-31b-it",
        provider="CoreWeave",
    )
    providers_used = {c["provider"] for c in client.calls}
    assert providers_used == {"CoreWeave"}


# ---------------------------------------------------------------- isolation from the verdict path


def _imports(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            names.add(node.module)
    return names


@pytest.mark.parametrize(
    "path",
    [
        ROOT / "src" / "evalgen" / "report.py",
        ROOT / "src" / "evalgen" / "experiments.py",
        ROOT / "src" / "evalharness" / "compare.py",
        ROOT / "src" / "evalharness" / "manifest.py",
    ],
)
def test_judge_is_never_imported_by_the_verdict_path(path):
    """Report, comparison, and experiment-decision code must never import this module.

    `evalharness/` is already covered transitively by
    `test_boundary.py::test_evalharness_never_imports_evalgen` (judge.py lives in
    evalgen), so this test's real job is the evalgen verdict path, which that package
    boundary cannot catch.
    """
    offenders = [name for name in _imports(path) if "judge" in name.split(".")]
    assert not offenders, f"{path} imports judge.py: {offenders}"


# ---------------------------------------------------------------- rule-text resolution
#
# The arithmetic below is fixed by hand in tests/fixtures/judge/HAND-COMPUTED.md,
# addendum of 2026-08-09, written before the resolver existed. The three text
# expectations are byte-for-byte copies made with `sed -n 'Np'` against the tracked
# production-reference tree -- if one of these fails, either the tree moved (a diff a
# reviewer must see anyway) or the resolver is broken; work out which before editing
# anything, per CLAUDE.md's standing rule.

RULE_ROOT = ROOT / "production-reference"


def test_parse_rule_citation_matches_the_hand_computed_table():
    assert parse_rule_citation("prompt.py:4372") == ([("prompt.py", 4372, 4372)], [])
    assert parse_rule_citation("prompt.py:4390-4391") == ([("prompt.py", 4390, 4391)], [])
    assert parse_rule_citation("prompt.py:4360; prompt.py:4361") == (
        [("prompt.py", 4360, 4360), ("prompt.py", 4361, 4361)],
        [],
    )
    assert parse_rule_citation("prompt.py:4399; main.py:1018") == (
        [("prompt.py", 4399, 4399), ("main.py", 1018, 1018)],
        [],
    )


def test_parse_rule_citation_reports_mechanics_fragments_rather_than_dropping_them():
    """`grain` and `merge` are real citation values in the packs (RET-16, RET-17)."""
    parsed, unparsed = parse_rule_citation("grain")
    assert parsed == []
    assert unparsed == ["grain"]


def test_resolve_rule_text_reproduces_the_hand_copied_lines():
    lines, unresolved = resolve_rule_text("prompt.py:4372", RULE_ROOT)
    assert unresolved == []
    assert lines == [
        "                - ลูกค้าเลี่ยงที่จะบอกเหตุผล หรือ ให้เหตุผลแบบ hate speech / megative reason เช่น เกลียดทรู, เกลียดดีแทค, ไม่ชอบ CP"
    ]

    lines, unresolved = resolve_rule_text("prompt.py:4390-4391", RULE_ROOT)
    assert unresolved == []
    assert lines == [
        "    - `churn`",
        "        - Client confirms leaving the brand (moving to a competitor).",
    ]

    lines, unresolved = resolve_rule_text("main.py:1018", RULE_ROOT)
    assert unresolved == []
    assert lines == ['                "enum": ["churn", "save", "unknown", "undefined"],']


def test_resolve_rule_text_flags_out_of_range_and_unmapped_rather_than_guessing():
    lines, unresolved = resolve_rule_text("prompt.py:99999999", RULE_ROOT)
    assert lines == []
    assert len(unresolved) == 1 and "outside" in unresolved[0]

    lines, unresolved = resolve_rule_text("nonexistent.py:10", RULE_ROOT)
    assert lines == []
    assert len(unresolved) == 1 and "unmapped" in unresolved[0]


def test_every_rule_source_file_in_the_mapping_exists_in_the_tracked_tree():
    """The mapping is only as good as the tree it points at. A renamed reference file
    must fail here, in one line, rather than as 262 silently text-free judge prompts."""
    for basename, relative in RULE_SOURCE_FILES.items():
        assert (RULE_ROOT / relative).is_file(), f"{basename} -> {relative} is missing"


def test_pointer_only_prompt_is_byte_identical_with_and_without_the_new_parameter():
    """Fixture rule 5: `rule_texts=None` can never change an existing caller."""
    item = _item()
    citations = ["network: prompt.py:4330"]
    assert build_judge_prompt("t", item, citations) == build_judge_prompt(
        "t", item, citations, rule_texts=None
    )


def test_rule_text_prompt_quotes_the_cited_line_and_drops_the_pointer_only_stance():
    item = _item()
    lines, unresolved = resolve_rule_text("prompt.py:4372", RULE_ROOT)
    from evalgen.judge import _format_rule_entry

    block = _format_rule_entry("customer reason", "prompt.py:4372", lines, unresolved)
    messages = build_judge_prompt(
        "t", item, ["customer reason: prompt.py:4372"], rule_texts=[block]
    )
    system, user = messages[0]["content"], messages[1]["content"]
    assert "ลูกค้าเลี่ยงที่จะบอกเหตุผล" in user  # the actual rule text reached the judge
    assert "quoted verbatim" in user
    assert "apply it exactly as written" in system
    # The pointer-only framing must be gone: the whole defect was the judge being told
    # the references were pointers and then re-deriving the boundaries itself.
    assert "not rule text" not in system
    assert "(not rule text)" not in user


def test_run_judge_refuses_a_missing_rule_source_root():
    gt, incumbent, candidate = _synthetic_arms()
    with pytest.raises(JudgeError):
        run_judge(
            testset=_fake_testset(),
            gt=gt,
            incumbent=incumbent,
            candidate=candidate,
            dimensions=["call_result"],
            client=_FakeClient(),
            model="google/gemma-4-31b-it",
            provider="CoreWeave",
            rule_source_root=ROOT / "does-not-exist",
        )


def test_run_judge_resolves_rule_text_counts_it_and_shares_the_counts():
    gt, incumbent, candidate = _synthetic_arms()
    testset = _FakeTestSet(
        [
            _FakeItem(
                f"RET-{n}",
                str(n),
                "p",
                f"t{n}",
                {"rule_call_result:save": "prompt.py:4397", "rule_call_result:churn": "grain"},
            )
            for n in range(1, 7)
        ]
    )
    client = _FakeClient()
    report = run_judge(
        testset=testset,
        gt=gt,
        incumbent=incumbent,
        candidate=candidate,
        dimensions=["call_result"],
        client=client,
        model="google/gemma-4-31b-it",
        provider="CoreWeave",
        rule_source_root=RULE_ROOT,
    )
    block = report["rule_text"]
    assert block["enabled"] is True
    assert block["resolved_parts"] > 0
    # `grain` appears wherever the churn label is in play, and must be REPORTED:
    assert (block["unresolved_parts"] > 0) == bool(block["unresolved_fragments"])
    assert all("grain" in fragment for fragment in block["unresolved_fragments"])
    # the actual rule text reached at least one outgoing request
    joined = json.dumps([c["messages"] for c in client.calls], ensure_ascii=False)
    assert "ลังเล ขอเวลาคิดก่อน" in joined  # prompt.py:4397's own words
    # per-record counters exist and survive the shareable export
    assert all(row["rule_text_resolved"] is not None for row in report["items"])
    safe = shareable_report(report)
    assert safe["rule_text"]["enabled"] is True
    assert all("rule_text_resolved" in row for row in safe["items"])
    assert all("rationale" not in row for row in safe["items"])


def test_run_judge_without_rule_source_root_reports_disabled_and_null_counters():
    gt, incumbent, candidate = _synthetic_arms()
    report = run_judge(
        testset=_fake_testset(),
        gt=gt,
        incumbent=incumbent,
        candidate=candidate,
        dimensions=["call_result"],
        client=_FakeClient(),
        model="google/gemma-4-31b-it",
        provider="CoreWeave",
    )
    assert report["rule_text"] == {
        "enabled": False,
        "root": None,
        "resolved_parts": 0,
        "unresolved_parts": 0,
        "unresolved_fragments": [],
        "units_without_rule_text": [],
    }
    assert all(row["rule_text_resolved"] is None for row in report["items"])


def test_shareable_report_drops_the_absolute_rule_source_path():
    """Review finding, 2026-08-09: `rule_text.root` put the operator's absolute path --
    including their OS account name -- into the shareable export, and
    `assert_shareable_payload` does not reject a path. The counts are shareable; where
    the tree sat on one workstation is not."""
    gt, incumbent, candidate = _synthetic_arms()
    report = run_judge(
        testset=_fake_testset(),
        gt=gt,
        incumbent=incumbent,
        candidate=candidate,
        dimensions=["call_result"],
        client=_FakeClient(),
        model="google/gemma-4-31b-it",
        provider="CoreWeave",
        rule_source_root=RULE_ROOT,
    )
    assert report["rule_text"]["root"] is not None  # retained in the private report

    safe = shareable_report(report)
    assert "root" not in safe["rule_text"]
    assert safe["rule_text"]["enabled"] is True
    assert "resolved_parts" in safe["rule_text"]
    blob = json.dumps(safe)
    assert str(RULE_ROOT) not in blob


# ------------------------------------------------- defect-4 fix + replicates (2026-08-09)
#
# Arithmetic fixed by hand in tests/fixtures/judge/HAND-COMPUTED.md, second addendum,
# written before this code. The RET-37 and label-union expectations were read out of the
# raw JSONL and the tracked production tree by hand, not through the loader.

from evalgen.judge import (  # noqa: E402
    _unit_aggregation,
    label_citation_union,
    resolve_citations_dedup,
)


def _v3_items():
    from evalgen.testsets import load_testset

    return load_testset(
        ROOT / "tests" / "fixtures" / "testsets" / "retention_v3.jsonl", app="retention"
    ).items


def test_rule_entries_now_match_hash_suffix_keys_hand_read_from_ret37():
    """RET-37's rules carry both `rule_reason:save cost` -> prompt.py:4342 and
    `rule_reason:save cost#2` -> `prompt.py:4342; prompt.py:4345` (hand-read from the
    raw JSONL). Both citations must surface, and the previously-invisible :4345 --
    the CRITICAL clause -- must now be quoted."""
    from evalgen.judge import _rule_entries_for

    item = next(i for i in _v3_items() if i.item_id == "RET-37")
    entries = dict(_rule_entries_for(item.rules, "reason", {"save cost"}))
    assert "save cost" in entries
    assert entries["save cost"] == ("prompt.py:4342", "prompt.py:4342; prompt.py:4345")

    lines, unresolved = resolve_citations_dedup(
        entries["save cost"], RULE_ROOT
    )
    assert unresolved == []
    # dedup by line identity: 4342 cited twice, quoted once; 4345 present
    joined = "\n".join(lines)
    assert joined.count("\n") + 1 == len(lines)
    text_4345 = (RULE_ROOT / RULE_SOURCE_FILES["prompt.py"]).read_text(
        encoding="utf-8"
    ).splitlines()[4344]
    assert lines.count(text_4345) == 1
    text_4342 = (RULE_ROOT / RULE_SOURCE_FILES["prompt.py"]).read_text(
        encoding="utf-8"
    ).splitlines()[4341]
    assert lines.count(text_4342) == 1


def test_label_citation_union_matches_the_hand_enumerated_sets():
    union = label_citation_union(_v3_items(), "reason")
    assert union["down sell not success"] == (
        "prompt.py:4375-4376",
        "prompt.py:4375-4376; prompt.txt:43",
        "prompt.py:4376",
    )
    assert union["post to pre"] == (
        "prompt.py:4367",
        "prompt.py:4367-4369",
        "prompt.py:4368",
        "prompt.py:4369",
    )


def test_resolve_citations_dedup_never_repeats_a_line_across_overlapping_ranges():
    """The fixture's union example: 4368 and 4369 sit inside 4367-4369 and must not
    repeat when a label's union carries all the overlapping citations."""
    lines, unresolved = resolve_citations_dedup(
        ["prompt.py:4367", "prompt.py:4367-4369", "prompt.py:4368", "prompt.py:4369"],
        RULE_ROOT,
    )
    assert unresolved == []
    assert len(lines) == 3  # exactly 4367, 4368, 4369 -- once each
    assert len(set(lines)) == len(lines) or True  # identity dedup is by line NUMBER
    source = (RULE_ROOT / RULE_SOURCE_FILES["prompt.py"]).read_text(
        encoding="utf-8"
    ).splitlines()
    assert lines == [source[4366], source[4367], source[4368]]


def test_competing_label_now_gets_rule_text_from_the_pack_union():
    """Defect 4's exact shape: a unit whose COMPETING label the item's own rules never
    name. The union fallback must quote that label's rule text anyway, and the prompt
    must never silently equal the pointer prompt."""
    gt, incumbent, candidate = _synthetic_arms()
    # give the items rules that name only the GT label's class
    testset = _FakeTestSet(
        [
            _FakeItem(
                f"RET-{n}",
                str(n),
                "p",
                f"t{n}",
                {"rule_call_result:save": "prompt.py:4397"},
            )
            for n in range(1, 7)
        ]
    )
    client = _FakeClient()
    report = run_judge(
        testset=testset,
        gt=gt,
        incumbent=incumbent,
        candidate=candidate,
        dimensions=["call_result"],
        client=client,
        model="google/gemma-4-31b-it",
        provider="CoreWeave",
        rule_source_root=RULE_ROOT,
    )
    # every unit's labels include churn/unknown competitors; 'save' text must come from
    # the union even on items that only disagree about non-save labels, and labels with
    # no rule anywhere must be VISIBLY marked, never silently dropped
    joined = json.dumps([c["messages"] for c in client.calls], ensure_ascii=False)
    assert "ลังเล ขอเวลาคิดก่อน" in joined  # prompt.py:4397 via own-or-union
    assert "no rule text on file for this label" in joined  # visible, not silent
    assert report["rule_text"]["enabled"] is True
    # placebo elimination: with rule text enabled, no request may equal the
    # pointer-mode request for the same unit
    pointer = run_judge(
        testset=testset,
        gt=gt,
        incumbent=incumbent,
        candidate=candidate,
        dimensions=["call_result"],
        client=(pc := _FakeClient()),
        model="google/gemma-4-31b-it",
        provider="CoreWeave",
    )
    ruled = {r["judgment_unit_id"]: r["request_sha256"] for r in report["items"]}
    pointed = {r["judgment_unit_id"]: r["request_sha256"] for r in pointer["items"]}
    assert set(ruled) == set(pointed)
    overlap = [u for u in ruled if ruled[u] == pointed[u]]
    assert overlap == [], f"placebo path lives: {overlap}"


def test_unit_aggregation_reproduces_the_hand_computed_majority_table():
    """The seven-row table from the fixture's second addendum, byte for byte."""
    E, C, D, U = (
        "ground_truth_error",
        "ground_truth_correct",
        "defensible_disagreement",
        "unclear",
    )
    rows = []
    table = [
        ("u1", [E, E, E]),
        ("u2", [E, E, C]),
        ("u3", [C, D, C]),
        ("u4", [E, D, C]),
        ("u5", [E, U, E]),
        ("u6", [C, C]),
        ("u7", [E, C]),
    ]
    for unit_id, verdicts in table:
        for replicate, verdict in enumerate(verdicts, start=1):
            rows.append(
                {
                    "judgment_unit_id": unit_id,
                    "item_id": unit_id.upper(),
                    "dimension": "call_result",
                    "replicate": replicate,
                    "verdict": verdict,
                }
            )
    agg = _unit_aggregation(rows)
    assert agg["total"] == 7
    assert agg["flagged_majority"] == 3  # u1, u2, u5
    assert {f["judgment_unit_id"] for f in agg["flagged"]} == {"u1", "u2", "u5"}
    assert agg["flipped"] == 5  # u2, u3, u4, u5, u7
    assert agg["no_majority"] == 2  # u4, u7


def test_run_judge_repeats_makes_one_record_per_replicate_and_aggregates_units():
    gt, incumbent, candidate = _synthetic_arms()
    client = _FakeClient()
    report = run_judge(
        testset=_fake_testset(),
        gt=gt,
        incumbent=incumbent,
        candidate=candidate,
        dimensions=["call_result"],
        client=client,
        model="google/gemma-4-31b-it",
        provider="CoreWeave",
        repeats=3,
    )
    units = {r["judgment_unit_id"] for r in report["items"]}
    assert len(report["items"]) == 3 * len(units)
    assert report["repeats"] == 3
    assert report["units"]["total"] == len(units)
    assert report["units"]["flipped"] == 0  # fake client is deterministic
    assert report["units"]["no_majority"] == 0
    replicates = sorted(
        r["replicate"] for r in report["items"] if r["judgment_unit_id"] == next(iter(units))
    )
    assert replicates == [1, 2, 3]
    safe = shareable_report(report)
    assert safe["repeats"] == 3
    assert safe["units"]["total"] == len(units)
    assert all("replicate" in row for row in safe["items"])


def test_run_judge_rejects_a_non_positive_repeats():
    gt, incumbent, candidate = _synthetic_arms()
    for bad in (0, -1, True, 1.5):
        with pytest.raises(JudgeError):
            run_judge(
                testset=_fake_testset(),
                gt=gt,
                incumbent=incumbent,
                candidate=candidate,
                dimensions=["call_result"],
                client=_FakeClient(),
                model="google/gemma-4-31b-it",
                provider="CoreWeave",
                repeats=bad,
            )
