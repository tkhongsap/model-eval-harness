"""Emit `docs/reports/e24-figures.json` -- the machine-checked source for E24's numbers.

WHY THIS EXISTS. `scripts/doc_claims.py` checks published figures against a JSON source, and
until now it covered exactly one document: the TEXT track's migration decision. Every ASR-track
number -- the audit rate, the arm F1s, the corpus-fix attribution -- sat in prose under no
check at all.

That is not a theoretical gap. Twice in one day a published ASR figure went stale without
anything noticing: E23's Gemini F1s were computed by a scorer that did not match the
preregistration, and an ASR latency was quoted from a record an orphaned process overwrote.
Both were caught by hand. A gate is what catches the third one.

Every value here is DERIVED from the run and the committed audit record, never typed. Keys use
underscores because `doc_claims` path segments are `[A-Za-z_][A-Za-z0-9_]*` -- a hyphenated arm
name cannot be addressed by a marker.

Usage:
    python scripts/e24_figures.py --run out/runs/<id> --pack asr-eval-v3
    python scripts/e24_figures.py ... --check    # regenerate and diff, write nothing
"""

from __future__ import annotations

import argparse
import csv
import importlib.util
import json
import statistics
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "src"))
OUT = REPO / "docs" / "reports" / "e24-figures.json"

ARM_KEY = {
    "gemini-audio": "gemini_audio",
    "typhoon-pipeline": "typhoon_pipeline",
    "qwen-pipeline": "qwen_pipeline",
    "ceiling": "ceiling",
    "format-control": "format_control",
}


def load(name: str, rel: str):
    spec = importlib.util.spec_from_file_location(name, REPO / rel)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="e24_figures")
    ap.add_argument("--run", type=Path, required=True)
    ap.add_argument("--pack", type=Path, default=REPO / "asr-eval-v3")
    ap.add_argument("--before-run", type=Path,
                    default=REPO / "out/runs/20260820-e23-with-typhoon")
    ap.add_argument("--check", action="store_true")
    args = ap.parse_args(argv if argv is not None else sys.argv[1:])

    S = load("e23score", "scripts/experiment23_score.py")
    E = load("effect", "scripts/corpus_fix_effect.py")

    from evalharness.labelspaces import RETENTION

    pack = args.pack if args.pack.is_absolute() else REPO / args.pack
    truth, phones = S.load_truth(pack)
    collapsed, unstable, failures = S.collapse(args.run, "first")

    figures: dict = {
        "run": args.run.name,
        "pack": pack.name,
        "replicate_policy": "first",
        "calls": len(truth),
        "arms": {},
    }

    for arm, fields in sorted(collapsed.items()):
        gt, pred = S.build_pair(truth, phones, fields)
        scores = {
            "call_result": S.score_call_result(gt, pred, RETENTION.call_result),
            "reason": S.score_reason(gt, pred, RETENTION.reason),
            "product": S.score_product(gt, pred, RETENTION.product),
        }
        rec: dict = {}
        for dim, result in scores.items():
            rec[f"{dim}_f1"] = round(result.weighted("f1"), 3)
            correct = S.call_level_correct(gt, pred, dim)
            rec[f"{dim}_correct"] = sum(1 for v in correct.values() if v)
        counts = failures.get(arm, {})
        total = sum(counts.values())
        rec["label_calls"] = total
        rec["parse_valid"] = counts.get("ok", 0)
        rec["parse_failed"] = total - counts.get("ok", 0)
        rec["unstable_items"] = unstable.get(arm, 0)
        figures["arms"][ARM_KEY.get(arm, arm.replace("-", "_"))] = rec

    # Latency, tokens and cost, from the records the run actually wrote.
    rows = [json.loads(l) for l in (args.run / "results.jsonl").read_text(
        encoding="utf-8").splitlines() if l.strip()]
    for arm in sorted({r["arm"] for r in rows}):
        key = ARM_KEY.get(arm, arm.replace("-", "_"))
        ok = [r for r in rows if r["arm"] == arm and r.get("status") == "ok"]
        lat = sorted(r["latency_s"] for r in ok if r.get("latency_s") is not None)
        prompt = [(r.get("usage") or {}).get("prompt_tokens") or 0 for r in ok]
        audio = [((r.get("usage") or {}).get("prompt_tokens_details") or {}).get(
            "audio_tokens") or 0 for r in ok]
        compl = [(r.get("usage") or {}).get("completion_tokens") or 0 for r in ok]
        cost = [c for c in ((r.get("usage") or {}).get("cost") for r in ok)
                if isinstance(c, (int, float))]
        rec = figures["arms"].setdefault(key, {})
        rec["label_latency_med_s"] = round(statistics.median(lat), 1) if lat else None
        rec["input_tokens_med"] = int(statistics.median(prompt)) if prompt else None
        rec["audio_tokens_med"] = int(statistics.median(audio)) if any(audio) else None
        rec["text_tokens_med"] = (
            int(statistics.median([p - a for p, a in zip(prompt, audio)])) if prompt else None)
        rec["output_tokens_med"] = int(statistics.median(compl)) if compl else None
        rec["metered_cost_usd"] = round(sum(cost), 4) if cost else None

    # The blind audit, read from its committed record rather than restated.
    audit = json.loads((REPO / "docs/reports/audit-result.json").read_text(encoding="utf-8"))
    figures["audit"] = {
        "cases": audit["cases"],
        "reviewers": len(audit["reviewers"]),
        "threshold": audit["threshold"],
        "control_agreement": round(audit["control_agreement"], 3),
        "disputed_against_rate": round(audit["disputed_against_rate"], 3),
        "no_majority": len(audit["no_majority"]),
    }
    for group, counts in audit["by_group"].items():
        figures["audit"][f"{group}_agree"] = counts.get("agree", 0)
        figures["audit"][f"{group}_against"] = counts.get("against", 0)

    # The churn-miss sample the audit was pointed at.
    key_rows = list(csv.DictReader(
        (REPO / "out/audit-answer-key.csv").open(encoding="utf-8")))
    against = {a["case_id"] for a in audit["against"]}
    nomaj = set(audit["no_majority"])
    churn = {"control": [0, 0], "product_mismatch": [0, 0], "outcome_error": [0, 0]}
    for r in key_rows:
        if r["expected_call_result"] != "churn":
            continue
        cell = churn.setdefault(r["group"], [0, 0])
        if r["case_id"] in nomaj:
            continue
        cell[1] += 1
        if r["case_id"] in against:
            cell[0] += 1
    disputed = [churn["product_mismatch"], churn["outcome_error"]]
    figures["churn_sample"] = {
        "control_agree": churn["control"][1] - churn["control"][0],
        "control_n": churn["control"][1],
        "disputed_against": sum(c[0] for c in disputed),
        "disputed_n": sum(c[1] for c in disputed),
        "disputed_against_rate": round(
            sum(c[0] for c in disputed) / max(sum(c[1] for c in disputed), 1), 3),
    }

    # The corpus-fix attribution, recomputed rather than copied.
    gt_a, gt_b = E.business(REPO / "asr-eval-v2"), E.business(pack)
    man_a, man_b = E.manifest(REPO / "asr-eval-v2"), E.manifest(pack)
    scen = E.scenarios(REPO / "asr-eval-v2")
    same_audio = {k for k in man_a if k in man_b
                  and man_a[k]["sha256"] == man_b[k]["sha256"]}
    moved = {k for k in gt_b if gt_a[k]["product"] != gt_b[k]["product"]}
    cells = {
        "control": sorted((set(gt_b) - moved) & same_audio),
        "contradicted": sorted(k for k in moved if scen[k] in E.FORCED),
        "rerolled": sorted(k for k in moved if scen[k] not in E.FORCED),
    }
    fa, _, _ = S.collapse(args.before_run, "first")
    fb = collapsed
    figures["corpus_fix"] = {name: len(items) for name, items in cells.items()}
    figures["corpus_fix"]["total_calls"] = len(gt_b)
    for arm in sorted(set(fa) & set(fb)):
        key = ARM_KEY.get(arm, arm.replace("-", "_"))
        for name, items in cells.items():
            before, after, delta, n = E.rate(fa, fb, arm, items, gt_a, gt_b)
            figures["corpus_fix"].setdefault(key, {})[name] = {
                "before": round(before, 3), "after": round(after, 3),
                "delta": round(delta, 3), "n": n,
            }

    text = json.dumps(figures, ensure_ascii=False, indent=2, sort_keys=False) + "\n"
    if args.check:
        current = OUT.read_text(encoding="utf-8") if OUT.exists() else ""
        if current == text:
            print(f"{OUT.relative_to(REPO)} is up to date.")
            return 0
        print(f"{OUT.relative_to(REPO)} IS STALE -- regenerate it.")
        return 1
    OUT.write_text(text, encoding="utf-8")
    print(f"wrote {OUT.relative_to(REPO)}")
    print(f"  arms: {sorted(figures['arms'])}")
    print(f"  audit disputed-against {figures['audit']['disputed_against_rate']:.1%}, "
          f"control {figures['audit']['control_agreement']:.1%}")
    print(f"  churn sample: {figures['churn_sample']['disputed_against']}"
          f"/{figures['churn_sample']['disputed_n']} against")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
