from __future__ import annotations

from dataclasses import replace
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from evalgen import cli
from evalgen.cli import EXIT_OK, EXIT_REFUSED, _write_experiment_xlsx, main
from evalgen.experiments import (
    canonical_sha,
    decision,
    item_stability,
    load_plan,
    logical_call_budget,
    operational_summary,
    projected_execution_budget,
    qualification,
    qualification_contract_sha,
    reliability_gate,
    runtime_gate,
    stability_disagreement,
    validate_plan,
)
from evalgen.prompts import validate_manifest
from evalgen.request import build_request
from evalgen.runner import ItemResult, RunConfig, RunResult
from evalgen.testsets import load_testset
from evalharness.compare import Disagreement, exact_band, paired_verdict

ROOT = Path(__file__).resolve().parents[1]
PLAN = ROOT / "experiments" / "retention-e5.plan.json"


def _row(
    item: str,
    replicate: int,
    *,
    outcome: str = "ok",
    payload: dict | None = None,
    model: str | None = "qwen/qwen3.6-27b",
    provider: str | None = "CoreWeave",
    prompt_tokens: int | None = 100,
    reasoning_tokens: int | None = 0,
    http_status: int | None = None,
) -> ItemResult:
    if payload is None and outcome == "ok":
        payload = {"product": {"Postpaid": {"retention_outcome": "save"}}}
    return ItemResult(
        item_id=item,
        call_id=f"5{int(item.split('-')[1]):03d}",
        replicate=replicate,
        outcome=outcome,
        parse_ok=outcome == "ok",
        truncated=False,
        payload=payload,
        repairs=(),
        observed_model=model,
        provider=provider,
        generation_id=f"g-{item}-{replicate}",
        finish_reason="stop",
        raw_content="{}" if model else None,
        latency_s=0.25,
        prompt_tokens=prompt_tokens,
        completion_tokens=20 if model else None,
        reasoning_tokens=reasoning_tokens,
        cost=0.001 if model else None,
        error=None if model else "transport",
        http_status=http_status,
    )


def _run(*rows: ItemResult, repeats: int = 2) -> RunResult:
    return RunResult(
        config=RunConfig(
            model="qwen/qwen3.6-27b",
            arm="qwen3.6-27b",
            prompt_id="v9_16_base",
            repeats=repeats,
            provider="CoreWeave",
            reasoning_effort="none",
            max_attempts=1,
        ),
        prompt_sha="prompt",
        testset_sha="testset",
        results=tuple(rows),
    )


def _qualified_run() -> RunResult:
    rows = [
        _row(item, replicate, prompt_tokens=100 + index)
        for index, item in enumerate(("RET-01", "RET-109", "RET-138"))
        for replicate in (1, 2)
    ]
    return _run(*rows)


def test_committed_experiment_plan_and_prompt_library_are_in_sync():
    plan = load_plan(PLAN)

    assert validate_plan(plan, root=ROOT) == []
    assert validate_manifest() == []
    assert logical_call_budget(plan) == {
        "qualification_max": 108,
        "full": 1242,
        "load": 216,
        "total": 1458,
        "grand_total_max": 1566,
    }
    assert len(canonical_sha(plan)) == 64

    cost = projected_execution_budget(plan, root=ROOT)
    assert cost["full_calls_per_arm"] == 414
    assert cost["load_calls_per_arm"] == 72
    assert cost["grand_maximum_usd"] > cost["full_load_maximum_usd"] > 0


def test_locked_plan_requires_untampered_qualified_artifacts(tmp_path):
    plan = json.loads(json.dumps(load_plan(PLAN)))
    plan["status"] = "locked"
    for index, arm in enumerate(plan["arms"]):
        provider = arm["provider_candidates"][0]
        artifact = {
            "schema_version": 1,
            "experiment_id": plan["experiment_id"],
            "plan_sha": "qualification-time-draft-sha",
            "arm": arm["id"],
            "model": arm["model"],
            "provider": provider,
            "qualification_contract_sha": qualification_contract_sha(
                plan, arm_id=arm["id"], provider=provider
            ),
            "run_id": f"qualification-{index}",
            "qualification": {"status": "QUALIFIED", "passed": True},
        }
        artifact["qualification_sha"] = canonical_sha(artifact)
        path = tmp_path / f"qualification-{index}.json"
        path.write_text(json.dumps(artifact), encoding="utf-8")
        arm["selected_provider"] = provider
        arm["availability"] = "QUALIFIED"
        arm["qualification_artifact"] = str(path)
        arm["qualification_sha"] = artifact["qualification_sha"]

    assert validate_plan(plan, root=ROOT) == []

    first = Path(plan["arms"][0]["qualification_artifact"])
    tampered = json.loads(first.read_text(encoding="utf-8"))
    tampered["qualification"]["status"] = "REQUEST_INCOMPATIBLE"
    first.write_text(json.dumps(tampered), encoding="utf-8")

    problems = validate_plan(plan, root=ROOT)
    assert any("invalid self-hash" in problem for problem in problems)
    assert any("qualification status" in problem for problem in problems)


def test_explicit_reasoning_off_is_in_the_exact_pinned_request():
    request = build_request(
        model="qwen/qwen3.6-27b",
        messages=[{"role": "user", "content": "test"}],
        max_tokens=8000,
        temperature=0.0,
        response_format={"type": "json_schema"},
        provider="Morph",
        reasoning_effort="none",
    )

    assert request["extra_body"]["reasoning"] == {"effort": "none"}
    assert request["extra_body"]["provider"] == {
        "order": ["Morph"],
        "allow_fallbacks": False,
        "require_parameters": True,
    }


def test_provider_default_preserves_historical_request_and_bad_effort_refuses():
    request = build_request(
        model="vendor/model",
        messages=[],
        max_tokens=10,
        temperature=0,
    )
    assert "reasoning" not in request["extra_body"]

    with pytest.raises(ValueError, match="unknown reasoning effort"):
        build_request(
            model="vendor/model",
            messages=[],
            max_tokens=10,
            temperature=0,
            reasoning_effort="off-ish",
        )


@pytest.mark.parametrize(
    ("discordant", "band"), [(0, None), (5, None), (6, 6), (24, 12), (34, 14)]
)
def test_exact_verdict_band_is_calibrated_from_observed_discordance(discordant, band):
    assert exact_band(discordant) == band


def test_paired_verdict_never_calls_no_disagreement_a_tie():
    none = paired_verdict(Disagreement("product", 100, 0, 0, 0))
    ahead = paired_verdict(Disagreement("reason", 80, 0, 0, 6))
    behind = paired_verdict(Disagreement("reason", 80, 0, 6, 0))

    assert none.verdict == "UNDERPOWERED"
    assert ahead.verdict == "AHEAD"
    assert behind.verdict == "BEHIND"


def test_qualification_requires_all_six_calls_identity_usage_and_zero_reasoning():
    result = qualification(
        _qualified_run(),
        expected_model="qwen/qwen3.6-27b",
        expected_provider="CoreWeave",
    )

    assert result.passed
    assert result.status == "QUALIFIED"
    assert result.parse_ok == 6
    assert result.reasons == ()


def test_morph_style_400_is_request_incompatible_not_transient():
    rows = [
        _row(
            item,
            replicate,
            outcome="transport_error",
            payload=None,
            model=None,
            provider=None,
            prompt_tokens=None,
            reasoning_tokens=None,
            http_status=400,
        )
        for item in ("RET-01", "RET-109", "RET-138")
        for replicate in (1, 2)
    ]
    result = qualification(
        _run(*rows),
        expected_model="qwen/qwen3.6-27b",
        expected_provider="Morph",
    )

    assert not result.passed
    assert result.status == "REQUEST_INCOMPATIBLE"
    assert result.http_statuses == {"400": 6}


def test_scalar_or_invalid_json_is_schema_incompatible():
    rows = [
        _row(item, replicate, outcome="schema_violation", payload=None)
        for item in ("RET-01", "RET-109", "RET-138")
        for replicate in (1, 2)
    ]
    result = qualification(
        _run(*rows),
        expected_model="qwen/qwen3.6-27b",
        expected_provider="CoreWeave",
    )

    assert result.status == "SCHEMA_INCOMPATIBLE"


def test_reasoning_tokens_make_an_otherwise_valid_provider_regime_incompatible():
    run = _qualified_run()
    rows = tuple(replace(row, reasoning_tokens=1) for row in run.results)
    result = qualification(
        replace(run, results=rows),
        expected_model="qwen/qwen3.6-27b",
        expected_provider="CoreWeave",
    )

    assert result.status == "REGIME_INCOMPATIBLE"


def test_reliability_gate_uses_unrounded_counts():
    assert reliability_gate(410, 414)
    assert not reliability_gate(409, 414)


def test_item_level_stability_is_paired_and_not_an_aggregate_flip_count():
    stable_payload = {"x": 1}
    changed_payload = {"x": 2}
    incumbent = _run(
        _row("RET-01", 1, payload=stable_payload),
        _row("RET-01", 2, payload=stable_payload),
        _row("RET-02", 1, payload=stable_payload),
        _row("RET-02", 2, payload=stable_payload),
    )
    candidate = _run(
        _row("RET-01", 1, payload=stable_payload),
        _row("RET-01", 2, payload=changed_payload),
        _row("RET-02", 1, payload=stable_payload),
        _row("RET-02", 2, payload=stable_payload),
    )

    assert item_stability(incumbent) == {"RET-01": True, "RET-02": True}
    assert item_stability(candidate) == {"RET-01": False, "RET-02": True}
    assert stability_disagreement(incumbent, candidate) == Disagreement(
        "stability", 1, 0, 1, 0
    )


def test_operational_summary_retains_missing_cost_and_percentiles():
    run = _qualified_run()
    rows = list(run.results)
    rows[0] = replace(rows[0], cost=None, latency_s=9.0)

    facts = operational_summary(replace(run, results=tuple(rows)))

    assert facts["calls"] == 6
    assert facts["parse_valid_rate"] == 1.0
    assert facts["latency_s"]["max"] == 9.0
    assert facts["cost_usd"]["calls_missing"] == 1


def test_full_runtime_gate_rechecks_identity_reasoning_and_usage_after_qualification():
    run = _qualified_run()
    assert runtime_gate(
        run,
        expected_model="qwen/qwen3.6-27b",
        expected_provider="CoreWeave",
    ) == ()

    changed = replace(
        run,
        results=(replace(run.results[0], reasoning_tokens=None), *run.results[1:]),
    )
    assert "reasoning_tokens" in runtime_gate(
        changed,
        expected_model="qwen/qwen3.6-27b",
        expected_provider="CoreWeave",
    )[0]


def test_decision_applies_quality_before_operations_and_keeps_underpowered_visible():
    underpowered = paired_verdict(Disagreement("product", 100, 0, 0, 0))
    level = paired_verdict(Disagreement("reason", 100, 0, 6, 6))

    result = decision(
        qualification_status="QUALIFIED",
        parse_ok=414,
        total=414,
        quality_verdicts=[level, underpowered],
        stability_verdict=level,
    )

    assert result.status == "INCONCLUSIVE"
    assert "product" in result.reasons[0]


def test_experiment_workbook_carries_quality_regressions_and_load(tmp_path):
    latency = {"p50": 1.0, "p95": 2.0, "p99": 3.0, "max": 4.0}
    cost = {"lower_bound": 0.1, "calls_reported": 6, "calls_missing": 0}
    facts = {
        "parse_ok": 6,
        "calls": 6,
        "parse_valid_rate": 1.0,
        "latency_s": latency,
        "cost_usd": cost,
        "throughput_calls_per_s": 2.0,
    }
    summary = {
        "experiment_id": "retention-e5",
        "plan_sha": "a" * 64,
        "arms": {"gemini": facts, "qwen": facts},
        "comparisons": {
            "qwen": {
                "decision": {"status": "PASS", "reasons": ["passed"]},
                "slices": {
                    "full": [{
                        "dimension": "reason", "discordant": 6, "net": 6,
                        "band": 6, "verdict": "AHEAD",
                    }]
                },
                "regressions": [{
                    "item_key": "hashed", "dimension": "product",
                    "gt_label": "TOL", "incumbent_label": "TOL",
                    "candidate_label": "Postpaid", "error_type": "wrong_label",
                }],
            }
        },
        "load": {"gemini": {"1": facts}, "qwen": {"1": facts}},
    }
    path = tmp_path / "summary.xlsx"

    _write_experiment_xlsx(summary, path)

    from openpyxl import load_workbook

    workbook = load_workbook(path, read_only=True)
    assert workbook.sheetnames == ["Overview", "Quality", "Regressions", "Load"]
    assert workbook["Regressions"]["B2"].value == "hashed"


def test_cli_checks_plan_offline_and_refuses_full_calls_while_draft(capsys):
    plan = load_plan(PLAN)

    assert main(["experiment-check", "--plan", str(PLAN)]) == EXIT_OK
    assert main(["experiment-budget", "--plan", str(PLAN)]) == EXIT_OK
    assert main([
        "experiment-run",
        "--plan", str(PLAN),
        "--arm", "qwen3.6-27b",
        "--confirm-plan-sha", canonical_sha(plan),
    ]) == EXIT_REFUSED
    output = capsys.readouterr()
    assert "not 'locked'" in output.err
    assert "grand maximum" in output.out


def test_locked_experiment_runs_and_reports_are_reproducible_without_network(
    monkeypatch, tmp_path, capsys
):
    """The complete paid-call shape, driven by deterministic local completions.

    This is deliberately larger than a parser smoke test: three 414-call full arms and
    nine 24-call load runs pass through the real command gates, runner, logs, loader,
    scorers, paired verdicts and JSON/Markdown/XLSX writers. If those pieces drift apart,
    this is where a plausible-looking final report must fail before an invoice exists.
    """
    plan = json.loads(json.dumps(load_plan(PLAN)))
    plan["status"] = "locked"
    for index, arm in enumerate(plan["arms"]):
        provider = arm["provider_candidates"][0]
        artifact = {
            "schema_version": 1,
            "experiment_id": plan["experiment_id"],
            "plan_sha": "qualification-time-draft-sha",
            "arm": arm["id"],
            "model": arm["model"],
            "provider": provider,
            "qualification_contract_sha": qualification_contract_sha(
                plan, arm_id=arm["id"], provider=provider
            ),
            "run_id": f"qualified-{index}",
            "qualification": {"status": "QUALIFIED", "passed": True},
        }
        artifact["qualification_sha"] = canonical_sha(artifact)
        artifact_path = tmp_path / f"qualified-{index}.json"
        artifact_path.write_text(json.dumps(artifact), encoding="utf-8")
        arm.update({
            "availability": "QUALIFIED",
            "selected_provider": provider,
            "qualification_artifact": str(artifact_path),
            "qualification_sha": artifact["qualification_sha"],
        })

    plan_path = tmp_path / "locked.plan.json"
    plan_path.write_text(json.dumps(plan, ensure_ascii=False, indent=2), encoding="utf-8")
    assert validate_plan(plan, root=ROOT) == []
    plan_sha = canonical_sha(plan)

    testset = load_testset(ROOT / plan["assets"]["testset"]["path"])
    by_transcript = {item.transcript_th: item for item in testset.items}

    class DeterministicClient:
        def complete(
            self,
            *,
            model,
            messages,
            max_tokens,
            temperature,
            top_p=None,
            seed=None,
            response_format=None,
            provider=None,
            reasoning_effort="provider-default",
        ):
            assert reasoning_effort == "none"
            item = by_transcript[messages[1]["content"]]
            payload = {
                "product": {
                    row["product"]: {
                        "main": {"reason": row.get("main") or "", "keyword": ""},
                        "secondary": {
                            "reason": row.get("secondary") or "",
                            "keyword": "",
                        },
                        "third": {"reason": row.get("third") or "", "keyword": ""},
                        "retention_outcome": row.get("call_result") or "",
                    }
                    for row in item.gt
                },
                "call_event_detection": "Emerging or Undefined Events",
                "recommendation": "retain the exact current offer",
            }
            return SimpleNamespace(
                content=json.dumps(payload, ensure_ascii=False),
                finish_reason="stop",
                observed_model=model,
                generation_id=f"fake-{model}-{item.item_id}",
                provider=provider,
                prompt_tokens=1000 + len(item.transcript_th),
                completion_tokens=100,
                reasoning_tokens=0,
                cost=0.0001,
                latency_s=0.001,
            )

    monkeypatch.setenv("OPENROUTER_API_KEY", "sk-fake-not-a-real-key")
    monkeypatch.setenv("EVAL_HARNESS_KEY_HMAC", "deterministic-report-key")
    monkeypatch.setattr(cli, "build_client", lambda *args, **kwargs: DeterministicClient())

    run_root = tmp_path / "runs"
    full_runs: dict[str, Path] = {}
    load_runs: dict[str, Path] = {}

    def execute_and_find(arguments: list[str]) -> Path:
        before = set(run_root.iterdir()) if run_root.exists() else set()
        assert main(arguments) == EXIT_OK
        created = sorted(set(run_root.iterdir()) - before)
        assert len(created) == 1
        capsys.readouterr()
        return created[0]

    for arm in plan["arms"]:
        common = [
            "experiment-run",
            "--plan", str(plan_path),
            "--arm", arm["id"],
            "--confirm-plan-sha", plan_sha,
            "--out", str(run_root),
        ]
        full_runs[arm["id"]] = execute_and_find(common)
        for concurrency in (1, 4, 8):
            load_runs[f"{arm['id']}@{concurrency}"] = execute_and_find(
                common + [
                    "--mode", "load",
                    "--concurrency-level", str(concurrency),
                ]
            )

    def report_arguments(output: Path) -> list[str]:
        arguments = [
            "experiment-report", "--plan", str(plan_path), "--out", str(output)
        ]
        for arm_id, path in full_runs.items():
            arguments += ["--run", f"{arm_id}={path}"]
        for name, path in load_runs.items():
            arguments += ["--load-run", f"{name}={path}"]
        return arguments

    first = tmp_path / "report-first"
    second = tmp_path / "report-second"
    assert main(report_arguments(first)) == EXIT_OK
    capsys.readouterr()
    assert main(report_arguments(second)) == EXIT_OK
    capsys.readouterr()

    assert (first / "summary.json").read_bytes() == (second / "summary.json").read_bytes()
    assert (first / "summary.md").read_bytes() == (second / "summary.md").read_bytes()
    summary = json.loads((first / "summary.json").read_text(encoding="utf-8"))
    assert set(summary["arms"]) == {arm["id"] for arm in plan["arms"]}
    assert set(summary["comparisons"]) == {"qwen3.6-27b", "qwen3.6-35b-a3b"}
    assert all(
        comparison["decision"]["status"] == "INCONCLUSIVE"
        for comparison in summary["comparisons"].values()
    ), "zero discordant pairs is UNDERPOWERED, never silently called a tie/pass"
    assert all(
        facts["runtime_gate_problems"] == [] for facts in summary["arms"].values()
    )
    assert all(
        set(levels) == {"1", "4", "8"} for levels in summary["load"].values()
    )

    from openpyxl import load_workbook

    workbook = load_workbook(first / "summary.xlsx", read_only=True, data_only=True)
    assert workbook.sheetnames == ["Overview", "Quality", "Regressions", "Load"]
    assert workbook["Load"].max_row == 10  # header + 3 arms x 3 concurrency levels
    for arm in plan["arms"]:
        assert (first / f"arm-{arm['id']}.json").is_file()
        assert (first / f"arm-{arm['id']}.md").is_file()
