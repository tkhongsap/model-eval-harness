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

# A SECOND EVALUATION EFFORT EXISTS IN THE ORGANISATION, and it is not a production task.
# `production-reference/ai-local-eval-sentiment_project_v2` (internally `model_migration`)
# evaluates several of the same tasks this repository does, from a different direction.
#
# Declared separately rather than folded into PRODUCTION_TASKS, because counting it as either
# "covered" or "uncovered" would be wrong. Its existence does not mean THIS harness can score
# those tasks; it means the ORGANISATION already has numbers for them, produced by other code
# with other properties. A coverage table that silently absorbed it would overstate what this
# repository can do, and one that ignored it would understate what the business already knows
# and invite duplicated work.
#
# The area -> production-task correspondence is BY NAME and has NOT been verified field by
# field. `sentiment` most likely corresponds to sentiment_qa and `documents` to tax invoice
# extraction, but neither has been checked against the other's schema.
PARALLEL_EVAL_PATH = "ai-local-eval-sentiment_project_v2"
PARALLEL_EVAL_AREAS = ("sentiment", "sentiment_mnp", "sentiment_retention",
                       "sentiment_telesale", "documents")

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
    # The parallel eval effort lives under the same tree but is not a production app, so it
    # must not inflate the app count or the coverage denominator.
    app_dirs = [d for d in app_dirs if d != PARALLEL_EVAL_PATH]
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

    # ---- the parallel effort, measured rather than described --------------------------
    par = prod / PARALLEL_EVAL_PATH
    if par.is_dir():
        areas = {}
        for family in ("google_model", "local_model"):
            for area in PARALLEL_EVAL_AREAS:
                d = par / "src" / family / area
                if not d.is_dir():
                    continue
                files = sorted(d.rglob("*.py"))
                areas[f"{family}/{area}"] = {
                    "files": len(files),
                    "lines": sum(len(f.read_text(encoding="utf-8", errors="replace")
                                     .splitlines()) for f in files),
                }
        tests = sorted((par / "tests").glob("test_*.py"))
        inv["parallel_eval"] = {
            "path": f"production-reference/{PARALLEL_EVAL_PATH}",
            "internal_name": "model_migration",
            "task_areas": sorted({a.split("/")[1] for a in areas}),
            "task_area_count": len({a.split("/")[1] for a in areas}),
            "model_families": sorted({a.split("/")[0] for a in areas}),
            "source_files": sum(v["files"] for v in areas.values()),
            "source_lines": sum(v["lines"] for v in areas.values()),
            "test_files": len(tests),
            "test_lines": sum(len(t.read_text(encoding="utf-8", errors="replace")
                                  .splitlines()) for t in tests),
            "by_area": areas,
            "what_it_has_that_we_do_not":
                "Gemini's NATIVE usageMetadata, carrying a per-modality token split (AUDIO vs "
                "TEXT), cached_tokens and thoughts_tokens. Reading Gemini through OpenRouter, "
                "as this repository does, returns a flatter shape, and we measured "
                "cached_tokens: 0 where theirs records substantial caching.",
            "where_it_agrees_with_us":
                "Its metrics schema carries label_* and analysis_* token fields plus a separate "
                "float `Audio Seconds` column, with NO transcription-stage token fields -- "
                "'Whisper reports duration, hence float Audio Seconds'. It also records that "
                "'Blank token cells mean not reported, never 0'. Both conclusions were reached "
                "independently here on 2026-08-22.",
            "correspondence_is_by_name_only":
                "Area names suggest sentiment -> sentiment_qa and documents -> tax invoice "
                "extraction. Neither has been verified against the other's schema.",
            "its_own_claude_md_is_stale":
                "It describes the project as 'currently a platform scaffold'. What is on disk "
                "is not a scaffold; every figure here is counted from the filesystem.",
        }

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
    par_inv = inv.get("parallel_eval")
    if par_inv:
        print(f"  parallel eval effort: {par_inv['task_area_count']} task areas, "
              f"{par_inv['source_lines']:,} source lines, {par_inv['test_files']} test files")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
