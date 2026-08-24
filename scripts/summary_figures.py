"""Emit `docs/reports/summary.json` -- the first page: internal against production, six tasks.

WHAT THIS IS FOR. A manager should not have to hold six use cases in their head. This collapses
them to one column per side. Collapsing is where a summary can lie, so every choice is fixed
here, in code, with its reason and its exposure beside it.

THE INTERNAL COLUMN IS THE BEST-SCORING INTERNAL ARM PER TASK. That is a selection, so the
exposure is stated rather than hidden: on three of six tasks (MNP, Telesale, Retention) there is
only ONE internal arm and no selection happens at all. On Tax-Invoice and Sentiment QA the two
candidates are the same model under different prompts -- and on QA also a different speech
front-end -- so it is a pipeline choice, not a model choice. Only RTR-Fraud is a genuine
two-model pick, and its margin is 0.000855. Against the mean of all ten internal columns
(0.8764) best-per-task flatters by roughly 1.8 points, which is recorded in `selection_exposure`.

WHY THE HEADLINE IS A RATIO, NOT A GAP. Both sides are measured on benchmarks whose absolute
level says more about the benchmark than the model -- the incumbent itself only reaches 0.9188,
and several tabs are degenerate by construction. The ratio is the comparable quantity. Both
constructions are published (ratio of means, and mean of the per-task ratios) because they
differ by 0.11 points and a reader who finds the other one unaided will assume the difference
was hidden.

AND THE COUNTERWEIGHT IS PUBLISHED WITH IT. A ratio compresses near the ceiling: Tax-Invoice
reads 98.0% while its error rate goes from 0.00075 to 0.02085, a factor of 27.8. So
`error_basis` carries mean (1 - F1) for both sides -- internal makes about 30% more errors --
because that is the first thing a finance reviewer will ask for and it should not have to be
asked for.

WHAT IS DELIBERATELY NOT DONE: no use case is dropped from the headline. Excluding Sentiment
Retention would move the figure by +1.1 points, and the rescore that makes it arguably
provisional moved BOTH arms -- the incumbent led before it (93.4%) and leads by more after
(91.5%) -- so the exclusion improves the number without a reason that survives contact. It is
carried as a labelled sensitivity instead.

Usage:
    python scripts/summary_figures.py            # write
    python scripts/summary_figures.py --check    # staleness only
"""

from __future__ import annotations

import argparse
import json
import re
import statistics
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
MATRIX = REPO / "docs" / "reports" / "usecase-matrix.json"
BOOK = REPO / "docs" / "reports" / "parallel-eval-workbook.json"
OUT = REPO / "docs" / "reports" / "summary.json"

GENERATED = "2026-08-24"

# Tasks whose internal pipeline puts a speech-to-text stage in front of the labeller, while the
# incumbent takes the audio directly. Read from the workbook rather than assumed: a tab with no
# "Speech to text" row is not a speech task.
def _has_asr(book: dict, tab: str) -> bool:
    return any(r["label"] == "Speech to text" for r in book["tabs"][tab]["rows"])


def _frac(raw: str | None) -> float | None:
    """A cell as a 0-1 fraction, whichever of the workbook's two encodings it uses.

    Most rows print a plain fraction ('0.9848') or a fraction with a count in parens
    ('0.9848   (195/198)') -- the leading number is what we want. RTR-Fraud's Accuracy row
    prints the OPPOSITE shape, count first: '195/198  98.5%' -- there the leading number is a
    raw count, not a rate, and only the trailing percentage is the fraction. Preferring the
    trailing '%' when present, and falling back to the leading number otherwise, handles both
    without needing to know in advance which tab produced the cell.
    """
    raw = (raw or "").strip()
    if not raw:
        return None
    m = re.search(r"([\d.]+)\s*%\s*$", raw)
    if m:
        return float(m.group(1)) / 100
    m = re.match(r"^\s*(-?[\d.]+)", raw)
    return float(m.group(1)) if m else None


def rebuild_row(tab: dict, col: str, label_prefix: str) -> float | None:
    """Aggregate a BUSINESS OUTCOME row across a tab's dimensions, weighted EXACTLY the way
    that tab's F1 headline is -- same dimension order, same `recovered_weights` where the tab
    carries genuine weights (RTR-Fraud), same `headline_covers_dimensions` slice where the
    headline only covers part of the tab (Tax-Invoice: first 2 of 4). A second metric built by
    a different rule than F1 would be silently incomparable to it in a way nothing on the page
    explains.

    Matched by dimension + label PREFIX, not an exact label string: Sentiment QA's third
    dimension carries its accuracy-equivalent row as 'Accuracy  -  whole list matched', not
    'Accuracy'. An exact match silently drops that row and under-counts the tab to a mean of 2
    dimensions instead of 3, diverging from how the F1 headline (which does cover all 3) is
    built.
    """
    d = tab["derived"]
    by_dim: dict = {}
    for r in tab["rows"]:
        if r["section"] == "BUSINESS OUTCOME" and r["label"].startswith(label_prefix):
            by_dim[r["dimension"]] = r

    vals = []
    for dim in d["dimensions"]:
        r = by_dim.get(dim)
        if r is None:
            return None
        v = _frac(r["values"].get(col))
        if v is None:
            return None
        vals.append(v)

    w = d.get("recovered_weights")
    if w:
        return sum(v * w[dim] for v, dim in zip(vals, d["dimensions"]))
    covered = d.get("headline_covers_dimensions")
    if covered:
        return statistics.fmean(vals[: len(covered)])
    return statistics.fmean(vals)


def _close(a: float | None, b: float | None) -> bool:
    return a is not None and b is not None and abs(a - b) < 1e-3


def classify_accuracy(book: dict) -> dict:
    """For every dimension of every tab: is Accuracy identical to Precision, to Recall, or is
    it a genuinely independent number? Checked across EVERY arm on the tab (not just the two
    selected for the summary), because this is a structural property of how the tab's confusion
    matrix was built -- FN=0 makes Accuracy equal Precision; TN=0 makes it equal Recall -- not a
    coincidence of which two columns happen to be compared.

    This exists because presenting 'Accuracy' as independent information, when on most tasks
    it is arithmetically identical to a number already shown, is exactly the double-counting
    this project's earlier reviews of the same workbook flagged. The disclosure is derived here,
    from the data, so it cannot go stale if the workbook is regenerated with different numbers.
    """
    out: dict = {}
    for name, tab in book["tabs"].items():
        arms = [c for c in tab["arms"] if c.strip().upper() != "GROUND TRUTH"]
        by_dim: dict = {}
        for r in tab["rows"]:
            if r["section"] != "BUSINESS OUTCOME":
                continue
            if r["label"].startswith("Accuracy"):
                by_dim.setdefault(r["dimension"], {})["Accuracy"] = r
            elif r["label"] in ("Precision", "Recall"):
                by_dim.setdefault(r["dimension"], {})[r["label"]] = r

        dims = []
        for dim, rows in by_dim.items():
            acc = rows.get("Accuracy")
            if acc is None:
                continue

            def all_equal(other_row):
                if other_row is None:
                    return False
                pairs = [(a, acc["values"].get(a), other_row["values"].get(a)) for a in arms]
                pairs = [(a, x, y) for a, x, y in pairs if x and y]
                return bool(pairs) and all(
                    _close(_frac(x), _frac(y)) for _a, x, y in pairs)

            eq_p, eq_r = all_equal(rows.get("Precision")), all_equal(rows.get("Recall"))
            dims.append({"dimension": dim, "equals_precision": eq_p, "equals_recall": eq_r})

        if not dims:
            continue
        all_p = all(x["equals_precision"] for x in dims)
        all_r = all(x["equals_recall"] for x in dims)
        indep = [x for x in dims
                 if not x["equals_precision"] and not x["equals_recall"]]
        if all_p and all_r:
            summary = "identical to Precision and Recall on every dimension"
        elif all_p:
            summary = "identical to Precision on every dimension"
        elif all_r:
            summary = "identical to Recall on every dimension"
        elif not indep:
            summary = "identical to Precision or Recall on every dimension"
        elif len(indep) == len(dims):
            summary = "an independent measurement, not derivable from Precision or Recall"
        else:
            summary = (f"{len(indep)} of {len(dims)} dimensions independent "
                       f"({', '.join(x['dimension'] for x in indep)}); "
                       f"the rest equal Precision or Recall")
        out[name] = {"dimensions": dims, "summary": summary,
                     "independent": len(indep) == len(dims)}
    return out


def _col(book: dict, tab_name: str, arm: str) -> str:
    """Map a normalised arm label (single-spaced, as usecase-matrix.json stores it) back to
    the workbook's own column spelling (which carries double spaces before a parenthetical)."""
    for c in book["tabs"][tab_name]["arms"]:
        if " ".join(c.split()) == arm:
            return c
    raise KeyError(f"{tab_name}: no column matching {arm!r}")


def build() -> dict:
    mx = json.loads(MATRIX.read_text(encoding="utf-8"))
    book = json.loads(BOOK.read_text(encoding="utf-8"))

    tabs: dict = {}
    for c in mx["columns"]:
        tabs.setdefault(c["use_case"], []).append(c)
    order = list(tabs)

    inc = {t: next(c for c in cs if c["side"] == "incumbent") for t, cs in tabs.items()}
    pool = {t: [c for c in cs if c["side"] == "internal"] for t, cs in tabs.items()}
    best = {t: max(pool[t], key=lambda c: c["headline_f1"]) for t in order}

    per = []
    for t in order:
        g, q = inc[t], best[t]
        runners = sorted((c for c in pool[t] if c is not q),
                         key=lambda c: -c["headline_f1"])

        # Same dimension list, same weights as the F1 headline -- see rebuild_row's docstring
        # for why this cannot be a fresh, differently-weighted mean.
        acc_g = rebuild_row(book["tabs"][t], _col(book, t, g["arm"]), "Accuracy")
        acc_q = rebuild_row(book["tabs"][t], _col(book, t, q["arm"]), "Accuracy")
        assert acc_g is not None and acc_q is not None, (
            f"{t}: Accuracy did not aggregate for the selected arms -- a workbook revision "
            f"likely changed a row label; do not silently write null into the summary")

        per.append({
            "use_case": t,
            "incumbent_arm": g["arm"], "incumbent_f1": g["headline_f1"],
            "internal_arm": q["arm"], "internal_f1": q["headline_f1"],
            "pct_of_incumbent": round(q["headline_f1"] / g["headline_f1"] * 100, 2),
            "delta": round(q["headline_f1"] - g["headline_f1"], 4),
            "leader": "internal" if q["headline_f1"] > g["headline_f1"] else "incumbent",
            "internal_arms_available": len(pool[t]),
            "margin_over_runner_up": (round(q["headline_f1"] - runners[0]["headline_f1"], 6)
                                      if runners else None),
            "has_asr_stage": _has_asr(book, t),
            "latency_incumbent_s": g["latency_s"], "latency_internal_s": q["latency_s"],
            "accuracy_incumbent": round(acc_g, 4), "accuracy_internal": round(acc_q, 4),
            "accuracy_pct_of_incumbent": round(acc_q / acc_g * 100, 2),
            "input_tokens_incumbent": g["input_tokens"], "input_tokens_internal": q["input_tokens"],
            "output_tokens_incumbent": g["billable_output_tokens"],
            "output_tokens_internal": q["billable_output_tokens"],
            # Both the whole-run total and the per-item average are carried. The TABLE shows
            # per-item -- a run total invites summing across tasks whose runs covered different
            # numbers of items -- while the footnote still states each run's total, because
            # "what did this evaluation cost" is a fair question with a real answer.
            "cost_incumbent_usd": g["cost_usd"], "cost_internal_usd": q["cost_usd"],
            "cost_per_item_incumbent_usd": g.get("cost_per_item_usd"),
            "cost_per_item_internal_usd": q.get("cost_per_item_usd"),
            "cost_denominator": g.get("cost_denominator"),
            "cost_unit": g.get("cost_unit"),
            "cost_model_assumed": bool(g.get("model_assumed")),
            "cost_is_upper_bound": g.get("input_rate_kind") == "audio",
            "cost_floor_usd": g.get("cost_usd_floor"),
            "cost_per_item_floor_usd": g.get("cost_per_item_usd_floor"),
        })

    gm = statistics.fmean(p["incumbent_f1"] for p in per)
    qm = statistics.fmean(p["internal_f1"] for p in per)

    def group(pred) -> dict:
        sel = [p for p in per if pred(p)]
        g = statistics.fmean(p["incumbent_f1"] for p in sel)
        q = statistics.fmean(p["internal_f1"] for p in sel)
        return {"use_cases": [p["use_case"] for p in sel],
                "incumbent": round(g, 4), "internal": round(q, 4),
                "pct_of_incumbent": round(q / g * 100, 1)}

    five = [p for p in per if p["use_case"] != "Sentiment Retention"]
    g5 = statistics.fmean(p["incumbent_f1"] for p in five)
    q5 = statistics.fmean(p["internal_f1"] for p in five)

    all_internal = [c["headline_f1"] for t in order for c in pool[t]]
    worst = statistics.fmean(min(c["headline_f1"] for c in pool[t]) for t in order)

    # Speed, and the task that is missing from it. Sentiment QA's best-scoring arm published
    # no latency at all ("cannot meansure"; the round was "resumed over several days"), so it
    # cannot enter the range. That absence FLATTERS the internal side and must be declared:
    # the same task's OTHER configuration did publish a figure, and it is the slowest of the
    # whole set. Quoting "4.2x to 41.7x" without saying so understates the problem.
    speed, unmeasured = [], []
    for p in per:
        if p["latency_incumbent_s"] and p["latency_internal_s"]:
            speed.append({"use_case": p["use_case"],
                          "ratio": round(p["latency_internal_s"] / p["latency_incumbent_s"], 1)})
        else:
            other = [c for c in pool[p["use_case"]]
                     if c["latency_s"] and c is not best[p["use_case"]]]
            alt = max(other, key=lambda c: c["latency_s"]) if other else None
            unmeasured.append({
                "use_case": p["use_case"],
                "arm": p["internal_arm"],
                "workbook_says": next(
                    (c.get("latency_raw") for c in pool[p["use_case"]]
                     if c["arm"] == p["internal_arm"]), None),
                "other_arm": alt["arm"] if alt else None,
                "other_arm_latency_s": alt["latency_s"] if alt else None,
                "other_arm_ratio": (round(alt["latency_s"] / p["latency_incumbent_s"], 1)
                                    if alt and p["latency_incumbent_s"] else None),
            })

    costed = [inc[t] for t in order if inc[t]["cost_usd"] is not None]
    total_cost = sum(c["cost_usd"] for c in costed)

    tok = {side: {"input": sum(d[t]["input_tokens"] for t in order),
                  "output": sum(d[t]["billable_output_tokens"] for t in order)}
           for side, d in (("incumbent", inc), ("internal", best))}

    accuracy_classes = classify_accuracy(book)

    return {
        "generated": GENERATED,
        "generated_from": "scripts/summary_figures.py",
        "source": MATRIX.name,
        "internal_arm_rule":
            "The best-scoring internal arm on each task. Recorded with its exposure: on three "
            "of six tasks only one internal arm ran, so no selection happened; on two the "
            "candidates are one model under different prompts; only RTR-Fraud is a genuine "
            "two-model pick and its margin is 0.000855.",
        "internal_arms": {t: best[t]["arm"] for t in order},
        "per_use_case": per,
        "quality": {
            "incumbent": round(gm, 4), "internal": round(qm, 4),
            "delta": round(qm - gm, 4),
            "pct_ratio_of_means": round(qm / gm * 100, 1),
            "pct_mean_of_ratios": round(
                statistics.fmean(p["pct_of_incumbent"] for p in per), 1),
            "aggregation": "unweighted mean of the six published headlines; one task, one vote",
            "why_unweighted":
                "Each task is one migration decision, so each gets one vote. Weighting by "
                "evaluation-set size would let whoever assembled the eval decide the headline, "
                "and neither source states production volumes.",
        },
        "accuracy": {
            "incumbent": round(statistics.fmean(p["accuracy_incumbent"] for p in per), 4),
            "internal": round(statistics.fmean(p["accuracy_internal"] for p in per), 4),
            "pct_ratio_of_means": round(
                statistics.fmean(p["accuracy_internal"] for p in per)
                / statistics.fmean(p["accuracy_incumbent"] for p in per) * 100, 1),
            "pct_mean_of_ratios": round(
                statistics.fmean(p["accuracy_pct_of_incumbent"] for p in per), 1),
            "aggregation": "same dimensions, same weights as the F1 headline on each tab, "
                          "applied to the Accuracy row instead of F1-score",
        },
        "accuracy_disclosure": {
            "per_task": accuracy_classes,
            # Deliberately does NOT repeat "Accuracy is not independent information" -- the
            # page's own lead-in sentence already says that; this continues it rather than
            # restating it, and rendering both back to back read as an obvious duplicate the
            # first time this was checked.
            "note":
                "RTR-Fraud, Tax-Invoice and Sentiment Telesale each score it identical to "
                "Precision or Recall on every dimension -- a consequence of those tabs' "
                "confusion matrices being built with FN=0 or TN=0, the same construction "
                "already documented for this workbook. Only Sentiment MNP, Sentiment "
                "Retention and one dimension of Sentiment QA carry an accuracy that could not "
                "already be read off a column shown elsewhere. It is published here because it "
                "is a real number the workbook prints, not because it corroborates the F1 "
                "column with a second, independent look.",
        },
        "error_basis": {
            "incumbent_mean_error": round(1 - gm, 4),
            "internal_mean_error": round(1 - qm, 4),
            "internal_errors_relative": round((1 - qm) / (1 - gm), 3),
            "why": "A ratio of F1 compresses near the ceiling. On an error basis the same six "
                   "results read as roughly 30% more errors. Both are true; publish both, "
                   "because the second is the first thing a finance reviewer asks for.",
        },
        "where_the_gap_is": {
            "without_asr": group(lambda p: not p["has_asr_stage"]),
            "with_asr": group(lambda p: p["has_asr_stage"]),
            "reading":
                "The tasks whose internal pipeline reads the source directly are close; the "
                "four that put a speech-to-text stage in front carry the gap. Corroborated "
                "inside the workbook: Retention's internal transcript scores a mean CER above "
                "1.0, and Sentiment QA's transcript quality tracks which ASR model ran rather "
                "than which prompt.",
            "caveat":
                "Two cautions. The two direct tasks are also the two where the incumbent scores "
                "0.9877 and 0.9993, so part of that closeness is a ceiling with little headroom "
                "to lose, and it is only two data points. And neither source publishes a single "
                "token, second or dollar for the speech stage, so this identifies where the gap "
                "is without establishing what closing it would cost.",
        },
        "sensitivity_excluding_retention": {
            "incumbent": round(g5, 4), "internal": round(q5, 4),
            "pct_of_incumbent": round(q5 / g5 * 100, 1),
            "why_it_is_not_the_headline":
                "Sentiment Retention was rescored between two workbook revisions this week -- "
                "outcome F1 moved 0.4537 to 0.9147 on identical calls, tokens and latency -- "
                "which makes it arguably provisional. It is NOT excluded from the headline, "
                "because the rescore moved both arms: the incumbent led before it (93.4%) and "
                "leads by more after it (91.5%). Dropping the task improves the figure by 1.1 "
                "points without a reason that survives the question 'did it change who leads?'",
        },
        "selection_exposure": {
            "mean_of_all_internal_arms": round(statistics.fmean(all_internal), 4),
            "mean_of_worst_arm_per_task": round(worst, 4),
            "best_per_task_flatters_by": round(qm - statistics.fmean(all_internal), 4),
            "tasks_with_only_one_internal_arm": [p["use_case"] for p in per
                                                 if p["internal_arms_available"] == 1],
            "only_genuine_two_model_pick": "RTR-Fraud, margin 0.000855 over Gemma-4-12B",
        },
        "speed": {
            "per_use_case": speed,
            "range": [min(s["ratio"] for s in speed), max(s["ratio"] for s in speed)],
            "median_ratio": round(statistics.median(s["ratio"] for s in speed), 1),
            "tasks_measured": len(speed),
            "tasks_unmeasured": unmeasured,
            "widest_known_ratio": max(
                [s["ratio"] for s in speed]
                + [u["other_arm_ratio"] for u in unmeasured if u["other_arm_ratio"]]),
            "note":
                f"Per-item latency on the {len(speed)} tasks that publish it for the selected "
                f"arm. Sentiment QA does not: its cell reads 'cannot meansure' and the round "
                f"was 'resumed over several days', so that run cannot be timed. The absence "
                f"flatters the internal side, so the figure that does exist for that task is "
                f"published beside it -- its other configuration ran at "
                + ", ".join(f"{u['other_arm_ratio']}x" for u in unmeasured
                            if u["other_arm_ratio"])
                + ", the slowest of the whole set.",
        },
        "cost": {
            "incumbent_usd": round(total_cost, 2),
            "incumbent_note": "OpenRouter list rates for the whole evaluation, six tasks. Not "
                              "an invoice, not annualised, not divided per item.",
            "internal_note":
                "Not metered. Neither source publishes a cost, price, GPU-hour or utilisation "
                "figure for the internal endpoint in any of its six tabs. That is a "
                "measurement never taken -- it is not $0, not free and not n/a.",
            "per_item_units": sorted({p["cost_unit"] for p in per if p["cost_unit"]}),
            # The range is stated with its units attached rather than a mean, so no two units
            # are ever combined. It survives both live assumptions: the audio/text question
            # only moves the four call figures, which sit inside the range, and even if
            # RTR-Fraud ran a Pro-tier model (6.21x) it remains the minimum.
            "per_item_range": {
                "min": min(p["cost_per_item_incumbent_usd"] for p in per
                           if p["cost_per_item_incumbent_usd"] is not None),
                "min_task": min((p for p in per if p["cost_per_item_incumbent_usd"] is not None),
                                key=lambda p: p["cost_per_item_incumbent_usd"])["use_case"],
                "max": max(p["cost_per_item_incumbent_usd"] for p in per
                           if p["cost_per_item_incumbent_usd"] is not None),
                "max_task": max((p for p in per if p["cost_per_item_incumbent_usd"] is not None),
                                key=lambda p: p["cost_per_item_incumbent_usd"])["use_case"],
            },
            "why_per_item_cannot_be_aggregated":
                "The six per-item figures are denominated in three different units -- per "
                "submission on RTR-Fraud, per page on Tax-Invoice, per call on the four "
                "Sentiment tasks. A mean of them divides dollars by "
                "submissions-plus-pages-plus-calls, which is not a quantity; a sum of the run "
                "totals answers a different question and rescales with how many items each "
                "run happened to cover. The table therefore shows no aggregate cost, and each "
                "run's own total is stated in the note below instead.",
            "break_even_is_not_computable":
                "This evaluation cannot say what migrating would save. Three inputs are "
                "missing: production volume per task, the internal endpoint's cost per hour "
                "and its throughput, and the cost of the speech-to-text stage. Asking for "
                "those three is a firmer position than a saving that cannot be evidenced.",
        },
        "tokens": {
            **tok,
            "caveat":
                "Not like-for-like and not an efficiency claim. The incumbent's input includes "
                "the call audio charged as tokens; the internal side's is a text transcript "
                "produced by a stage whose own tokens appear nowhere in the workbook.",
        },
        "parity_not_wins": {
            "tasks": [p["use_case"] for p in per if p["leader"] == "internal"],
            "why": "Both leads are inside their own measurement noise. RTR-Fraud's +0.0045 was "
                   "bought with 594 internal model calls against the incumbent's 198 over the "
                   "same 198 submissions. Telesale's +0.0080 is over 25 calls scored against "
                   "26, and turns on one dimension of four -- the incumbent leads the other "
                   "three, and the one internal wins carries 3 criteria against the others' "
                   "15, 12 and 9. Present these as parity, not as wins.",
        },
        "must_not_claim": [
            "That the internal models match the incumbent, or that the gap is within noise. "
            "They are behind under every defensible aggregation, and neither source publishes "
            "any dispersion or interval that could support a noise claim.",
            "That the internal side uses fewer tokens, or that this is efficiency. It is an "
            "artefact of an unmeasured speech stage and a different tokeniser.",
            "That migrating saves money. No internal cost figure exists in either source.",
            "That 'weighted Average F1' is a weighted average. Only RTR-Fraud is; Tax-Invoice "
            "covers 2 of its 4 dimensions and the rest are plain means.",
            "That Accuracy is an independent check on the F1 column. On RTR-Fraud, Tax-Invoice and "
            "Sentiment Telesale it is arithmetically identical to Precision or Recall; only "
            "MNP, Retention and one dimension of QA carry a genuinely independent figure.",
        ],
    }


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="summary_figures")
    ap.add_argument("--check", action="store_true")
    args = ap.parse_args(argv if argv is not None else sys.argv[1:])

    text = json.dumps(build(), ensure_ascii=False, indent=2) + "\n"
    if args.check:
        current = OUT.read_text(encoding="utf-8") if OUT.exists() else ""
        ok = current == text
        print(f"{OUT.relative_to(REPO)} "
              + ("is up to date." if ok else "IS STALE -- regenerate it."))
        return 0 if ok else 1
    OUT.write_text(text, encoding="utf-8")
    d = json.loads(text)
    q, e, g = d["quality"], d["error_basis"], d["where_the_gap_is"]
    print(f"wrote {OUT.relative_to(REPO)}")
    print(f"  quality   incumbent {q['incumbent']}  internal {q['internal']}  "
          f"= {q['pct_ratio_of_means']}% (mean of ratios {q['pct_mean_of_ratios']}%)")
    a = d["accuracy"]
    print(f"  accuracy  incumbent {a['incumbent']}  internal {a['internal']}  "
          f"= {a['pct_ratio_of_means']}%")
    for t, cls in d["accuracy_disclosure"]["per_task"].items():
        print(f"    {t:22s} {cls['summary']}")
    print(f"  errors    {e['incumbent_mean_error']} vs {e['internal_mean_error']}  "
          f"internal makes {e['internal_errors_relative']}x the errors")
    print(f"  no ASR    {g['without_asr']['pct_of_incumbent']}%   "
          f"with ASR {g['with_asr']['pct_of_incumbent']}%")
    s = d["sensitivity_excluding_retention"]
    print(f"  ex-Retention {s['pct_of_incumbent']}%  (NOT the headline)")
    print(f"  speed     {d['speed']['range'][0]}x to {d['speed']['range'][1]}x slower")
    print(f"  cost      incumbent ${d['cost']['incumbent_usd']}, internal not metered")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
