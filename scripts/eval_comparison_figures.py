"""Assemble `docs/reports/eval-comparison.json` from the survey and the published results.

TWO SOURCES, JOINED HERE SO NEITHER CAN DRIFT.

  * `docs/reports/eval-comparison-survey.json` -- what production's evaluation code actually
    does, surveyed area by area from `production-reference/` and adversarially verified. This
    is the committed output of the survey workflow; it is evidence, so it is stored rather than
    regenerated on demand.
  * `docs/reports/parallel-eval-workbook.json` -- the published results, parsed from the
    parallel project's .xlsx by `scripts/parallel_eval_workbook.py`.

The join is by task area, and it is DELIBERATELY PARTIAL. `RTR-Fraud` appears in the results
workbook but has no area in this evaluation project -- it is a separate production application
(`production-reference/rtr-fraud-validation-main`). Recording that gap is the point: a report
that silently dropped it would read as though five areas covered six results.

Usage:
    python scripts/eval_comparison_figures.py            # write
    python scripts/eval_comparison_figures.py --check    # staleness only
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
SURVEY = REPO / "docs" / "reports" / "eval-comparison-survey.json"
BOOK = REPO / "docs" / "reports" / "parallel-eval-workbook.json"
FIGS = REPO / "docs" / "reports" / "gpu-performance.json"
OUT = REPO / "docs" / "reports" / "eval-comparison.json"

GENERATED = "2026-08-24"

# Task area in the evaluation project -> tab in the results workbook.
AREA_TO_TAB = {
    "documents": "Tax-Invoice",
    "sentiment": "Sentiment QA",
    "sentiment_mnp": "Sentiment MNP",
    "sentiment_retention": "Sentiment Retention",
    "sentiment_telesale": "Sentiment Telesale",
}


def build() -> dict:
    survey = json.loads(SURVEY.read_text(encoding="utf-8"))
    cons = survey["consolidated"]
    figs = json.loads(FIGS.read_text(encoding="utf-8"))
    tasks = figs["cross_task"]["tasks"]

    out: dict = {
        "generated": GENERATED,
        "generated_from": "scripts/eval_comparison_figures.py",
        "survey_source": SURVEY.name,
        "results_source": BOOK.name,
        "areas": cons["areas"],
        "cross_cutting": cons.get("cross_cutting", []),
        "fairness_verdict": cons["fairness_verdict"],
        "what_the_report_must_not_claim": cons.get("what_the_report_must_not_claim", []),
    }

    # Results, in the survey's own area order, so the two tables read against each other.
    results: dict = {}
    for a in cons["areas"]:
        tab = AREA_TO_TAB.get(a["area"])
        t = tasks.get(tab) if tab else None
        if not t or "winner" not in t:
            continue
        results[tab] = {
            "area": a["area"], "internal": t["internal"], "incumbent": t["incumbent"],
            "margin": t["margin"], "winner": t["winner"],
            "slowdown_x": t.get("slowdown_x"),
        }
    out["results_by_task"] = results

    # The gap, named rather than hidden.
    covered = {AREA_TO_TAB.get(a["area"]) for a in cons["areas"]}
    out["results_without_a_surveyed_area"] = {
        "tabs": [n for n in tasks if n not in covered],
        "why": "These results appear in the workbook but have no area in this evaluation "
               "project. RTR-Fraud is a separate production application "
               "(production-reference/rtr-fraud-validation-main) and was not surveyed here, so "
               "nothing in this report describes how its numbers were produced.",
    }

    # How the survey itself was checked -- the report claims adversarial verification, so the
    # count of what the adversary actually changed travels with it.
    per = survey.get("per_area_verdicts", [])
    out["verification"] = {
        "areas_surveyed": len(cons["areas"]),
        "areas_adversarially_verified": len(per),
        "claims_refuted": sum(len(v.get("refuted", [])) for v in per),
        "corrections_applied": sum(len(v.get("corrections", [])) for v in per),
        "gaps_the_adversary_found": sum(len(v.get("missed", [])) for v in per),
        "method": "Each area was surveyed by one agent citing file:line, then attacked by a "
                  "second agent instructed to refute rather than agree. The adversary's "
                  "corrections take precedence in the consolidated result.",
    }
    return out


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="eval_comparison_figures")
    ap.add_argument("--check", action="store_true")
    args = ap.parse_args(argv if argv is not None else sys.argv[1:])

    if not SURVEY.is_file():
        print(f"{SURVEY.relative_to(REPO)} does not exist -- run the survey workflow first.")
        return 1

    text = json.dumps(build(), ensure_ascii=False, indent=2) + "\n"
    if args.check:
        current = OUT.read_text(encoding="utf-8") if OUT.exists() else ""
        ok = current == text
        print(f"{OUT.relative_to(REPO)} "
              + ("is up to date." if ok else "IS STALE -- regenerate it."))
        return 0 if ok else 1
    OUT.write_text(text, encoding="utf-8")
    d = json.loads(text)
    print(f"wrote {OUT.relative_to(REPO)}")
    print(f"  {len(d['areas'])} areas, {sum(len(a.get('defects', [])) for a in d['areas'])} "
          f"defects, {len(d['cross_cutting'])} cross-cutting findings")
    print(f"  fairness verdict: {d['fairness_verdict']['verdict']}")
    print(f"  adversary refuted {d['verification']['claims_refuted']} claim(s), "
          f"applied {d['verification']['corrections_applied']} correction(s)")
    if d["results_without_a_surveyed_area"]["tabs"]:
        print(f"  results with no surveyed area: "
              f"{d['results_without_a_surveyed_area']['tabs']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
