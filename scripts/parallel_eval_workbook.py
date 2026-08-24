"""Parse `docs/reports/OverallEvaluationReport.xlsx` into a committed JSON interface.

WHY THIS EXISTS. The workbook is the parallel evaluation project's result across **six
production tasks**, and it is the only measurement either team has of these models on REAL
data with human ground truth. Our own runs are synthetic. Any report we send that quotes it
must quote it exactly, so the figures are PARSED here rather than transcribed by hand, and
`--check` fails if the JSON drifts from the workbook.

The workbook is a hand-built summary, not a machine artifact: sections are upper-case banner
rows, dimensions are bare label rows, and metric rows carry a label in column A. The parser
therefore captures every data-bearing row verbatim (`rows`) and derives only what it can
prove (`derived`), so a layout change shows up as a missing derivation rather than a wrong
number.

Two derivations are the point of the exercise:

  * `weighted_average_formula` - what the headline row actually computes. In the CURRENT
    revision it is an honest mean everywhere except RTR-Fraud, which carries real weights
    (0.45 / 0.45 / 0.10, recovered exactly), and Tax-Invoice, whose headline averages the
    first two of its four dimensions because the other two are breakdowns.
    In the PREVIOUS revision it was the SUM of the dimension F1s over 2 in three tabs, which
    is why Sentiment QA published 1.2887 and Telesale 1.8704 - values above 1.0 that cannot be
    an F1. Run `--book "docs/reports/OverallEvaluationReport.xlsx"` to reproduce that; the
    detector reports it without being told which revision it is reading, which is the only
    reason this history can be trusted.
  * `recall_exactly_one` - the FN=0 signature. A confusion matrix built with FN=0 makes recall
    identically 1.0 and accuracy identically precision, so four reported metrics collapse to
    one. We predicted this from production's code (`sentiment_qa/fact_check_task.py:1004-1013`);
    counting the cells measures it in the published numbers instead. It has NOT been fixed:
    Tax-Invoice went from 8/8 pinned cells to 15/15 as the breakdown rows were added, and the
    current revision prints Accuracy identical to Precision rather than hiding the collapse
    behind a "105080/105386  99.7%" fraction.

Usage:
    python scripts/parallel_eval_workbook.py            # write
    python scripts/parallel_eval_workbook.py --check    # staleness only
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from itertools import product
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]

# The CURRENT revision. An earlier one, `OverallEvaluationReport.xlsx`, is kept beside it
# because two of this file's findings were about that revision and were fixed in this one:
# the headline row was the SUM of dimension F1s over 2 (so Sentiment QA published 1.2887 and
# Telesale 1.8704, values above 1.0), and Sentiment Retention has since been rescored --
# outcome F1 moved 0.4537 -> 0.9147 for the incumbent on the same 97 calls, and the raw
# per-dimension counts were dropped from the cells. Pass --book to parse the older file and
# confirm that history rather than take this comment's word for it.
BOOK = REPO / "docs" / "reports" / "OverallEvaluationReport (1).xlsx"
OUT = REPO / "docs" / "reports" / "parallel-eval-workbook.json"

METRIC_ROWS = {"F1-score", "Precision", "Recall", "Accuracy"}
HEADLINE = "weighted average f1"

# Column A labels that are banners rather than data. Compared upper-cased and stripped.
SECTIONS = ("WHAT RAN", "BUSINESS OUTCOME", "LATENCY", "TOKENS")


def num(x) -> float | None:
    """Leading number of a cell. Cells read like '0.9848   (195/198)' or '1,983,182'."""
    if isinstance(x, bool):
        return None
    if isinstance(x, (int, float)):
        return float(x)
    if isinstance(x, str):
        m = re.match(r"^\s*(-?\d*\.?\d+)", x.replace(",", ""))
        if m:
            return float(m.group(1))
    return None


def parse_sheet(ws) -> dict:
    rows = [list(r) for r in ws.iter_rows(values_only=True)]
    header = [("" if c is None else str(c).strip()) for c in rows[0]]
    width = max((i for i, c in enumerate(header) if c), default=0) + 1
    cols = header[1:width]

    # A column headed GROUND TRUTH is the corpus scored against itself, not a model.
    arms = [c for c in cols if c.upper() != "GROUND TRUTH"]

    out: dict = {"columns": cols, "arms": arms, "rows": []}
    section = dimension = None
    for r in rows[1:]:
        label = "" if r[0] is None else str(r[0]).strip()
        vals = [r[i] if i < len(r) else None for i in range(1, width)]
        has_data = any(v is not None and str(v).strip() for v in vals)
        if not label and not has_data:
            continue
        if not has_data:
            # A banner is upper-case, but only up to its explanatory dash: the workbook writes
            # "THE TRANSCRIPT   -   scored by meaning and by characters". Testing the whole
            # string for upper-case classifies those as dimensions and silently files the
            # transcript rows under BUSINESS OUTCOME, which is where they were until this was
            # fixed. Test the head of the label instead.
            headword = re.split(r"\s+-\s+", label, maxsplit=1)[0].strip()
            if label.upper() in SECTIONS:
                section, dimension = label.upper(), None
            elif (headword and headword.upper() == headword and len(headword) > 3
                  and not headword[0].isdigit() and any(ch.isalpha() for ch in headword)):
                section, dimension = label.upper(), None  # e.g. "THE TRANSCRIPT   -   ..."
            else:
                dimension = label
            continue
        out["rows"].append({
            "section": section, "dimension": dimension, "label": label,
            "values": {c: ("" if v is None else str(v).strip())
                       for c, v in zip(cols, vals)},
            "numeric": {c: num(v) for c, v in zip(cols, vals)},
        })
    return out


def derive(sheet: dict) -> dict:
    cols, arms = sheet["columns"], sheet["arms"]
    d: dict = {}

    f1_rows = [r for r in sheet["rows"]
               if r["label"] == "F1-score" and r["section"] == "BUSINESS OUTCOME"]
    head = next((r for r in sheet["rows"]
                 if r["label"].lower().startswith(HEADLINE)), None)
    d["dimensions"] = [r["dimension"] for r in f1_rows]

    if head and f1_rows:
        d["headline_label"] = head["label"]
        d["headline"] = {c: head["numeric"][c] for c in cols
                         if head["numeric"][c] is not None}
        verdicts = {}
        for c in cols:
            vals = [r["numeric"][c] for r in f1_rows if r["numeric"][c] is not None]
            rep = head["numeric"][c]
            if rep is None or not vals:
                continue
            s = sum(vals)
            verdicts[c] = {
                "reported": rep, "sum": round(s, 6), "mean": round(s / len(vals), 6),
                "is_mean": abs(rep - s / len(vals)) < 1e-9,
                "is_sum_over_2": abs(rep - s / 2) < 1e-9,
            }
        d["headline_check"] = verdicts
        every = list(verdicts.values())
        n = len(f1_rows)

        def prefix_mean_k() -> int | None:
            """Largest k < n such that the headline is the mean of the FIRST k dimensions.

            Tax-Invoice gained two breakdown dimensions whose values are NOT in its headline;
            the headline still averages the original two. Saying "mean" flatly would be wrong
            and saying UNRECOVERED would be lazy, so the prefix is found and named.
            """
            for k in range(n - 1, 1, -1):
                if all(
                    v is not None and abs(
                        v - sum(r["numeric"][c] for r in f1_rows[:k]
                                if r["numeric"][c] is not None) / k) < 1e-9
                    for c, v in ((c, verdicts[c]["reported"]) for c in verdicts)
                ):
                    return k
            return None

        if every and all(v["is_mean"] for v in every):
            d["weighted_average_formula"] = f"mean of all {n} dimension F1 scores"
        elif every and all(v["is_sum_over_2"] for v in every):
            d["weighted_average_formula"] = (
                f"SUM of dimension F1 / 2 -- NOT a mean; {n} dimensions divided by 2")
        elif (k := prefix_mean_k()) is not None:
            d["weighted_average_formula"] = (
                f"mean of the FIRST {k} of {n} dimension F1 scores -- the remaining "
                f"{n - k} are breakdowns and do not enter the headline")
            d["headline_covers_dimensions"] = [r["dimension"] for r in f1_rows[:k]]
        else:
            # Recover real weights on a coarse grid, then confirm to floating-point exactness.
            n = len(f1_rows)
            best = None
            if 2 <= n <= 3 and len(arms) >= n:
                for w in product(range(0, 101), repeat=n):
                    if sum(w) != 100:
                        continue
                    ws_ = [x / 100 for x in w]
                    err = max(abs(sum(f1_rows[i]["numeric"][c] * ws_[i] for i in range(n))
                                  - verdicts[c]["reported"])
                              for c in verdicts
                              if all(f1_rows[i]["numeric"][c] is not None for i in range(n)))
                    if best is None or err < best[0]:
                        best = (err, ws_)
            if best and best[0] < 1e-9:
                d["weighted_average_formula"] = "genuine weighted average"
                d["recovered_weights"] = dict(zip(d["dimensions"], best[1]))
                d["recovered_weights_max_error"] = best[0]
            else:
                d["weighted_average_formula"] = "UNRECOVERED"

    # The FN=0 signature, counted rather than argued.
    rec = [r for r in sheet["rows"] if r["label"] == "Recall"]
    cells = [(c, r["numeric"][c]) for r in rec for c in cols if r["numeric"][c] is not None]
    ones = [c for c, v in cells if abs(v - 1.0) < 1e-9]
    d["recall_exactly_one"] = {"cells": len(ones), "of": len(cells)}

    # Macro-F1 against micro-P/R shows up as a small gap; record the largest so a reader can
    # see it is a rounding-scale difference and not a contradiction.
    worst = 0.0
    by_dim: dict = {}
    for r in sheet["rows"]:
        if r["label"] in METRIC_ROWS:
            by_dim.setdefault((r["section"], r["dimension"]), {})[r["label"]] = r["numeric"]
    for m in by_dim.values():
        if not {"F1-score", "Precision", "Recall"} <= set(m):
            continue
        for c in cols:
            f, p, q = m["F1-score"][c], m["Precision"][c], m["Recall"][c]
            if None in (f, p, q) or p + q == 0:
                continue
            worst = max(worst, abs(2 * p * q / (p + q) - f))
    d["max_f1_vs_harmonic_gap"] = round(worst, 4)

    def row(prefix: str):
        return next((r for r in sheet["rows"]
                     if r["label"].lower().startswith(prefix.lower())), None)

    for key, prefix in (("calls_scored", "Calls scored"), ("model_calls", "Model calls"),
                        ("per_call", "Per call"), ("per_page", "Per page"),
                        ("speech_to_text", "Speech to text"), ("where", "Where it ran"),
                        ("runtime", "Runtime"), ("total_input", "Total Input"),
                        ("total_output", "Total Output")):
        r = row(prefix)
        if r:
            d[key] = r["values"]
    return d


def build(book: Path = BOOK) -> dict:
    try:
        import openpyxl  # pinned in requirements.txt
    except ModuleNotFoundError:  # pragma: no cover - the pin makes this unreachable in CI
        raise SystemExit(
            "openpyxl is required to re-parse the workbook (see requirements.txt)")

    wb = openpyxl.load_workbook(book, data_only=True)
    out: dict = {
        "generated_from": "scripts/parallel_eval_workbook.py",
        "source_file": book.name,
        "what_it_is":
            "The parallel evaluation project's measurement of internally hosted models against "
            "the incumbent, across six production tasks, on REAL data with human ground truth. "
            "Not our run and not reproduced here -- parsed from their published workbook so "
            "our reports cannot drift from it.",
        "tabs": {},
    }
    for name in wb.sheetnames:
        sheet = parse_sheet(wb[name])
        out["tabs"][name] = {"arms": sheet["arms"], "columns": sheet["columns"],
                             "derived": derive(sheet), "rows": sheet["rows"]}
    return out


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="parallel_eval_workbook")
    ap.add_argument("--check", action="store_true")
    ap.add_argument("--book", type=Path, default=BOOK,
                    help="workbook to parse; defaults to the current revision")
    ap.add_argument("--out", type=Path, default=None,
                    help="write here instead of the committed JSON (use with --book)")
    args = ap.parse_args(argv if argv is not None else sys.argv[1:])

    global OUT
    if args.out:
        OUT = args.out

    # The workbook itself is gitignored -- `*.gitignore:31` excludes every .xlsx, because
    # ground-truth workbooks arrive in that format and must not be committed. The parsed JSON
    # IS committed and is the interface. So a clone can hold the JSON and not the source, and
    # `--check` must say it cannot verify rather than crash on a missing file. Reported as
    # exit 2, distinct from a genuine staleness failure, so a caller can tell them apart.
    if not args.book.is_file():
        print(f"{args.book.name} is not present (it is gitignored); "
              f"{OUT.name} cannot be verified against its source.")
        return 2

    text = json.dumps(build(args.book), ensure_ascii=False, indent=2) + "\n"
    if args.check:
        current = OUT.read_text(encoding="utf-8") if OUT.exists() else ""
        ok = current == text
        print(f"{OUT.relative_to(REPO)} "
              + ("is up to date." if ok else "IS STALE -- regenerate it."))
        return 0 if ok else 1

    OUT.write_text(text, encoding="utf-8")
    data = json.loads(text)
    try:
        shown = OUT.relative_to(REPO)
    except ValueError:      # --out may point outside the repo for a one-off comparison
        shown = OUT
    print(f"wrote {shown}  ({len(data['tabs'])} tabs)")
    for name, tab in data["tabs"].items():
        d = tab["derived"]
        r = d["recall_exactly_one"]
        print(f"  {name:22s} {len(d['dimensions'])} dims  "
              f"headline: {d.get('weighted_average_formula')}   "
              f"recall==1.0 in {r['cells']}/{r['of']} cells")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
