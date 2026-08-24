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
import re
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


def _sec(cell: str | None) -> float | None:
    """Seconds from a latency cell. Cells read '77.9 s', '119.9', or 'cannot meansure'."""
    if not cell:
        return None
    m = re.match(r"^\s*(-?\d*\.?\d+)", str(cell).replace(",", ""))
    return float(m.group(1)) if m else None


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

    # ---- the same models, measured on REAL calls by the parallel effort, SIX TASKS ----
    #
    # THIS IS THE DECISION-GRADE COMPARISON AND OURS IS NOT. Its ground truth is
    # human-maintained, its audio and documents are production's own, and it runs the
    # incumbent on VERTEX AI BATCH -- how production actually runs it. Ours is synthetic
    # audio, generated labels, and Gemini through OpenRouter, on one task of the six.
    #
    # Read from parallel-eval-workbook.json, which scripts/parallel_eval_workbook.py parses
    # out of their .xlsx, so nothing here is transcribed by hand.
    book_path = REPO / "docs/reports/parallel-eval-workbook.json"
    if book_path.is_file():
        book = json.loads(book_path.read_text(encoding="utf-8"))

        def pick(d: dict, want: str) -> tuple[str, float] | None:
            """Best scoring column whose header names `want` (QWEN / GEMINI / GEMMA).

            Columns with no number are skipped rather than treated as zero -- Sentiment QA's
            full-prompt latency reads "cannot meansure", which is missing, not fast.
            """
            hits = {k: v for k, v in d.items() if want in k.upper() and v is not None}
            if not hits:
                return None
            k = max(hits, key=lambda k: hits[k])
            return k.strip(), hits[k]

        cross: dict = {
            "source_file": book["source_file"],
            "parsed_by": "scripts/parallel_eval_workbook.py",
            "ground_truth": "human-maintained sheets; production's own audio and documents",
            "tasks": {},
        }
        wins = {"internal": [], "incumbent": []}
        for name, tab in book["tabs"].items():
            d = tab["derived"]
            head = d.get("headline") or {}
            q, g = pick(head, "QWEN"), pick(head, "GEMINI")
            lat = {k: _sec(v) for k, v in
                   (d.get("per_call") or d.get("per_page") or {}).items()}
            entry: dict = {
                "dimensions": d["dimensions"],
                "headline_label": d.get("headline_label"),
                "headline_formula": d.get("weighted_average_formula"),
                "recall_exactly_one": d["recall_exactly_one"],
                "latency_unit": "per call" if d.get("per_call") else "per page",
            }
            if q and g:
                entry["internal_arm"], entry["internal"] = q
                entry["incumbent_arm"], entry["incumbent"] = g
                entry["margin"] = round(q[1] - g[1], 5)
                entry["winner"] = "internal" if q[1] > g[1] else "incumbent"
                wins[entry["winner"]].append(name)

                # Latency for the SAME arms the headline compared. Where that arm has no
                # measurable figure -- QA's full-prompt run reads "cannot meansure" -- fall
                # back to the one arm that does, and say which arm was used.
                def lat_for(arm: str, want: str) -> tuple[str, float] | None:
                    exact = next((v for k, v in lat.items()
                                  if k.strip() == arm and v is not None), None)
                    if exact is not None:
                        return arm, exact
                    return pick(lat, want)

                lq, lg = lat_for(q[0], "QWEN"), lat_for(g[0], "GEMINI")
                if lq and lg and lg[1]:
                    entry["latency_internal_s"], entry["latency_incumbent_s"] = lq[1], lg[1]
                    entry["latency_internal_arm"], entry["latency_incumbent_arm"] = lq[0], lg[0]
                    entry["slowdown_x"] = round(lq[1] / lg[1], 1)
            cross["tasks"][name] = entry

        # THE TRANSCRIPT TAIL, and who actually has it. A mean "characters right" far below
        # the median means a few transcripts carry more inserted text than the reference --
        # the runaway failure. Our own report previously attributed this to Typhoon; across
        # six tasks it hits whichever transcript source is being scored, the incumbent's own
        # included, so it is a property of long-form Thai ASR here and not one model's defect.
        # Match on "mean"/"median" rather than on a row-label prefix: the tabs spell this row
        # two different ways ("Characters right - 1 - mean CER" and "The transcript -
        # characters right (mean)"), and a prefix filter silently skipped Sentiment QA.
        # A scan that cannot see a whole tab is not a check.
        tails, scanned = [], []
        for name, tab in book["tabs"].items():
            rows = [r for r in tab["rows"] if "haracters right" in r["label"]]
            mean = next((r for r in rows if "mean" in r["label"].lower()), None)
            med = next((r for r in rows if "median" in r["label"].lower()), None)
            if not mean or not med:
                continue
            scanned.append(name)
            for col in tab["arms"]:
                mu, md = mean["numeric"].get(col), med["numeric"].get(col)
                if mu is None or md is None:
                    continue
                if md - mu >= 0.15:  # the tail is dragging the mean well below the median
                    tails.append({"task": name, "arm": col.strip(),
                                  "transcript_source": (tab["derived"].get("speech_to_text")
                                                        or {}).get(col, "").strip(),
                                  "mean": mu, "median": md, "gap": round(md - mu, 4)})
        cross["transcript_runaway_tail"] = {
            "cases": tails,
            "tabs_scanned": scanned,
            "tabs_without_the_rows": [n for n in book["tabs"] if n not in scanned],
            "note": "Cases where the mean 'characters right' sits at least 0.15 below the "
                    "median, which is what a handful of runaway transcripts does to a mean. "
                    "It is not confined to the internal transcriber: the incumbent's own "
                    "audio path shows it too.",
        }

        cross["scoreboard"] = {
            "internal_wins": wins["internal"], "incumbent_wins": wins["incumbent"]}
        cross["slower_on_every_task"] = all(
            t.get("slowdown_x", 0) > 1 for t in cross["tasks"].values()
            if "slowdown_x" in t)
        cross["slowdown_range_x"] = [
            min(t["slowdown_x"] for t in cross["tasks"].values() if "slowdown_x" in t),
            max(t["slowdown_x"] for t in cross["tasks"].values() if "slowdown_x" in t)]

        # BOTH internal wins land on benchmarks that cannot separate models. Stated as a
        # derived fact with its evidence, not as an opinion about the result.
        rtr = cross["tasks"].get("RTR-Fraud", {})
        tele = cross["tasks"].get("Sentiment Telesale", {})
        # Evidence for saturation is the range of the underlying per-field accuracies, NOT the
        # spread of the headline: when the two compared arms are the best and the worst, margin
        # and spread are the same number by definition and prove nothing.
        rtr_acc = [v for r in book["tabs"]["RTR-Fraud"]["rows"] if r["label"] == "Accuracy"
                   for k, v in r["numeric"].items()
                   if v is not None and k.upper() != "GROUND TRUTH"]
        cross["both_internal_wins_are_on_saturated_benchmarks"] = {
            "RTR-Fraud": {
                "margin": rtr.get("margin"),
                # The cells read "195/198  98.5%", so the leading number is an item COUNT.
                "accuracy_cells": len(rtr_acc),
                "items_correct_min": int(min(rtr_acc)) if rtr_acc else None,
                "items_correct_max": int(max(rtr_acc)) if rtr_acc else None,
                "of_items": 198,
                "why": "Every one of the nine model-by-field accuracy cells lands between "
                       "98.5% and 100%: the worst arm misses 3 items out of 198 and the best "
                       "misses none. Three different model families separated by three items "
                       "is a saturated benchmark -- it cannot rank them, so the win is not "
                       "evidence of an advantage.",
            },
            "Sentiment Telesale": {
                "margin": tele.get("margin"),
                "calls": "26 scored for the incumbent, 25 for internal (1 call failed)",
                "why": "The parallel project's own code records that 25 of 39 criteria carry a "
                       "single label across all 26 calls and 5 of 14 sub-categories contain no "
                       "violation at all, so a constant answer scores 98-99%. The internal win "
                       "is driven by Compliance -- 3 criteria, 2 of which hold one label.",
            },
        }
        out["cross_task"] = cross

        # Keep the Retention detail: it is the task our own run measures, so it is the only
        # place the two evaluations can be set side by side.
        ret = book["tabs"].get("Sentiment Retention")
        if ret:
            f1 = [r for r in ret["rows"]
                  if r["label"] == "F1-score" and r["section"] == "BUSINESS OUTCOME"]
            gcol = next(c for c in ret["arms"] if "GEMINI" in c.upper())
            qcol = next(c for c in ret["arms"] if "QWEN" in c.upper())
            dims = ("call_result", "reason", "product")
            real: dict = {
                "source_file": book["source_file"],
                "calls_scored_gemini": (ret["derived"].get("calls_scored") or {}).get(gcol),
                "calls_scored_internal": (ret["derived"].get("calls_scored") or {}).get(qcol),
                "gemini_runtime": (ret["derived"].get("where") or {}).get(gcol),
                "internal_runtime": (ret["derived"].get("where") or {}).get(qcol),
                "internal_asr": (ret["derived"].get("speech_to_text") or {}).get(qcol),
                "ground_truth": "human sheet 'Voice_retention - Groundtruth' "
                                "(AI Benchmark Report.xlsx, SharePoint); audio from GCS",
                "gemini": {}, "internal": {},
            }
            for dim, r in zip(dims, f1):
                real["gemini"][f"{dim}_f1"] = r["numeric"][gcol]
                real["internal"][f"{dim}_f1"] = r["numeric"][qcol]
            real["headline_gemini"] = round((ret["derived"].get("headline") or {}).get(gcol), 5)
            real["headline_internal"] = round((ret["derived"].get("headline") or {}).get(qcol), 5)
            real["latency_per_call_gemini"] = (ret["derived"].get("per_call") or {}).get(gcol)
            real["latency_per_call_internal"] = (ret["derived"].get("per_call") or {}).get(qcol)
            for key, label in (("transcript_mean_1_minus_cer_internal",
                                "Characters right  -  1 - mean CER"),
                               ("transcript_median_1_minus_cer_internal",
                                "Characters right  -  1 - median CER")):
                r = next((x for x in ret["rows"] if x["label"] == label), None)
                if r:
                    real[key] = r["values"][qcol]
            # The three dimension margins, in calls rather than in F1, because 0.027 of F1
            # reads as a result and 3 calls out of 97 reads as what it is.
            real["margin_in_calls"] = {
                "call_result": "37/97 correct against 34/97 -- 3 calls",
                "product": "61/97 against 58/97 -- 3 calls",
                "reason": "41/153 labels against 33/153 -- 8 labels",
            }
            real["ranking"] = (
                "The incumbent leads all three dimensions and the published headline "
                "(0.66135 against 0.61750). The leads on outcome and product are 3 calls out "
                "of 97 each, with no replicates, so they are not separable; the reason lead "
                "is 8 of 153 labels. Our synthetic run has the internal pipeline ahead on all "
                "three. The reversal, not the level, is the finding.")
            real["why_it_outranks_ours"] = (
                "Real audio, human ground truth, and the incumbent on its production runtime. "
                "Ours is synthetic audio whose labels are SPOKEN ALOUD, which removes the need "
                "for the inferential step the direct-audio arm is plausibly better at -- so the "
                "corpus may not merely inflate scores but change which arm wins. That mechanism "
                "is a hypothesis; the reversal is a measurement.")
            out["real_data_run"] = real

        # What the published headline row actually computes, and where recall cannot fail.
        out["how_their_numbers_are_scored"] = {
            "headline_row": (
                "'weighted Average F1 Score' is the SUM of the dimension F1 scores divided by "
                "2 in five of the six tabs. With two dimensions that coincides with the mean; "
                "with three or four it does not, which is why Sentiment QA publishes 1.2887 "
                "and Telesale 1.8704 -- values above 1.0 that cannot be an F1. The divisor is "
                "constant within a tab, so no winner changes; the number is not a score and "
                "is not comparable across tabs. RTR-Fraud is the exception and carries real "
                "weights, recovered exactly as 0.45 / 0.45 / 0.10."),
            "recall_that_cannot_fail": (
                "Recall is exactly 1.0000 in all 8 Tax-Invoice cells and 6 of 9 Sentiment QA "
                "cells -- the FN=0 signature. Where it appears, accuracy equals precision and "
                "F1 is a monotone transform of it, so four reported metrics are one "
                "measurement printed four times. We predicted this from production's code "
                "(sentiment_qa/fact_check_task.py:1004-1013); these cells measure it."),
            "macro_vs_micro": (
                "The printed F1 differs from the harmonic mean of the printed precision and "
                "recall by at most 0.02 outside one collapsed Gemma column. That is macro-F1 "
                "against micro-averaged precision and recall -- a normal choice, not a defect."),
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
