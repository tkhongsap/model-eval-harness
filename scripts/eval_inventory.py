"""Emit `docs/reports/eval-inventory.json` -- what evaluation assets exist, counted from disk.

WHY A GENERATOR AND NOT A HAND-WRITTEN TABLE. An inventory is exactly the kind of document that
is true on the day it is written and quietly wrong three weeks later, because nothing recounts
it. Every figure here is read from the repository at run time: test-set rows from the ground
truth CSVs, audio counts from the corpus roots, experiment status from the plan files, spend
from the run records, and the production task list from `production-reference/` rather than
from memory.

The one judgement this file encodes rather than measures is the COVERAGE VERDICT -- which
production tasks have an eval and which do not. That mapping is stated in `PRODUCTION_TASKS`
below with the evidence for each, because it cannot be derived: a directory existing under
`production-reference/` does not tell you whether we can score it.

Usage:
    python scripts/eval_inventory.py            # write
    python scripts/eval_inventory.py --check    # report staleness, write nothing
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
OUT = REPO / "docs" / "reports" / "eval-inventory.json"

# Which production task each app directory carries, what shape its model call is, and whether
# anything in this repository can score it. `covered` is a claim about US, not about them.
PRODUCTION_TASKS = [
    {
        "key": "retention",
        "app_dir": "sentiment-batch-retention-main",
        "task": "Retention call labelling",
        "modality": "Thai audio -> JSON",
        "covered": "full",
        "evidence": "8 test sets, 4 preregistered experiments, blind-audited ground truth",
    },
    {
        "key": "mnp",
        "app_dir": "sentiment-batch-mnp-develop",
        "task": "MNP retention labelling",
        "modality": "Thai audio -> JSON",
        "covered": "none",
        "evidence": "MNP LabelSpace declared at src/evalharness/labelspaces.py:56 and "
                    "commented 'Not yet in scope'. No adapter, schema, prompt or test set.",
    },
    {
        "key": "sentiment_qa",
        "app_dir": "sentiment-voice-analysis-develop",
        "task": "QA pipeline fact-check",
        "modality": "audio/text -> ~118 keys",
        "covered": "cost only",
        "evidence": "Token A/B measured (docs/reports/token-ab.json). NO accuracy eval: "
                    "no labelled batch exists. docs/sentiment-qa-token-ask.md is the ask.",
    },
    {
        "key": "telesale",
        "app_dir": "sentiment-voice-analysis-develop",
        "task": "Telesale rubric scoring",
        "modality": "audio -> weighted rubric",
        "covered": "none",
        "evidence": "Additive negative points with per-section caps "
                    "(telesale_scoring.yml). Not classification; needs new metric code.",
    },
    {
        "key": "tax_invoice",
        "app_dir": "sentiment-voice-analysis-develop",
        "task": "Tax invoice extraction",
        "modality": "document image -> fields",
        "covered": "none",
        "evidence": "Per-field thresholds already specified in "
                    "fact_check_uat_baseline.yml. Exact-match scoring, no new statistics.",
    },
    {
        "key": "rtr_fraud",
        "app_dir": "rtr-fraud-validation-main",
        "task": "Shop image classification",
        "modality": "images -> 3 detections",
        "covered": "none",
        "evidence": "Vision. Prompt also requires the model to COUNT its inputs and end "
                    "every result with /N, which is a known weak spot.",
    },
]

TEXT_SETS = [
    ("retention_v1", "seed"),
    ("retention_v2", "scale"),
    ("retention_v3", "primary text pack"),
    ("retention_challenge_v1", "adversarial"),
]

AUDIO_ROOTS = [
    ("asr-eval", "committed audio seed"),
    ("asr-eval-v2", "E23 corpus"),
    ("asr-eval-v3", "E24 corpus, product labels corrected"),
]


def count_rows(path: Path) -> int | None:
    if not path.is_file():
        return None
    return max(len(path.read_text(encoding="utf-8").splitlines()) - 1, 0)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="eval_inventory")
    ap.add_argument("--check", action="store_true")
    args = ap.parse_args(argv if argv is not None else sys.argv[1:])

    inv: dict = {"generated_from": "scripts/eval_inventory.py"}

    # ---- test sets -------------------------------------------------------------------
    sets = []
    ts_dir = REPO / "tests" / "fixtures" / "testsets"
    for name, role in TEXT_SETS:
        jl = ts_dir / f"{name}.jsonl"
        sets.append({
            "name": name, "role": role, "kind": "text",
            "calls": len(jl.read_text(encoding="utf-8").splitlines()) if jl.is_file() else None,
            "gt_rows": count_rows(ts_dir / f"{name}.gt.csv"),
        })
    blocks = sorted(ts_dir.glob("block_*.jsonl"))
    sets.append({
        "name": "block_a..d", "role": "unit fixtures", "kind": "text",
        "calls": sum(len(p.read_text(encoding="utf-8").splitlines()) for p in blocks),
        "gt_rows": None, "files": len(blocks),
    })
    for root, role in AUDIO_ROOTS:
        d = REPO / root
        sets.append({
            "name": root, "role": role, "kind": "audio",
            "calls": len(list((d / "audio").glob("*.wav"))) if (d / "audio").is_dir() else 0,
            "gt_rows": count_rows(d / "ground-truth" / "business.csv"),
        })
    inv["test_sets"] = sets
    inv["totals"] = {
        "text_sets": sum(1 for s in sets if s["kind"] == "text"),
        "audio_sets": sum(1 for s in sets if s["kind"] == "audio"),
        "audio_calls": sum(s["calls"] or 0 for s in sets if s["kind"] == "audio"),
        "labelled_text_rows": sum(s["gt_rows"] or 0 for s in sets if s["kind"] == "text"),
    }

    # ---- experiments -----------------------------------------------------------------
    exps = []
    for p in sorted((REPO / "experiments").glob("*.plan.json")):
        j = json.loads(p.read_text(encoding="utf-8"))
        assets = j.get("assets") or {}
        stamped = any((assets.get(k) or {}).get("sha256")
                      or (assets.get(k) or {}).get("aggregate_sha256")
                      for k in ("corpus_manifest", "audio_bytes", "business_ground_truth"))
        exps.append({
            "id": j.get("experiment_id"), "status": j.get("status"), "app": j.get("app"),
            "arms": len(j.get("arms") or []), "corpus_frozen": bool(stamped),
            "title": (j.get("title") or "")[:110],
        })
    inv["experiments"] = exps

    # ---- spend -----------------------------------------------------------------------
    spend, runs = 0.0, 0
    for p in sorted((REPO / "out" / "runs").glob("*/results.jsonl")):
        s = sum(((json.loads(l).get("usage") or {}).get("cost") or 0)
                for l in p.read_text(encoding="utf-8").splitlines() if l.strip())
        if s:
            spend += s
            runs += 1
    inv["spend"] = {"metered_usd_total": round(spend, 4), "runs_with_cost": runs}

    e24 = REPO / "docs" / "reports" / "e24-figures.json"
    if e24.is_file():
        g = json.loads(e24.read_text(encoding="utf-8"))["arms"]["gemini_audio"]
        inv["spend"]["e24_incumbent_usd"] = g["metered_cost_usd"]
        inv["spend"]["e24_incumbent_calls"] = g["label_calls"]
        inv["spend"]["e24_usd_per_call"] = round(
            g["metered_cost_usd"] / g["label_calls"], 5)

    # ---- the sentiment_qa token A/B --------------------------------------------------
    ab = REPO / "docs" / "reports" / "token-ab.json"
    if ab.is_file():
        j = json.loads(ab.read_text(encoding="utf-8"))
        arms = {a["arm"]: a for a in j["summary"]}
        base, off = arms.get("baseline"), arms.get("reasoning-off")
        if base and off:
            inv["sentiment_qa_token_ab"] = {
                "items": j["items"],
                "baseline_completion_med": base["median_completion"],
                "reasoning_off_completion_med": off["median_completion"],
                # A FRACTION, not a percent. doc_claims' `pct1` scales by 100 on the
                # way out; storing 62.5 here would render as 6250.0%.
                "completion_cut": round(
                    1 - off["median_completion"] / base["median_completion"], 3),
                "baseline_json_ok": base["json_ok"],
                "reasoning_off_json_ok": off["json_ok"],
                "baseline_cost_usd": base["cost_usd"],
                "reasoning_off_cost_usd": off["cost_usd"],
                "accuracy_eval_exists": False,
            }

    # ---- app bindings: the extensibility claim ---------------------------------------
    apps_src = (REPO / "src" / "evalgen" / "apps.py").read_text(encoding="utf-8")
    inv["app_bindings"] = {
        "registered": 1 if "BINDINGS: dict[str, AppBinding] = {RETENTION_BINDING.app:"
                           " RETENTION_BINDING}" in apps_src else None,
        "adapters": sorted(p.stem for p in
                           (REPO / "src" / "evalharness" / "adapters").glob("*.py")
                           if p.stem not in {"__init__", "registry"}),
        "schemas": sorted(p.stem for p in (REPO / "src" / "evalgen" / "schemas").glob("*.json")),
        "label_spaces": sorted(
            n for n in ("RETENTION", "MNP")
            if f"\n{n} = LabelSpace(" in
            (REPO / "src" / "evalharness" / "labelspaces.py").read_text(encoding="utf-8")),
        "note": "The harness is parameterised by app -- --app, binding(application_id), and "
                "adapters/registry.py resolving a loader from the hashed contract string -- "
                "but exactly one binding is registered, so that extensibility has never been "
                "exercised by a second application.",
    }

    # ---- production coverage ----------------------------------------------------------
    prod = REPO / "production-reference"
    app_dirs = sorted(d.name for d in prod.iterdir() if d.is_dir()) if prod.is_dir() else []
    inv["production"] = {
        "app_directories": app_dirs,
        "tasks": PRODUCTION_TASKS,
        "tasks_total": len(PRODUCTION_TASKS),
        "tasks_covered": sum(1 for t in PRODUCTION_TASKS if t["covered"] == "full"),
        "tasks_uncovered": sum(1 for t in PRODUCTION_TASKS if t["covered"] == "none"),
    }
    for t in PRODUCTION_TASKS:
        if t["app_dir"] not in app_dirs:
            raise SystemExit(
                f"INVENTORY REFUSING: {t['app_dir']!r} is named in PRODUCTION_TASKS but is "
                f"not under production-reference/. Found: {app_dirs}. The coverage table "
                "would describe an app that is not there.")

    text = json.dumps(inv, ensure_ascii=False, indent=2) + "\n"
    if args.check:
        current = OUT.read_text(encoding="utf-8") if OUT.exists() else ""
        print(f"{OUT.relative_to(REPO)} " + ("is up to date." if current == text
                                             else "IS STALE -- regenerate it."))
        return 0 if current == text else 1
    OUT.write_text(text, encoding="utf-8")
    print(f"wrote {OUT.relative_to(REPO)}")
    tot = inv["totals"]
    print(f"  {tot['text_sets']} text sets ({tot['labelled_text_rows']} labelled rows), "
          f"{tot['audio_sets']} audio sets ({tot['audio_calls']} calls)")
    print(f"  {len(exps)} experiments; production tasks "
          f"{inv['production']['tasks_covered']} covered / {inv['production']['tasks_total']}")
    print(f"  metered spend ${inv['spend']['metered_usd_total']}")
    print(f"  app bindings registered: {inv['app_bindings']['registered']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
