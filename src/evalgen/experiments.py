"""Machine-checkable enterprise experiment plans and their decision gates.

This module does not call a model.  It defines the contract around calls: what must be
identical across arms, what qualifies a provider for the production-like regime, and
when a completed arm is eligible for quality and operations comparison.  Keeping these
rules here makes ``qualify`` and ``experiment-report`` two views of one policy instead
of two scripts with similar-looking thresholds.
"""

from __future__ import annotations

import csv
import hashlib
import json
import math
from collections import defaultdict
from collections.abc import Mapping, Sequence
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Literal

from evalgen.runner import ItemResult, RunResult
from evalgen.prompts import PromptError, get as get_prompt
from evalgen.testsets import TestsetError, load_testset, validate as validate_testset
from evalharness.compare import Disagreement, PairedVerdict

__all__ = [
    "Decision",
    "PlanError",
    "Qualification",
    "arm_by_id",
    "canonical_sha",
    "decision",
    "item_stability",
    "load_plan",
    "logical_call_budget",
    "operational_summary",
    "projected_execution_budget",
    "qualification",
    "qualification_contract_sha",
    "reliability_gate",
    "runtime_gate",
    "stability_disagreement",
    "validate_plan",
]

PlanStatus = Literal["draft", "qualified", "locked"]
QualificationStatus = Literal[
    "QUALIFIED",
    "REQUEST_INCOMPATIBLE",
    "SCHEMA_INCOMPATIBLE",
    "REGIME_INCOMPATIBLE",
    "IDENTITY_INCOMPATIBLE",
    "PROVENANCE_INCOMPATIBLE",
]


class PlanError(ValueError):
    """A plan cannot be executed or would describe a different experiment."""


def canonical_sha(value: Any) -> str:
    """SHA-256 of canonical JSON, independent of whitespace and key order."""
    encoded = json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def load_plan(path: str | Path) -> dict[str, Any]:
    """Load an experiment plan as an object and retain no implicit defaults."""
    plan_path = Path(path)
    try:
        value = json.loads(plan_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise PlanError(f"cannot read experiment plan {plan_path}: {exc}") from exc
    if not isinstance(value, dict):
        raise PlanError(f"experiment plan {plan_path} must have an object at its root")
    return value


def _file_sha(root: Path, relative: str) -> str:
    path = (root / relative).resolve()
    try:
        path.relative_to(root.resolve())
    except ValueError as exc:
        raise PlanError(f"asset path escapes the repository: {relative}") from exc
    if not path.is_file():
        raise PlanError(f"planned asset does not exist: {relative}")
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _ids(first: int, last: int) -> list[str]:
    return [f"RET-{number:02d}" for number in range(first, last + 1)]


def validate_plan(plan: Mapping[str, Any], *, root: Path) -> list[str]:
    """Return every preregistration defect without making a network call.

    Experiment 5 is intentionally specific.  A generic validator would accept a plan
    that has the right fields but silently changes the approved sample size, retry
    policy or load subset.  These checks pin the decisions that determine its meaning.
    """
    problems: list[str] = []

    def expect(path: str, actual: Any, expected: Any) -> None:
        if actual != expected:
            problems.append(f"{path}: expected {expected!r}, found {actual!r}")

    expect("schema_version", plan.get("schema_version"), 1)
    expect("experiment_id", plan.get("experiment_id"), "retention-e5")
    expect("app", plan.get("app"), "retention")
    if plan.get("status") not in {"draft", "qualified", "locked"}:
        problems.append("status: expected draft, qualified, or locked")

    assets = plan.get("assets", {})
    for name in (
        "testset", "dataset_manifest", "ground_truth", "prompt_manifest", "schema"
    ):
        asset = assets.get(name, {}) if isinstance(assets, Mapping) else {}
        relative = asset.get("path") if isinstance(asset, Mapping) else None
        recorded = asset.get("sha256") if isinstance(asset, Mapping) else None
        if not isinstance(relative, str) or not isinstance(recorded, str):
            problems.append(f"assets.{name}: path and sha256 are required")
            continue
        try:
            observed = _file_sha(root, relative)
        except PlanError as exc:
            problems.append(str(exc))
        else:
            expect(f"assets.{name}.sha256", recorded, observed)

    testset = assets.get("testset", {}) if isinstance(assets, Mapping) else {}
    gt = assets.get("ground_truth", {}) if isinstance(assets, Mapping) else {}
    expect("assets.testset.items", testset.get("items"), 138)
    expect("assets.ground_truth.rows", gt.get("rows"), 150)
    testset_path = root / str(testset.get("path", ""))
    loaded_testset = None
    try:
        loaded_testset = load_testset(testset_path, app="retention")
    except (OSError, TestsetError) as exc:
        problems.append(f"cannot validate planned testset: {exc}")
    else:
        expect("actual testset item count", len(loaded_testset.items), 138)
        testset_problems = validate_testset(loaded_testset)
        problems.extend(f"testset: {problem}" for problem in testset_problems)
    gt_path = root / str(gt.get("path", ""))
    try:
        with gt_path.open(encoding="utf-8", newline="") as handle:
            actual_gt_rows = sum(1 for _ in csv.DictReader(handle))
    except OSError as exc:
        problems.append(f"cannot validate planned ground truth: {exc}")
    else:
        expect("actual ground-truth row count", actual_gt_rows, 150)
    prompt_asset = assets.get("prompt", {}) if isinstance(assets, Mapping) else {}
    try:
        prompt = get_prompt(str(prompt_asset.get("id")))
    except PromptError as exc:
        problems.append(str(exc))
    else:
        expect("assets.prompt.sha256", prompt_asset.get("sha256"), prompt.sha)

    slices = plan.get("slices", {})
    phase_one = slices.get("phase_one", {}) if isinstance(slices, Mapping) else {}
    phase_two = slices.get("phase_two", {}) if isinstance(slices, Mapping) else {}
    full = slices.get("full", {}) if isinstance(slices, Mapping) else {}
    expect("slices.phase_one.item_ids", phase_one.get("item_ids"), _ids(1, 100))
    expect("slices.phase_two.item_ids", phase_two.get("item_ids"), _ids(101, 138))
    expect("slices.full.item_ids", full.get("item_ids"), _ids(1, 138))
    if loaded_testset is not None:
        expect(
            "actual testset item ids",
            [item.item_id for item in loaded_testset.items],
            full.get("item_ids"),
        )

    workload = plan.get("workload", {})
    expect("workload.replicates", workload.get("replicates"), 3)
    expect("workload.concurrency", workload.get("concurrency"), 4)
    expect("workload.max_attempts", workload.get("max_attempts"), 1)
    expect("workload.temperature", workload.get("temperature"), 0.0)
    expect("workload.top_p", workload.get("top_p"), 0.0)
    expect("workload.seed", workload.get("seed"), 0)
    expect("workload.reasoning_effort", workload.get("reasoning_effort"), "none")

    arms = plan.get("arms", [])
    expected_models = {
        "gemini-2.5-flash": "google/gemini-2.5-flash",
        "qwen3.6-27b": "qwen/qwen3.6-27b",
        "qwen3.6-35b-a3b": "qwen/qwen3.6-35b-a3b",
    }
    observed_models = {
        arm.get("id"): arm.get("model")
        for arm in arms
        if isinstance(arm, Mapping)
    }
    expect("arms", observed_models, expected_models)

    qualification_plan = plan.get("qualification", {})
    expect("qualification.item_ids", qualification_plan.get("item_ids"), [
        "RET-01", "RET-109", "RET-138"
    ])
    expect("qualification.replicates", qualification_plan.get("replicates"), 2)
    expect("qualification.max_attempts", qualification_plan.get("max_attempts"), 1)
    expect("qualification.reasoning_effort", qualification_plan.get("reasoning_effort"), "none")
    expect("qualification.required_parse_ok", qualification_plan.get("required_parse_ok"), 6)
    expect("qualification.required_reasoning_tokens", qualification_plan.get("required_reasoning_tokens"), 0)

    quality = plan.get("quality_gates", {})
    expect("quality_gates.minimum_parse_valid_rate", quality.get("minimum_parse_valid_rate"), 0.99)
    expect("quality_gates.minimum_parse_valid_calls", quality.get("minimum_parse_valid_calls"), 410)
    expect("quality_gates.total_calls_per_arm", quality.get("total_calls_per_arm"), 414)
    expect("quality_gates.alpha_per_side", quality.get("alpha_per_side"), 0.015625)

    operations = plan.get("operations", {})
    expect("operations.item_ids", operations.get("item_ids"), [
        "RET-04", "RET-48", "RET-81", "RET-84", "RET-95", "RET-118",
        "RET-123", "RET-138", "RET-101", "RET-102", "RET-109", "RET-110",
    ])
    expect("operations.concurrency_levels", operations.get("concurrency_levels"), [1, 4, 8])
    expect("operations.replicates_per_level", operations.get("replicates_per_level"), 2)
    expect("operations.max_attempts", operations.get("max_attempts"), 1)

    try:
        budget = logical_call_budget(plan)
        expect("call_budget", plan.get("call_budget"), budget)
        qualification_budget = plan.get("qualification_budget", {})
        expect(
            "qualification_budget.eligible_provider_names",
            qualification_budget.get("eligible_provider_names"),
            sum(len(arm["provider_candidates"]) for arm in arms),
        )
        expect(
            "qualification_budget.maximum_calls",
            qualification_budget.get("maximum_calls"),
            budget["qualification_max"],
        )
        prices = qualification_budget.get("prices_per_million_tokens", {})
        input_bound = int(qualification_budget.get("input_token_upper_per_provider"))
        output_bound = int(qualification_budget.get("output_token_upper_per_provider"))
        calculated = 0.0
        for arm in arms:
            arm_prices = prices.get(arm["id"], {})
            expect(
                f"qualification_budget prices for {arm['id']}",
                set(arm_prices),
                set(arm["provider_candidates"]),
            )
            for input_price, output_price in arm_prices.values():
                calculated += (
                    input_bound * float(input_price)
                    + output_bound * float(output_price)
                ) / 1_000_000
        expect(
            "qualification_budget.maximum_usd_at_check",
            qualification_budget.get("maximum_usd_at_check"),
            math.ceil(calculated * 100) / 100,
        )
    except (KeyError, TypeError, ValueError) as exc:
        problems.append(f"cannot compute call budget: {exc}")

    if plan.get("status") == "locked":
        def read_artifact(index: int, arm: Mapping[str, Any], provider: str, name: Any):
            if not name:
                problems.append(
                    f"arms[{index}] qualification artifact is required for {provider}"
                )
                return None
            artifact_path = root / str(name)
            try:
                artifact = json.loads(artifact_path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError) as exc:
                problems.append(f"arms[{index}] qualification artifact cannot be read: {exc}")
                return None
            recorded_sha = artifact.get("qualification_sha")
            unsigned = dict(artifact)
            unsigned.pop("qualification_sha", None)
            if recorded_sha != canonical_sha(unsigned):
                problems.append(
                    f"arms[{index}] qualification artifact has an invalid self-hash"
                )
            expect(f"arms[{index}] qualification arm", artifact.get("arm"), arm.get("id"))
            expect(f"arms[{index}] qualification provider", artifact.get("provider"), provider)
            expect(
                f"arms[{index}] qualification contract",
                artifact.get("qualification_contract_sha"),
                qualification_contract_sha(
                    plan, arm_id=str(arm.get("id")), provider=provider
                ),
            )
            return artifact

        for index, raw_arm in enumerate(arms):
            if not isinstance(raw_arm, Mapping):
                problems.append(f"arms[{index}] must be an object")
                continue
            arm = raw_arm
            availability = arm.get("availability")
            if availability not in {"QUALIFIED", "UNAVAILABLE"}:
                problems.append(
                    f"arms[{index}].availability must be QUALIFIED or UNAVAILABLE "
                    "when status is locked"
                )
                continue
            if availability == "UNAVAILABLE":
                evidence = arm.get("unavailability_evidence")
                expected_providers = set(arm.get("provider_candidates", []))
                if not isinstance(evidence, Mapping) or set(evidence) != expected_providers:
                    problems.append(
                        f"arms[{index}].unavailability_evidence must cover every "
                        f"candidate provider: {sorted(expected_providers)}"
                    )
                    continue
                for provider, artifact_name in evidence.items():
                    artifact = read_artifact(index, arm, str(provider), artifact_name)
                    if artifact is not None and artifact.get("qualification", {}).get("status") == "QUALIFIED":
                        problems.append(
                            f"arms[{index}] is UNAVAILABLE but {provider} qualified"
                        )
                continue

            provider = arm.get("selected_provider")
            if not provider:
                problems.append(
                    f"arms[{index}].selected_provider is required when qualified"
                )
                continue
            if provider not in arm.get("provider_candidates", []):
                problems.append(
                    f"arms[{index}].selected_provider {provider!r} is not a candidate"
                )
            artifact = read_artifact(
                index, arm, str(provider), arm.get("qualification_artifact")
            )
            if artifact is None:
                continue
            expect(
                f"arms[{index}].qualification_sha",
                arm.get("qualification_sha"),
                artifact.get("qualification_sha"),
            )
            expect(
                f"arms[{index}] qualification status",
                artifact.get("qualification", {}).get("status"),
                "QUALIFIED",
            )
    return problems


def arm_by_id(plan: Mapping[str, Any], arm_id: str) -> Mapping[str, Any]:
    matches = [
        arm
        for arm in plan.get("arms", [])
        if isinstance(arm, Mapping) and arm.get("id") == arm_id
    ]
    if len(matches) != 1:
        raise PlanError(f"plan has {len(matches)} arms named {arm_id!r}, expected one")
    return matches[0]


def logical_call_budget(plan: Mapping[str, Any]) -> dict[str, int]:
    """Return full/load budget and the maximum over the current provider inventory."""
    items = int(plan["assets"]["testset"]["items"])
    arms = len(plan["arms"])
    full = items * int(plan["workload"]["replicates"]) * arms
    operations = plan["operations"]
    load = (
        len(operations["item_ids"])
        * len(operations["concurrency_levels"])
        * int(operations["replicates_per_level"])
        * arms
    )
    qualification_plan = plan["qualification"]
    qualification_calls = (
        len(qualification_plan["item_ids"])
        * int(qualification_plan["replicates"])
        * sum(len(arm["provider_candidates"]) for arm in plan["arms"])
    )
    return {
        "qualification_max": qualification_calls,
        "full": full,
        "load": load,
        "total": full + load,
        "grand_total_max": qualification_calls + full + load,
    }


def qualification_contract_sha(
    plan: Mapping[str, Any], *, arm_id: str, provider: str
) -> str:
    """Stable identity of a probe, excluding lifecycle and selection fields."""
    arm = arm_by_id(plan, arm_id)
    workload = plan["workload"]
    return canonical_sha({
        "experiment_id": plan["experiment_id"],
        "app": plan["app"],
        "assets": plan["assets"],
        "model": arm["model"],
        "provider": provider,
        "qualification": plan["qualification"],
        "decoding": {
            key: workload[key]
            for key in ("temperature", "top_p", "seed", "max_tokens")
        },
    })


def projected_execution_budget(
    plan: Mapping[str, Any], *, root: Path
) -> dict[str, Any]:
    """Conservative full/load ceiling at the plan's snapshotted provider prices.

    Input tokens are estimated at twice the UTF-8 content byte count (byte fallback plus
    ample chat-template overhead), and output assumes every call consumes ``max_tokens``.
    Before provider selection the most expensive eligible provider is
    used for each arm; after selection its exact snapshotted price is used.  This is an
    approval ceiling, not a prediction of likely spend.
    """
    testset = load_testset(root / plan["assets"]["testset"]["path"], app=plan["app"])
    prompt = get_prompt(plan["assets"]["prompt"]["id"])
    item_bytes = {
        item.item_id: len((prompt.system_text + item.transcript_th).encode("utf-8"))
        for item in testset.items
    }
    workload = plan["workload"]
    operations = plan["operations"]
    full_calls = len(testset.items) * int(workload["replicates"])
    load_calls = (
        len(operations["item_ids"])
        * len(operations["concurrency_levels"])
        * int(operations["replicates_per_level"])
    )
    input_upper = 2 * (
        int(workload["replicates"]) * sum(item_bytes.values())
        + len(operations["concurrency_levels"])
        * int(operations["replicates_per_level"])
        * sum(item_bytes[item_id] for item_id in operations["item_ids"])
    )
    output_upper = (full_calls + load_calls) * int(workload["max_tokens"])
    prices = plan["qualification_budget"]["prices_per_million_tokens"]
    arms: dict[str, Any] = {}
    total = 0.0
    for arm in plan["arms"]:
        if arm.get("availability") == "UNAVAILABLE":
            arms[arm["id"]] = {"availability": "UNAVAILABLE", "maximum_usd": 0.0}
            continue
        candidates = prices[arm["id"]]
        selected = arm.get("selected_provider")
        if selected:
            provider = selected
            input_price, output_price = candidates[provider]
        else:
            provider, (input_price, output_price) = max(
                candidates.items(),
                key=lambda entry: (
                    input_upper * float(entry[1][0])
                    + output_upper * float(entry[1][1])
                ),
            )
        maximum = (
            input_upper * float(input_price) + output_upper * float(output_price)
        ) / 1_000_000
        total += maximum
        arms[arm["id"]] = {
            "availability": arm.get("availability"),
            "provider": provider,
            "provider_selection": "selected" if selected else "highest-cost candidate",
            "calls": full_calls + load_calls,
            "input_token_upper": input_upper,
            "output_token_upper": output_upper,
            "input_price_per_million": input_price,
            "output_price_per_million": output_price,
            "maximum_usd": maximum,
        }
    return {
        "full_calls_per_arm": full_calls,
        "load_calls_per_arm": load_calls,
        "arms": arms,
        "full_load_maximum_usd": total,
        "qualification_maximum_usd": plan["qualification_budget"]["maximum_usd_at_check"],
        "grand_maximum_usd": total + plan["qualification_budget"]["maximum_usd_at_check"],
        "price_snapshot_utc": plan["qualification_budget"]["inventory_checked_utc"],
    }


@dataclass(frozen=True)
class Qualification:
    status: QualificationStatus
    passed: bool
    calls: int
    parse_ok: int
    observed_models: dict[str, int]
    observed_providers: dict[str, int]
    reasoning_tokens: tuple[int | None, ...]
    calls_without_prompt_usage: int
    split_items: dict[str, tuple[int, ...]]
    http_statuses: dict[str, int]
    reasons: tuple[str, ...]

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def qualification(
    result: RunResult,
    *,
    expected_model: str,
    expected_provider: str,
) -> Qualification:
    """Classify a six-call explicit-reasoning-off provider probe."""
    rows = result.results
    reasons: list[str] = []
    parse_ok = sum(row.outcome == "ok" for row in rows)
    observed_models = result.observed_models()
    observed_providers = result.observed_providers()
    reasoning = tuple(row.reasoning_tokens for row in rows)
    missing_usage = result.calls_without_prompt_usage()
    split = result.split_items()
    statuses: dict[str, int] = {}
    for row in rows:
        if row.http_status is not None:
            key = str(row.http_status)
            statuses[key] = statuses.get(key, 0) + 1

    if len(rows) != 6:
        reasons.append(f"expected 6 calls, observed {len(rows)}")
    if parse_ok != 6:
        reasons.append(f"parse_ok was {parse_ok}/6")
    if set(observed_models) != {expected_model}:
        reasons.append(
            f"observed model set {sorted(observed_models)} != {[expected_model]}"
        )
    if set(observed_providers) != {expected_provider}:
        reasons.append(
            f"observed provider set {sorted(observed_providers)} != {[expected_provider]}"
        )
    if any(value != 0 for value in reasoning):
        reasons.append("reasoning_tokens was not exactly zero on every call")
    if missing_usage:
        reasons.append(f"{missing_usage} calls had no positive prompt token count")
    if split:
        reasons.append(f"prompt token fingerprint split on {sorted(split)}")

    request_error = any(row.http_status == 400 for row in rows)
    schema_error = any(row.outcome in {"not_json", "schema_violation"} for row in rows)
    regime_error = any(value is not None and value != 0 for value in reasoning)
    identity_error = (
        set(observed_models) != {expected_model}
        or set(observed_providers) != {expected_provider}
    )
    provenance_error = bool(missing_usage or split or any(value is None for value in reasoning))

    if request_error:
        status: QualificationStatus = "REQUEST_INCOMPATIBLE"
    elif schema_error:
        status = "SCHEMA_INCOMPATIBLE"
    elif regime_error:
        status = "REGIME_INCOMPATIBLE"
    elif identity_error:
        status = "IDENTITY_INCOMPATIBLE"
    elif provenance_error:
        status = "PROVENANCE_INCOMPATIBLE"
    elif reasons:
        status = "SCHEMA_INCOMPATIBLE"
    else:
        status = "QUALIFIED"
    return Qualification(
        status=status,
        passed=status == "QUALIFIED",
        calls=len(rows),
        parse_ok=parse_ok,
        observed_models=observed_models,
        observed_providers=observed_providers,
        reasoning_tokens=reasoning,
        calls_without_prompt_usage=missing_usage,
        split_items=split,
        http_statuses=statuses,
        reasons=tuple(reasons),
    )


def reliability_gate(ok: int, total: int, *, minimum: float = 0.99) -> bool:
    """True only when the unrounded parse-valid fraction reaches the gate."""
    if total <= 0 or not 0 <= ok <= total:
        raise ValueError("reliability counts must satisfy 0 <= ok <= total and total > 0")
    return ok / total >= minimum


def runtime_gate(
    result: RunResult, *, expected_model: str, expected_provider: str
) -> tuple[str, ...]:
    """Prove the selected identity and explicit-off regime held for the paid run."""
    problems: list[str] = []
    if set(result.observed_models()) != {expected_model}:
        problems.append(
            f"observed models {sorted(result.observed_models())} != {[expected_model]}"
        )
    if set(result.observed_providers()) != {expected_provider}:
        problems.append(
            f"observed providers {sorted(result.observed_providers())} != "
            f"{[expected_provider]}"
        )
    if any(row.reasoning_tokens != 0 for row in result.results):
        problems.append("reasoning_tokens was not exactly zero on every call")
    if result.calls_without_prompt_usage():
        problems.append(
            f"{result.calls_without_prompt_usage()} calls lacked positive prompt usage"
        )
    if result.split_items():
        problems.append(
            f"prompt-token fingerprint split on {sorted(result.split_items())}"
        )
    return tuple(problems)


def _result_signature(row: ItemResult) -> str:
    return canonical_sha({
        "outcome": row.outcome,
        "payload": row.payload,
        "raw_content": row.raw_content if row.payload is None else None,
    })


def item_stability(result: RunResult) -> dict[str, bool]:
    """Whether each item's classified output is identical across all replicates."""
    signatures: dict[str, set[str]] = defaultdict(set)
    counts: dict[str, int] = defaultdict(int)
    for row in result.results:
        signatures[row.item_id].add(_result_signature(row))
        counts[row.item_id] += 1
    expected = result.config.repeats
    return {
        item_id: counts[item_id] == expected and len(values) == 1
        for item_id, values in signatures.items()
    }


def stability_disagreement(incumbent: RunResult, candidate: RunResult) -> Disagreement:
    """Paired table where right means stable across the declared replicates."""
    inc = item_stability(incumbent)
    cand = item_stability(candidate)
    if set(inc) != set(cand):
        raise PlanError("stability comparison requires identical item ids")
    br = bw = ior = cor = 0
    for item_id in sorted(inc):
        if inc[item_id] and cand[item_id]:
            br += 1
        elif not inc[item_id] and not cand[item_id]:
            bw += 1
        elif inc[item_id]:
            ior += 1
        else:
            cor += 1
    return Disagreement("stability", br, bw, ior, cor)


def _percentile(values: Sequence[float], probability: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    return ordered[max(0, math.ceil(probability * len(ordered)) - 1)]


def operational_summary(result: RunResult) -> dict[str, Any]:
    """Machine-readable reliability, latency, token and cost facts for one run."""
    rows = result.results
    latencies = [row.latency_s for row in rows]
    prompt = [row.prompt_tokens for row in rows if row.prompt_tokens is not None]
    completion = [row.completion_tokens for row in rows if row.completion_tokens is not None]
    reasoning = [row.reasoning_tokens for row in rows if row.reasoning_tokens is not None]
    costs = [row.cost for row in rows if row.cost is not None]
    ok = sum(row.outcome == "ok" for row in rows)
    return {
        "calls": len(rows),
        "parse_ok": ok,
        "parse_valid_rate": ok / len(rows) if rows else None,
        "reliability_gate_99": reliability_gate(ok, len(rows)) if rows else False,
        "latency_s": {
            "p50": _percentile(latencies, 0.50),
            "p95": _percentile(latencies, 0.95),
            "p99": _percentile(latencies, 0.99),
            "max": max(latencies) if latencies else None,
        },
        "tokens": {
            "prompt": sum(prompt),
            "completion": sum(completion),
            "reasoning": sum(reasoning),
            "calls_missing_prompt": len(rows) - len(prompt),
            "calls_missing_completion": len(rows) - len(completion),
            "calls_missing_reasoning": len(rows) - len(reasoning),
        },
        "cost_usd": {
            "lower_bound": sum(costs),
            "calls_reported": len(costs),
            "calls_missing": len(rows) - len(costs),
        },
        "observed_models": result.observed_models(),
        "observed_providers": result.observed_providers(),
        "unstable_items": sorted(
            item_id for item_id, stable in item_stability(result).items() if not stable
        ),
    }


@dataclass(frozen=True)
class Decision:
    status: Literal["PASS", "FAIL", "INCONCLUSIVE", "UNAVAILABLE"]
    reasons: tuple[str, ...]


def decision(
    *,
    qualification_status: str,
    parse_ok: int,
    total: int,
    quality_verdicts: Sequence[PairedVerdict],
    stability_verdict: PairedVerdict,
    runtime_problems: Sequence[str] = (),
    reference_parse_ok: int | None = None,
    reference_total: int | None = None,
    reference_runtime_problems: Sequence[str] = (),
) -> Decision:
    """Quality gate first; operations rank only arms that pass it."""
    reasons: list[str] = []
    if qualification_status != "QUALIFIED":
        return Decision("UNAVAILABLE", (f"provider qualification: {qualification_status}",))
    if reference_runtime_problems:
        return Decision(
            "INCONCLUSIVE",
            tuple(f"incumbent runtime: {problem}" for problem in reference_runtime_problems),
        )
    if reference_parse_ok is not None and reference_total is not None:
        if not reliability_gate(reference_parse_ok, reference_total):
            return Decision(
                "INCONCLUSIVE",
                (f"incumbent parse reliability {reference_parse_ok}/{reference_total} is below 99%",),
            )
    if runtime_problems:
        reasons.extend(f"runtime: {problem}" for problem in runtime_problems)
    if not reliability_gate(parse_ok, total):
        reasons.append(f"parse reliability {parse_ok}/{total} is below 99%")
    behind = [row.dimension for row in quality_verdicts if row.verdict == "BEHIND"]
    underpowered = [
        row.dimension for row in quality_verdicts if row.verdict == "UNDERPOWERED"
    ]
    if behind:
        reasons.append(f"quality BEHIND on {', '.join(behind)}")
    if stability_verdict.verdict == "BEHIND":
        reasons.append("stability is BEHIND")
    if reasons:
        return Decision("FAIL", tuple(reasons))
    if underpowered:
        return Decision(
            "INCONCLUSIVE",
            (f"quality UNDERPOWERED on {', '.join(underpowered)}",),
        )
    return Decision("PASS", ("all preregistered quality gates passed",))
