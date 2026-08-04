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
import subprocess
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


def scorer_sha() -> str:
    """Which version of THIS harness produced the numbers."""
    try:
        out = subprocess.run(
            ["git", "rev-parse", "--short", "HEAD"],
            cwd=Path(__file__).resolve().parent,
            capture_output=True, text=True, timeout=5, check=False,
        )
        return out.stdout.strip() or "unknown"
    except Exception:  # pragma: no cover
        return "unknown"


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
