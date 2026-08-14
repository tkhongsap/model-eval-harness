"""Run manifest, split into fields that BLOCK a comparison and fields that are
merely recorded.

The split is the whole point. An earlier draft blocked on generation-config
equality, which is **unsatisfiable by construction**: Retention runs with
`thinkingBudget: 0` and forced function calling (`src/main.py:1104-1113`), and vLLM
has neither. A gate that fires on every real run gets bypassed on every real run,
and then it is not a gate.

So: block on the things that MUST match for a paired comparison to mean anything
(same items, same labels, same scorer). Record and print the things that are
expected to differ (backend, model, prompt, decoding), so a reader sees the delta
rather than a hidden assumption.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass, field
from pathlib import Path

from .records import Record


def _sha(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def items_hash(records: list[Record]) -> str:
    """Order-independent hash of the scored item set."""
    return _sha("\n".join(sorted(f"{r.call_id}|{r.phone or ''}|{r.product or ''}" for r in records)))


def file_hash(path: str | Path) -> str:
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def _repository_root() -> Path:
    return Path(__file__).resolve().parents[2]


def _tree_hash(paths: list[Path], *, root: Path) -> str:
    """Content address a declared code surface, independent of git history.

    Relative names are included so moving code is a contract change even when its bytes
    are not. Documentation, commit messages and unrelated files cannot move the hash.
    """
    digest = hashlib.sha256()
    for path in sorted(paths, key=lambda p: p.as_posix()):
        relative = path.resolve().relative_to(root.resolve()).as_posix()
        digest.update(relative.encode("utf-8"))
        digest.update(b"\0")
        digest.update(path.read_bytes())
        digest.update(b"\0")
    return digest.hexdigest()


def outcome_contract_sha(*, root: Path | None = None) -> str:
    """Code that made and bridged the run-time ``parse_ok`` decision."""
    repo = (root or _repository_root()).resolve()
    return _tree_hash(
        [
            repo / "src" / "evalgen" / "outcomes.py",
            repo / "src" / "evalgen" / "runner.py",
        ],
        root=repo,
    )


def generation_contract_sha(*, root: Path | None = None) -> str:
    """Code that determines request bytes, transport, retry, and response extraction."""
    repo = (root or _repository_root()).resolve()
    return _tree_hash(
        [
            repo / "src" / "evalgen" / name
            for name in (
                "client.py",
                "config.py",
                "decoding.py",
                "request.py",
                "runner.py",
                "runtime.py",
            )
        ],
        root=repo,
    )


def decision_policy_sha(*, root: Path | None = None) -> str:
    """Code that turns paired evidence into an experiment decision."""
    repo = (root or _repository_root()).resolve()
    return _tree_hash(
        [
            repo / "src" / "evalharness" / "compare.py",
            repo / "src" / "evalgen" / "experiments.py",
        ],
        root=repo,
    )


def scoring_code_sha(*, root: Path | None = None) -> str:
    """Code that turns classified payloads into paired scores and verdicts.

    `cli.py` is intentionally included because it chooses replicate 1 and performs the
    grain-changing wiring. The digest is narrower than repository HEAD while retaining
    the one orchestration file capable of silently changing the reported number.

    **`apps.py` is included for exactly the same reason, added 2026-08-12.** It holds the
    dimension-to-scorer pairing that `cli.py` used to hold: which function scores which
    dimension, against which class list. Editing one entry there -- pairing `reason` with
    `score_product`, say -- changes every number this harness reports.

    It was outside the digest for one commit and that was a provenance regression, not a
    style question. `application_contract_sha` does not cover it either: the contract
    names an application's *dimensions*, never the implementations bound to them, so a
    swapped scorer leaves both the contract hash and every BLOCKING field untouched. The
    test for inclusion here is not which package a file lives in; it is whether the file
    can silently change the reported number, and this one can.
    """
    repo = (root or _repository_root()).resolve()
    paths = list((repo / "src" / "evalharness").rglob("*.py"))
    paths.extend(
        repo / "src" / "evalgen" / name
        for name in ("apps.py", "cli.py", "flatten.py", "report.py")
    )
    return _tree_hash(paths, root=repo)


def scorer_sha() -> str:
    """Backward-compatible name for content-addressed scoring code provenance."""
    return scoring_code_sha()


def workload_sha(contract: dict) -> str:
    """Hash the canonical workload contract; model and provider must be absent."""
    forbidden = {"model", "model_id", "provider", "provider_requested", "arm"}
    present = forbidden.intersection(contract)
    if present:
        raise ValueError(
            f"workload identity cannot contain arm-specific field(s): {sorted(present)}"
        )
    encoded = json.dumps(contract, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return _sha(encoded)


class ManifestMismatch(RuntimeError):
    """Raised when two arms differ on a BLOCKING field."""


@dataclass(frozen=True)
class Manifest:
    # --- blocking: these must match across arms -------------------------------
    items_sha: str
    labels_sha: str
    item_count: int
    scorer_sha: str

    # --- recorded: expected to differ, printed as a delta ----------------------
    arm: str = "unknown"
    backend: str = "unknown"           # e.g. "vertex-batch", "vllm"
    model_id: str = "unknown"          # OBSERVED from the response, not a constant
    output_mechanism: str = "unknown"  # "function_call" | "guided_json"
    prompt_sha: str = "unknown"
    generation_config: dict = field(default_factory=dict)

    # --- provenance -----------------------------------------------------------
    reconciled: bool = False

    BLOCKING = ("items_sha", "labels_sha", "item_count", "scorer_sha")
    RECORDED = ("backend", "model_id", "output_mechanism", "prompt_sha", "generation_config")

    def to_json(self) -> str:
        return json.dumps(asdict(self), indent=2, sort_keys=True)


def assert_comparable(a: Manifest, b: Manifest) -> list[str]:
    """Block on mismatched blocking fields. Return the recorded deltas to print.

    Raises ManifestMismatch if the arms did not score the same items with the same
    labels and the same scorer. Returns a human-readable list of the differences
    that are legitimate, so the caller can print them beside the result.
    """
    broken = [f for f in Manifest.BLOCKING if getattr(a, f) != getattr(b, f)]
    if broken:
        detail = "; ".join(f"{f}: {getattr(a, f)!r} vs {getattr(b, f)!r}" for f in broken)
        raise ManifestMismatch(
            f"refusing to compare arms that differ on blocking fields ({detail}). "
            "A paired comparison requires the same items, the same labels, and the "
            "same scorer. Everything else may differ and is reported as a delta."
        )

    deltas = []
    for f in Manifest.RECORDED:
        av, bv = getattr(a, f), getattr(b, f)
        if av != bv:
            deltas.append(f"{f}: {a.arm}={av!r} vs {b.arm}={bv!r}")
    return deltas


def provenance_banner(a: Manifest, b: Manifest, deltas: list[str]) -> str:
    """Every report carries this header.

    Until a run has been reconciled against the app's own live Gemini fact-check
    report, no number here is a migration verdict, and the report says so rather
    than leaving the reader to assume otherwise. Framework section 5.3 names that
    gate; this harness cannot satisfy it without real data, so it declares the gap.
    """
    reconciled = a.reconciled and b.reconciled
    lines = [
        f"RECONCILED: {'YES' if reconciled else 'NO'}",
        f"items: {a.item_count}   items_sha: {a.items_sha[:12]}   "
        f"labels_sha: {a.labels_sha[:12]}   scorer: {a.scorer_sha}",
    ]
    if deltas:
        lines.append("arm differences (expected, recorded not blocked):")
        lines.extend(f"  - {d}" for d in deltas)
    if not reconciled:
        lines.append(
            "NOT A MIGRATION VERDICT: these scores have not been reconciled against "
            "the app's live Gemini fact-check report. Until they are, treat them as "
            "harness output, not as evidence about a model."
        )
    return "\n".join(lines)
