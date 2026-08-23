"""Emit `docs/reports/gpu-performance.json` — how the internally hosted models performed.

WHY A SEPARATE SOURCE FROM e24-figures.json. That file answers "which pipeline shape wins".
This one answers a different question the business actually asks: **how do the models we host
ourselves behave**, across both stages, including the one that could not be scored.

The two stages live in different places and are pulled from both rather than restated:

  * the labeller (`qwen3.8-27b-fp8`) from `docs/reports/e24-figures.json`, which is itself
    generated from the E24 run and gated by doc_claims;
  * the transcribers from `asr-eval-v2/reports/*.json`, the ASR scoring output of the E23 run
    — the last run in which BOTH transcribers completed. E24's ASR reports do not exist:
    Qwen never finished and Typhoon's timing record was destroyed by an orphaned process
    (`docs/qwen-asr-outage-2026-08-21.txt`).

That split is the honest one, and this file records which run each number came from rather
than presenting them as one measurement.

Usage:
    python scripts/gpu_figures.py            # write
    python scripts/gpu_figures.py --check    # staleness only
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
OUT = REPO / "docs" / "reports" / "gpu-performance.json"

# arm -> the internally hosted model that produced it. The labeller is the same model in all
# three; what differs is what it was fed, which is the point of having three.
LABELLER_ARMS = {
    "ceiling": "a flawless transcript — the upper bound on the labeller",
    "format_control": "ASR-shaped text with zero mishearing — separates layout from hearing",
    "typhoon_pipeline": "a real Typhoon transcript — the deployable configuration",
}


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="gpu_figures")
    ap.add_argument("--check", action="store_true")
    args = ap.parse_args(argv if argv is not None else sys.argv[1:])

    e24 = json.loads((REPO / "docs/reports/e24-figures.json").read_text(encoding="utf-8"))
    out: dict = {
        "generated_from": "scripts/gpu_figures.py",
        "labeller_source_run": e24["run"],
        "labeller_source_pack": e24["pack"],
        "asr_source_run": "20260820-e23-with-typhoon",
        "asr_source_pack": "asr-eval-v2",
        "why_two_runs":
            "E24 is the current business-outcome run and is where the labeller figures come "
            "from. Its ASR reports do not exist -- Qwen never finished and Typhoon's timing "
            "record was overwritten by an orphaned process -- so transcription quality is "
            "quoted from E23, the last run in which both transcribers completed.",
        "calls": e24["calls"],
    }

    # ---- stage 2: the labeller -------------------------------------------------------
    lab: dict = {"model": "qwen3.8-27b-fp8", "runtime": "Token Factory (internal GPU)",
                 "arms": {}}
    for arm, why in LABELLER_ARMS.items():
        a = e24["arms"][arm]
        lab["arms"][arm] = {
            "fed": why,
            "call_result_f1": a["call_result_f1"],
            "product_f1": a["product_f1"],
            "reason_f1": a["reason_f1"],
            "call_result_correct": a["call_result_correct"],
            "latency_med_s": a["label_latency_med_s"],
            "input_tokens_med": a["input_tokens_med"],
            "output_tokens_med": a["output_tokens_med"],
            "parse_valid": a["parse_valid"],
            "label_calls": a["label_calls"],
            "unstable_items": a["unstable_items"],
        }
    out["labeller"] = lab

    # The external incumbent, for contrast only. Named as external so no reader mistakes it
    # for something we host.
    g = e24["arms"]["gemini_audio"]
    out["external_incumbent"] = {
        "model": "google/gemini-2.5-flash", "runtime": "OpenRouter",
        "call_result_f1": g["call_result_f1"], "product_f1": g["product_f1"],
        "reason_f1": g["reason_f1"], "latency_med_s": g["label_latency_med_s"],
        "parse_valid": g["parse_valid"], "label_calls": g["label_calls"],
        "unstable_items": g["unstable_items"],
        "metered_cost_usd": g["metered_cost_usd"],
        "usd_per_call": round(g["metered_cost_usd"] / g["label_calls"], 5),
    }

    # ---- stage 1: the transcribers ---------------------------------------------------
    asr: dict = {}
    for model in ("typhoon-whisper-large-v3", "qwen3-asr-1.7b"):
        p = REPO / "asr-eval-v2" / "reports" / f"{model}.json"
        if not p.is_file():
            continue
        j = json.loads(p.read_text(encoding="utf-8"))
        o = j.get("overall") or {}
        e = j.get("entity_overall") or {}
        asr[model.replace("-", "_").replace(".", "")] = {
            "model": model,
            "cer_norm": round(o["cer_norm"], 4),
            "wer_norm": round(o["wer_norm"], 4),
            "runaway_items": o.get("runaway_items"),
            "runaway_rate": round(o.get("runaway_rate") or 0.0, 4),
            "scoreable_items": o.get("scoreable_items"),
            "entity_accuracy": round(e["accuracy"], 4) if e.get("accuracy") else None,
        }
    out["transcribers"] = asr

    out["qwen_asr_status"] = {
        "state": "UNAVAILABLE",
        "since_utc": "2026-08-21T06:52:00Z",
        "transcripts_completed": 120,
        "transcripts_required": 138,
        "coverage_gate": 0.90,
        "record": "docs/qwen-asr-outage-2026-08-21.txt",
        "note": "Down for 10 h 40 min on 2026-08-21 without one successful response, and it "
                "has not been re-run since. The labeller it shares a gateway with answered "
                "throughout, so this is that model rather than the platform.",
    }

    out["caveats"] = {
        "reconciled": "NO",
        "upper_bound":
            "Every label in this corpus is spoken aloud, so the set measures fact extraction "
            "under transcription noise, not inferential labelling. Real calls state their "
            "outcome far less often; these are upper bounds.",
        "instability":
            "Arms disagree with themselves across three identical replicates on 17 to 33 of "
            "138 items. That is a standing property of the task, measured in both runs.",
        "audit":
            "The ground truth was corrected after a blind audit whose reviewers were models, "
            "not people.",
    }

    text = json.dumps(out, ensure_ascii=False, indent=2) + "\n"
    if args.check:
        current = OUT.read_text(encoding="utf-8") if OUT.exists() else ""
        print(f"{OUT.relative_to(REPO)} " + ("is up to date." if current == text
                                             else "IS STALE -- regenerate it."))
        return 0 if current == text else 1
    OUT.write_text(text, encoding="utf-8")
    print(f"wrote {OUT.relative_to(REPO)}")
    for arm, a in lab["arms"].items():
        print(f"  labeller/{arm:16s} call_result {a['call_result_f1']:.3f}  "
              f"product {a['product_f1']:.3f}  {a['latency_med_s']}s")
    for k, a in asr.items():
        print(f"  asr/{a['model']:26s} CER {a['cer_norm']:.4f}  runaways {a['runaway_items']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
