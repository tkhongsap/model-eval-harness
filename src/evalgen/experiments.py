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
from evalharness.compare import Disagreement, PairedVerdict, paired_verdict

__all__ = [
    "DEFAULT_QUALITY_DIMENSIONS",
    "Decision",
    "DecisionPolicyName",
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
    """Route a plan to the contract validator that owns it.

    There is no generic plan validator here, and that is the point: a generic one would
    accept a plan with the right field NAMES while the approved sample size, retry policy
    or alpha had been quietly changed.  Each contract therefore gets its own checker, and
    a plan whose ``experiment_id`` no checker claims is a REFUSAL rather than a pass --
    otherwise dropping a new plan into ``experiments/`` would add a gate that validates
    nothing and reports success.

    Why this dispatch exists at all: ``verify.py`` globs ``experiments/*.plan.json`` and
    runs this on every match.  When ``retention-e23`` landed it was graded against the
    E5/E7 text contract, which expects ``RET-*`` item ids and E7's three OpenRouter arms,
    and produced 27 defects for an entirely well-formed plan.  E23 is a different
    experiment -- audio in, two arms, its own thresholds -- not a malformed repeat of an
    old one.
    """
    experiment_id = plan.get("experiment_id")
    if experiment_id in {"retention-e5", "retention-e7"}:
        return _validate_retention_text_plan(plan, root=root)
    if experiment_id == "retention-e23":
        return _validate_e23_audio_plan(plan, root=root)
    return [
        f"experiment_id: no contract validator is registered for {experiment_id!r}. "
        "Registered: 'retention-e5', 'retention-e7', 'retention-e23'. Add a validator "
        "for the new contract rather than relaxing an existing one -- an unvalidated "
        "plan in experiments/ is a gate that reports success without checking anything."
    ]


def _validate_retention_text_plan(plan: Mapping[str, Any], *, root: Path) -> list[str]:
    """The Retention TEXT experiment contract: retention-e5 and retention-e7.

    ``retention-e5`` remains the historical legacy-policy run; ``retention-e7`` is the
    decision-grade repeat.  Both read the same 138-item ``RET-*`` testset through the same
    prompt registry, so one checker pins both.
    """
    problems: list[str] = []

    def expect(path: str, actual: Any, expected: Any) -> None:
        if actual != expected:
            problems.append(f"{path}: expected {expected!r}, found {actual!r}")

    expect("schema_version", plan.get("schema_version"), 1)
    experiment_id = plan.get("experiment_id")
    if experiment_id not in {"retention-e5", "retention-e7"}:
        # Unreachable through validate_plan's dispatch, kept because this function is
        # also called directly by tests and would otherwise grade any plan handed to it.
        problems.append(
            "experiment_id: expected 'retention-e5' or 'retention-e7', "
            f"found {experiment_id!r}"
        )
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

    # `role` decides which arm is the reference for every paired comparison
    # (`cli.py`'s `incumbent_id = next(arm["id"] for arm in plan["arms"] if
    # arm.get("role") == "incumbent")`), and nothing checked it before this line --
    # not here, and not in the locked-status re-audit below, which otherwise re-verifies
    # `selected_provider`/`availability`/`qualification_sha` against evidence artifacts.
    # A transposed role (copy/paste before locking, or an edit `validate_plan` did not
    # yet enumerate) flips the sign of `net` for every dimension silently: AHEAD and
    # BEHIND swap, and `decision()` turns a flipped BEHIND straight into a FAIL/PASS
    # flip with no error anywhere in the pipeline (audit finding, 2026-08-07).
    observed_roles = {
        arm.get("id"): arm.get("role") for arm in arms if isinstance(arm, Mapping)
    }
    incumbent_ids = [aid for aid, role in observed_roles.items() if role == "incumbent"]
    if len(incumbent_ids) != 1:
        problems.append(
            f"arms[].role: expected exactly one arm with role 'incumbent', found "
            f"{incumbent_ids!r} among {observed_roles!r}. Every paired comparison "
            "binds to this arm by role alone; zero or more than one makes the binding "
            "ambiguous or wrong rather than merely undocumented."
        )
    unexpected_roles = {
        aid: role for aid, role in observed_roles.items() if role not in ("incumbent", "candidate")
    }
    if unexpected_roles:
        problems.append(f"arms[].role: expected 'incumbent' or 'candidate', found {unexpected_roles!r}")

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
        evidence_summary = plan.get("qualification_evidence_summary", {})
        evidence_plan_sha = (
            evidence_summary.get("draft_plan_sha")
            if isinstance(evidence_summary, Mapping)
            else None
        )
        all_artifacts: dict[tuple[str, str], Mapping[str, Any]] = {}
        status_counts: dict[str, int] = defaultdict(int)
        evidence_calls = 0

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
            expect(
                f"arms[{index}] qualification schema version",
                artifact.get("schema_version"),
                1,
            )
            expect(
                f"arms[{index}] qualification experiment",
                artifact.get("experiment_id"),
                plan.get("experiment_id"),
            )
            expect(
                f"arms[{index}] qualification draft plan SHA",
                artifact.get("plan_sha"),
                evidence_plan_sha,
            )
            expect(f"arms[{index}] qualification arm", artifact.get("arm"), arm.get("id"))
            expect(
                f"arms[{index}] qualification model",
                artifact.get("model"),
                arm.get("model"),
            )
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
            expected_providers = set(arm.get("provider_candidates", []))
            evidence = arm.get("qualification_evidence")
            if not isinstance(evidence, Mapping) or set(evidence) != expected_providers:
                problems.append(
                    f"arms[{index}].qualification_evidence must cover every "
                    f"candidate provider: {sorted(expected_providers)}"
                )
                evidence = {}
            arm_artifacts: dict[str, Mapping[str, Any]] = {}
            for provider, artifact_name in evidence.items():
                provider_name = str(provider)
                artifact = read_artifact(index, arm, provider_name, artifact_name)
                if artifact is None:
                    continue
                arm_artifacts[provider_name] = artifact
                all_artifacts[(str(arm.get("id")), provider_name)] = artifact
                qualification_result = artifact.get("qualification", {})
                status = qualification_result.get("status")
                if status not in {
                    "QUALIFIED",
                    "REQUEST_INCOMPATIBLE",
                    "SCHEMA_INCOMPATIBLE",
                    "REGIME_INCOMPATIBLE",
                    "IDENTITY_INCOMPATIBLE",
                    "PROVENANCE_INCOMPATIBLE",
                }:
                    problems.append(
                        f"arms[{index}] {provider_name} has unknown qualification "
                        f"status {status!r}"
                    )
                else:
                    status_counts[str(status)] += 1
                calls = qualification_result.get("calls")
                if not isinstance(calls, int) or calls < 0:
                    problems.append(
                        f"arms[{index}] {provider_name} qualification calls must "
                        "be a non-negative integer"
                    )
                else:
                    evidence_calls += calls
                expect(
                    f"arms[{index}] {provider_name} qualification passed",
                    qualification_result.get("passed"),
                    status == "QUALIFIED",
                )

            availability = arm.get("availability")
            if availability not in {"QUALIFIED", "UNAVAILABLE"}:
                problems.append(
                    f"arms[{index}].availability must be QUALIFIED or UNAVAILABLE "
                    "when status is locked"
                )
                continue
            if availability == "UNAVAILABLE":
                unavailable = arm.get("unavailability_evidence")
                if not isinstance(unavailable, Mapping) or set(unavailable) != expected_providers:
                    problems.append(
                        f"arms[{index}].unavailability_evidence must cover every "
                        f"candidate provider: {sorted(expected_providers)}"
                    )
                    continue
                for provider, artifact_name in unavailable.items():
                    expect(
                        f"arms[{index}] unavailability artifact for {provider}",
                        artifact_name,
                        evidence.get(provider),
                    )
                    artifact = arm_artifacts.get(str(provider))
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
            expect(
                f"arms[{index}] selected qualification artifact",
                arm.get("qualification_artifact"),
                evidence.get(provider),
            )
            artifact = arm_artifacts.get(str(provider))
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
        if not isinstance(evidence_summary, Mapping):
            problems.append("qualification_evidence_summary must be an object")
        else:
            expect(
                "qualification evidence provider results",
                evidence_summary.get("provider_results"),
                len(all_artifacts),
            )
            expect(
                "qualification evidence logical calls",
                evidence_summary.get("logical_calls"),
                evidence_calls,
            )
            expect(
                "qualification evidence status counts",
                evidence_summary.get("status_counts"),
                dict(status_counts),
            )
            expect(
                "qualification evidence raw model output committed",
                evidence_summary.get("raw_model_output_committed"),
                False,
            )
    return problems


def _validate_e23_audio_plan(plan: Mapping[str, Any], *, root: Path) -> list[str]:
    """The E23 END-TO-END AUDIO contract.

    E23 asks a different question from the text experiments -- audio in, business label
    out, two arms rather than three -- so it pins different things.  What it pins are the
    decisions that would change the VERDICT if edited after a number existed: the primary
    metric, the primary dimension, the alpha, the replicate the headline is computed on,
    and the item set.

    Three checks here cross-reference CODE rather than restating a constant, which is the
    only kind that catches drift:

      * ``alpha_per_side`` must equal ``compare.exact_band``'s default.  A plan claiming
        1/64 while the scorer had moved to 1/20 would silently widen every band.
      * ``failure_categories.label_stage`` must equal the ``Outcome`` literal exactly.  A
        preregistered taxonomy that has fallen behind the runner's is how a failure gets
        reclassified into a friendlier bucket after the fact.
      * ``call_budget.total_logical_calls`` must equal its own components summed.  The
        arithmetic is spelled out in the plan's prose; this checks the prose against the
        numbers beside it.

    Asset sha256 values are deliberately NOT required to be non-null here.  The plan's
    ``sha256_stamping_policy`` says each is stamped exactly once at corpus freeze, and a
    validator demanding them earlier would force someone to invent a hash for bytes that
    do not exist yet -- the precise failure that policy was written to prevent.
    """
    from evalharness.compare import exact_band

    from .outcomes import Outcome

    problems: list[str] = []

    def expect(path: str, actual: Any, expected: Any) -> None:
        if actual != expected:
            problems.append(f"{path}: expected {expected!r}, found {actual!r}")

    expect("schema_version", plan.get("schema_version"), 1)
    expect("experiment_id", plan.get("experiment_id"), "retention-e23")
    expect("app", plan.get("app"), "retention")
    if plan.get("status") not in {"draft", "qualified", "locked"}:
        problems.append("status: expected draft, qualified, or locked")

    metrics = plan.get("metrics") or {}
    primary = metrics.get("primary") if isinstance(metrics, Mapping) else None
    primary = primary if isinstance(primary, Mapping) else {}
    expect("metrics.primary.id", primary.get("id"), "business_f1_call_result")
    if not primary.get("definition"):
        problems.append("metrics.primary.definition: required, and must name the scorer")

    gates = plan.get("quality_gates") or {}
    gates = gates if isinstance(gates, Mapping) else {}
    expect(
        "quality_gates.primary_dimension", gates.get("primary_dimension"), "call_result"
    )
    expect(
        "quality_gates.secondary_paired_dimensions",
        gates.get("secondary_paired_dimensions"),
        ["reason", "product"],
    )

    # Cross-check 1: the alpha the plan preregisters against the one the scorer defaults
    # to. Restating 0.015625 as a literal here would pass even if compare.py had moved.
    default_alpha = exact_band.__kwdefaults__["alpha_per_side"]
    expect("quality_gates.alpha_per_side", gates.get("alpha_per_side"), default_alpha)

    workload = plan.get("workload") or {}
    workload = workload if isinstance(workload, Mapping) else {}
    expect("workload.replicates", workload.get("replicates"), 3)
    expect("workload.max_attempts", workload.get("max_attempts"), 1)
    expect(
        "workload.point_estimate_replicate", workload.get("point_estimate_replicate"), 1
    )

    # Cross-check 2: the preregistered failure taxonomy against the runner's own literal.
    categories = plan.get("failure_categories") or {}
    categories = categories if isinstance(categories, Mapping) else {}
    expect(
        "failure_categories.label_stage",
        categories.get("label_stage"),
        list(Outcome.__args__),
    )

    arms = plan.get("arms")
    if not isinstance(arms, list) or len(arms) != 2:
        count = len(arms) if isinstance(arms, list) else None
        problems.append(f"arms: expected exactly 2, found {count!r}")
    else:
        roles = [arm.get("role") for arm in arms if isinstance(arm, Mapping)]
        if sorted(roles) != ["candidate", "incumbent"]:
            problems.append(
                f"arms: expected one 'incumbent' and one 'candidate', found {roles!r}"
            )

    slices = plan.get("slices") or {}
    slices = slices if isinstance(slices, Mapping) else {}
    full = slices.get("full") if isinstance(slices.get("full"), Mapping) else {}
    item_ids = full.get("item_ids")
    expected_ids = [f"ASR-{number:03d}" for number in range(1, 139)]
    if item_ids != expected_ids:
        found = len(item_ids) if isinstance(item_ids, list) else None
        problems.append(
            "slices.full.item_ids: expected the 138 contiguous ids ASR-001..ASR-138, "
            f"found {found!r} ids"
        )

    # Cross-check 3: the budget against its own components.
    budget = plan.get("call_budget") or {}
    budget = budget if isinstance(budget, Mapping) else {}
    parts = (
        "asr_transcription",
        "asr_stability_probe",
        "label_calls_incumbent",
        "label_calls_candidate",
    )
    values = [budget.get(name) for name in parts]
    if all(isinstance(value, int) for value in values):
        expect(
            "call_budget.total_logical_calls",
            budget.get("total_logical_calls"),
            sum(values),
        )
    else:
        problems.append(f"call_budget: {', '.join(parts)} must all be integers")

    thresholds = plan.get("success_thresholds") or {}
    thresholds = thresholds if isinstance(thresholds, Mapping) else {}
    expect("success_thresholds.verbatim", thresholds.get("verbatim"), True)
    if not thresholds.get("source_path"):
        problems.append(
            "success_thresholds.source_path: required. A band with no cited source is a "
            "band that can be moved to fit a result."
        )

    decision_block = plan.get("decision") or {}
    decision_block = decision_block if isinstance(decision_block, Mapping) else {}
    reconciliation = str(decision_block.get("reconciliation") or "")
    if "RECONCILED remains NO" not in reconciliation:
        problems.append(
            "decision.reconciliation: E23 uses synthetic audio and generator-authored "
            "labels, so it must state that RECONCILED remains NO."
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
    """Return full/load budget and the maximum over the current provider inventory.

    A plan carrying its own ``call_budget`` block is returned verbatim under a
    ``declared`` key.  The derivation below reconstructs the budget from the E5/E7 plan
    shape -- ``assets.testset.items`` times replicates times arms, plus a load sweep and a
    provider qualification pass.  E23 has none of those: its input is a corpus of .wav
    files rather than a testset row count, it runs no load sweep, and it has one runtime
    per stage so there is no provider inventory to qualify against.  Deriving a number
    from fields that do not apply would produce a confident wrong total; reading the one
    the plan states, and checking its internal arithmetic in the validator, does not.
    """
    declared = plan.get("call_budget")
    if isinstance(declared, Mapping) and "total_logical_calls" in declared:
        return {
            "declared": True,
            "total": int(declared["total_logical_calls"]),
        }
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
    # No "declared" key here on purpose: retention-e5 and retention-e7 embed this exact
    # dict in their plans and validate by equality, so an extra key fails both.
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

    # With a provider pin and ``require_parameters=true``, OpenRouter uses 404
    # ("No endpoints found") when the named backend cannot accept the exact
    # request.  That is a request-regime incompatibility, not an identity
    # mismatch merely because no backend had a chance to report its identity.
    # 400 and 422 are the other deterministic request-rejection statuses seen
    # at this boundary.  Authentication failures and rate limits deliberately
    # remain outside this bucket.
    request_error = any(row.http_status in {400, 404, 422} for row in rows)
    if request_error:
        rejected = {
            key: value
            for key, value in statuses.items()
            if int(key) in {400, 404, 422}
        }
        reasons.append(f"exact request rejected with HTTP statuses {rejected}")
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
    """Prove identity/regime on responses without making failures fail twice.

    Reliability deliberately permits up to four non-parse-valid rows in a 414-call
    full arm.  A transport/provider failure normally has no reasoning or usage
    metadata, so requiring those fields on *every logical call* would silently turn
    the 99% reliability gate into a 100% gate.  Identity is still checked on every row
    that reports it; reasoning is checked wherever reported and required on every
    successful response; tokenizer usage is required only on successful responses.
    """
    problems: list[str] = []
    successful = [row for row in result.results if row.outcome == "ok"]
    if set(result.observed_models()) != {expected_model}:
        problems.append(
            f"observed models {sorted(result.observed_models())} != {[expected_model]}"
        )
    if set(result.observed_providers()) != {expected_provider}:
        problems.append(
            f"observed providers {sorted(result.observed_providers())} != "
            f"{[expected_provider]}"
        )
    successful_models = sum(
        row.observed_model is not None for row in successful
    )
    successful_providers = sum(row.provider is not None for row in successful)
    if successful_models != len(successful):
        problems.append(
            f"{len(successful) - successful_models} successful calls lacked model identity"
        )
    if successful_providers != len(successful):
        problems.append(
            f"{len(successful) - successful_providers} successful calls lacked provider identity"
        )
    if any(
        row.reasoning_tokens is not None and row.reasoning_tokens != 0
        for row in result.results
    ) or any(row.reasoning_tokens is None for row in successful):
        problems.append(
            "reasoning_tokens was not exactly zero on every successful response"
        )
    successful_without_usage = sum(
        row.prompt_tokens is None or row.prompt_tokens <= 0
        for row in successful
    )
    if successful_without_usage:
        problems.append(
            f"{successful_without_usage} successful calls lacked positive prompt usage"
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


DecisionPolicyName = Literal["decision_grade_v2", "legacy_v1"]
DEFAULT_QUALITY_DIMENSIONS = ("call_result", "reason", "product")


def _legacy_decision(
    *,
    qualification_status: str,
    parse_ok: int,
    total: int,
    quality_verdicts: Sequence[PairedVerdict],
    stability_verdict: PairedVerdict,
    runtime_problems: Sequence[str],
    reference_parse_ok: int | None,
    reference_total: int | None,
    reference_runtime_problems: Sequence[str],
) -> Decision:
    """The Experiment 5 directional policy, retained only for reproduction.

    This deliberately preserves the historical semantics, including allowing a
    negative-net ``INDISTINGUISHABLE`` result to reach PASS.  New decisions use the
    default v2 policy; callers must name ``legacy_v1`` to reproduce an old report.
    """
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
                (
                    f"incumbent parse reliability {reference_parse_ok}/"
                    f"{reference_total} is below 99%",
                ),
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
    inconclusive: list[str] = []
    if underpowered:
        inconclusive.append(f"quality UNDERPOWERED on {', '.join(underpowered)}")
    if stability_verdict.verdict == "UNDERPOWERED":
        inconclusive.append("stability UNDERPOWERED (too few discordant items for a verdict)")
    if inconclusive:
        return Decision("INCONCLUSIVE", tuple(inconclusive))
    return Decision("PASS", ("all preregistered quality gates passed",))


def _paired_evidence_problem(
    row: object, *, expected_alpha_per_side: float
) -> str | None:
    """Return a structural defect without trusting a hand-built verdict object."""
    if not isinstance(row, PairedVerdict):
        return f"paired evidence has type {type(row).__name__}, expected PairedVerdict"
    if not isinstance(row.dimension, str) or not row.dimension:
        return "paired evidence dimension must be a non-empty string"
    if (
        isinstance(row.discordant, bool)
        or not isinstance(row.discordant, int)
        or isinstance(row.net, bool)
        or not isinstance(row.net, int)
    ):
        return f"{row.dimension}: discordant and net must be integers"
    if (
        isinstance(row.alpha_per_side, bool)
        or not isinstance(row.alpha_per_side, (int, float))
        or not math.isfinite(float(row.alpha_per_side))
        or float(row.alpha_per_side) != float(expected_alpha_per_side)
    ):
        return (
            f"{row.dimension}: alpha_per_side {row.alpha_per_side!r} does not match "
            f"preregistered {expected_alpha_per_side}"
        )
    if row.discordant < 0:
        return f"{row.dimension}: discordant count is negative"
    if abs(row.net) > row.discordant:
        return f"{row.dimension}: abs(net) exceeds discordant count"
    if (row.discordant - row.net) % 2:
        return f"{row.dimension}: net parity is incompatible with discordant count"
    if row.band is not None and (row.band < 0 or row.band > row.discordant):
        return f"{row.dimension}: decision band is outside the discordant count"
    if row.verdict == "UNDERPOWERED" and row.band is not None:
        return f"{row.dimension}: UNDERPOWERED verdict unexpectedly has a band"
    if row.verdict != "UNDERPOWERED" and row.band is None:
        return f"{row.dimension}: {row.verdict} verdict is missing its band"
    incumbent_only = (row.discordant - row.net) // 2
    candidate_only = (row.discordant + row.net) // 2
    canonical = paired_verdict(
        Disagreement(
            row.dimension,
            both_right=0,
            both_wrong=0,
            incumbent_only_right=incumbent_only,
            candidate_only_right=candidate_only,
        ),
        alpha_per_side=float(expected_alpha_per_side),
    )
    if row.band != canonical.band or row.verdict != canonical.verdict:
        return (
            f"{row.dimension}: verdict/band {row.verdict}/{row.band} does not match "
            f"canonical {canonical.verdict}/{canonical.band} for d={row.discordant}, "
            f"net={row.net}"
        )
    return None


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
    expected_dimensions: Sequence[str] = DEFAULT_QUALITY_DIMENSIONS,
    maximum_net_losses: Mapping[str, int] | None = None,
    expected_alpha_per_side: float = 1 / 64,
    policy: DecisionPolicyName = "decision_grade_v2",
) -> Decision:
    """Apply a complete, fail-closed decision policy to paired evidence.

    ``decision_grade_v2`` is the default.  It requires exactly one verdict for every
    expected dimension, treats runtime/identity/provenance defects as invalid evidence
    (INCONCLUSIVE rather than a model-quality FAIL), and refuses PASS when an
    INDISTINGUISHABLE point result loses more paired units than the explicit allowance.

    ``maximum_net_losses`` is a conservative observed-loss guard, not a confidence
    interval and not a claim of statistical non-inferiority.  It defaults to zero for
    every quality dimension and stability.  A future preregistered plan can explicitly
    permit a bounded loss per dimension while a full non-inferiority interval is added.

    ``legacy_v1`` exists solely to reproduce Experiment 5 reports and must be requested
    explicitly.  It does not receive the v2 completeness or net-loss protections.
    """
    if policy not in {"decision_grade_v2", "legacy_v1"}:
        raise PlanError(f"unknown decision policy {policy!r}")
    if policy == "legacy_v1":
        return _legacy_decision(
            qualification_status=qualification_status,
            parse_ok=parse_ok,
            total=total,
            quality_verdicts=quality_verdicts,
            stability_verdict=stability_verdict,
            runtime_problems=runtime_problems,
            reference_parse_ok=reference_parse_ok,
            reference_total=reference_total,
            reference_runtime_problems=reference_runtime_problems,
        )

    expected = tuple(expected_dimensions)
    if not expected or len(set(expected)) != len(expected):
        raise PlanError("expected_dimensions must be a non-empty sequence of unique names")
    margins = dict(maximum_net_losses or {})
    allowed_margin_names = set(expected) | {"stability"}
    unknown_margins = sorted(set(margins) - allowed_margin_names)
    if unknown_margins:
        raise PlanError(
            f"maximum_net_losses names unexpected dimensions: {unknown_margins}"
        )
    if any(isinstance(value, bool) or not isinstance(value, int) or value < 0
           for value in margins.values()):
        raise PlanError("maximum_net_losses values must be non-negative integers")
    if (
        isinstance(expected_alpha_per_side, bool)
        or not isinstance(expected_alpha_per_side, (int, float))
        or not math.isfinite(float(expected_alpha_per_side))
        or not 0 < float(expected_alpha_per_side) < 0.5
    ):
        raise PlanError("expected_alpha_per_side must be between 0 and 0.5")

    if qualification_status != "QUALIFIED":
        return Decision("UNAVAILABLE", (f"provider qualification: {qualification_status}",))

    invalid: list[str] = []
    non_verdicts = [row for row in quality_verdicts if not isinstance(row, PairedVerdict)]
    if not isinstance(stability_verdict, PairedVerdict):
        non_verdicts.append(stability_verdict)
    if non_verdicts:
        return Decision(
            "INCONCLUSIVE",
            ("paired evidence contains non-PairedVerdict value(s)",),
        )
    quality_dimensions = [row.dimension for row in quality_verdicts]
    duplicates = sorted(
        dimension
        for dimension in set(quality_dimensions)
        if quality_dimensions.count(dimension) > 1
    )
    missing = [dimension for dimension in expected if dimension not in quality_dimensions]
    unexpected = sorted(set(quality_dimensions) - set(expected))
    if duplicates:
        invalid.append(f"duplicate quality verdicts for {', '.join(duplicates)}")
    if missing:
        invalid.append(f"missing quality verdicts for {', '.join(missing)}")
    if unexpected:
        invalid.append(f"unexpected quality verdicts for {', '.join(unexpected)}")
    if stability_verdict.dimension != "stability":
        invalid.append(
            f"stability verdict has dimension {stability_verdict.dimension!r}"
        )
    for row in (*quality_verdicts, stability_verdict):
        problem = _paired_evidence_problem(
            row, expected_alpha_per_side=float(expected_alpha_per_side)
        )
        if problem:
            invalid.append(problem)
    if (reference_parse_ok is None) != (reference_total is None):
        invalid.append("incumbent parse reliability requires both ok and total counts")
    elif reference_parse_ok is not None and reference_total is not None:
        if reference_total <= 0 or not 0 <= reference_parse_ok <= reference_total:
            invalid.append("incumbent parse reliability counts are invalid")
    if total <= 0 or not 0 <= parse_ok <= total:
        invalid.append("candidate parse reliability counts are invalid")
    invalid.extend(
        f"invalid incumbent runtime/provenance: {problem}"
        for problem in reference_runtime_problems
    )
    invalid.extend(
        f"invalid candidate runtime/provenance: {problem}" for problem in runtime_problems
    )
    if invalid:
        return Decision("INCONCLUSIVE", tuple(invalid))

    assert total > 0
    if reference_parse_ok is not None and reference_total is not None:
        if not reliability_gate(reference_parse_ok, reference_total):
            return Decision(
                "INCONCLUSIVE",
                (
                    f"incumbent parse reliability {reference_parse_ok}/"
                    f"{reference_total} is below 99%",
                ),
            )
    if not reliability_gate(parse_ok, total):
        return Decision(
            "FAIL", (f"parse reliability {parse_ok}/{total} is below 99%",)
        )

    behind = [row.dimension for row in quality_verdicts if row.verdict == "BEHIND"]
    if stability_verdict.verdict == "BEHIND":
        behind.append("stability")
    if behind:
        return Decision("FAIL", (f"BEHIND on {', '.join(behind)}",))

    inconclusive: list[str] = []
    underpowered = [
        row.dimension for row in quality_verdicts if row.verdict == "UNDERPOWERED"
    ]
    if underpowered:
        inconclusive.append(f"quality UNDERPOWERED on {', '.join(underpowered)}")
    if stability_verdict.verdict == "UNDERPOWERED":
        inconclusive.append(
            "stability UNDERPOWERED (too few discordant items for a verdict)"
        )
    for row in (*quality_verdicts, stability_verdict):
        margin = margins.get(row.dimension, 0)
        if row.verdict == "INDISTINGUISHABLE" and row.net < -margin:
            inconclusive.append(
                f"{row.dimension} non-inferiority is not established: observed net "
                f"{row.net} is below the allowed {-margin}"
            )
    if inconclusive:
        return Decision("INCONCLUSIVE", tuple(inconclusive))
    return Decision(
        "PASS", ("all decision-grade quality and observed net-loss gates passed",)
    )
