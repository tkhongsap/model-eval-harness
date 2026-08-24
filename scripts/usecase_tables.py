"""Render `docs/reports/usecase-comparison.html` -- one table per use case, incumbent vs ours.

WHAT THIS IS FOR. The other two reports argue. This one just shows the numbers: for each of the
six production use cases, one table, incumbent column first, candidate columns beside it, rows
grouped into what the decision rests on, what ran, what it costs, and what the text came out
like. It is the page to put in front of someone who has ten minutes.

Every figure is read from `docs/reports/parallel-eval-workbook.json`, which
`scripts/parallel_eval_workbook.py` parses out of the published .xlsx. Nothing is typed by
hand, and `--check` fails if the committed HTML has drifted from the workbook.

Two things the layout deliberately does NOT do:

  * It does not print Precision, Recall and Accuracy beside F1. On Tax-Invoice and Sentiment QA
    those are not independent numbers -- the confusion matrix is built with FN=0, so recall is
    pinned at 1.0000 and accuracy equals precision. Printing four columns that carry one
    measurement is how a reader gets misled; the footnote says so instead.
  * It does not name a winner per use case. The margins are small, several benchmarks cannot
    separate the arms at all, and the two sides are not fed the same input. Bolding the larger
    number in a row is as far as this page goes.

Usage:
    python scripts/usecase_tables.py            # write
    python scripts/usecase_tables.py --check    # staleness only
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
SRC = REPO / "docs" / "reports" / "parallel-eval-workbook.json"
OUT = REPO / "docs" / "reports" / "usecase-comparison.html"
HEAD = REPO / "scripts" / "assets" / "gpu-report-head.html"

STAMP = "2026-08-24"

# One line per use case, so a reader outside the project knows what the task is.
WHAT_IT_IS = {
    "RTR-Fraud": "fraud checks on retail transaction images",
    "Tax-Invoice": "field extraction from tax invoice documents",
    "Sentiment QA": "call-centre QA against a 22-criterion rubric",
    "Sentiment MNP": "number-portability calls: outcome and reason",
    "Sentiment Telesale": "telesales calls against a 39-criterion rubric",
    "Sentiment Retention": "retention calls: outcome, reason and product",
}

# Row label -> the small grey line under it. Keyed on the workbook's own labels.
SUBLABEL = {
    "Model": "which model answered",
    "Speech to text": "stage 1, where there is one",
    "Prompt": "which prompt version",
    "Prompt version": "which prompt version",
    "Where it ran": "runtime",
    "Runtime": "runtime",
    "Calls scored": "how many items entered the score",
    "Model calls": "requests made",
    "Documents fed in": "corpus size",
    "Per call": "median, seconds",
    "Per page": "median, seconds",
    "Time for the whole round  -  seconds": "whole round, seconds",
    "Total Input": "input tokens, whole round",
    "Total Output": "output tokens, whole round",
    "Total tokens": "input + output",
}

GROUPS = [
    ("The decision production acts on", "the headline and the dimensions under it"),
    ("What ran", "the two pipelines side by side"),
    ("What it costs to run", "tokens and wall clock for the whole round"),
    ("The text it produced", "not part of the headline score"),
]


def esc(s) -> str:
    out = str(s).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
    return "".join(c if ord(c) < 127 else f"&#{ord(c)};" for c in out)


def tidy(s: str) -> str:
    """Workbook labels carry runs of spaces around their dashes."""
    return re.sub(r"\s{2,}", " ", str(s or "")).strip()


def split_label(s: str) -> tuple[str, str]:
    """'Did the customer stay or churn  -  4 outcomes' -> label, sublabel."""
    s = tidy(s)
    m = re.split(r"\s+-\s+", s, maxsplit=1)
    return (m[0], m[1]) if len(m) == 2 else (s, "")


def order_columns(tab: dict) -> tuple[list[str], list[str]]:
    """Incumbent first, then candidates. GROUND TRUTH is a reference column, not an arm.

    RTR-Fraud prints GEMINI last; every other tab prints it first. Ordering by name rather
    than by position is what keeps the incumbent in the same column on every table.
    """
    arms = [c for c in tab["arms"] if c.strip().upper() != "GROUND TRUTH"]
    inc = [c for c in arms if "GEMINI" in c.upper()]
    cand = [c for c in arms if "GEMINI" not in c.upper()]
    return inc, cand


def fmt(raw: str, n: float | None, score: bool = False) -> str:
    """Show the workbook's own text, tidied.

    On score rows a bare number is padded to 4 decimals, because the workbook mixes "1" and
    "0.9985" in the same column and a reader compares those two badly. This is presentation
    only: the value is untouched, and any cell carrying text (counts, notes) is passed through
    exactly as published.
    """
    t = tidy(raw)
    if not t:
        return "&mdash;"
    if score and n is not None and re.fullmatch(r"-?\d*\.?\d+", t.replace(",", "")):
        return f"{n:.4f}"
    # Escaped here rather than at the call site: these are workbook cells, and one of them
    # reads "1 call failed (stream exceeded 900s x1)" with a multiplication sign in it.
    # Unescaped, that leaves a non-ASCII byte on a page that must survive any charset, and a
    # cell containing "<" would break the markup outright.
    return esc(t)


def build(d: dict) -> str:
    head = HEAD.read_text(encoding="utf-8").rstrip("\n")
    head = head.replace("<title>Internal GPU Model Performance</title>",
                        "<title>Six Use Cases, Side by Side</title>", 1)

    tables = []
    for name, tab in d["tabs"].items():
        inc, cand = order_columns(tab)
        cols = inc + cand
        if not cols:
            continue
        der = tab["derived"]
        rows_by_section: dict = {}
        for r in tab["rows"]:
            rows_by_section.setdefault(r["section"] or "", []).append(r)

        body: list[str] = []

        def group(title: str, sub: str) -> None:
            body.append(
                f"    <tr class='group'><td>{esc(title)}"
                f"<span class='hint'>{esc(sub)}</span></td>"
                + "".join(f"<td class='{'cand' if c in cand else ''}'></td>" for c in cols)
                + "</tr>")

        def metric(label: str, sub: str, r: dict, emphasise: bool = False) -> None:
            vals = {c: fmt(r["values"].get(c, ""), r["numeric"].get(c), score=emphasise)
                    for c in cols}
            best = None
            if emphasise:
                nums = {c: r["numeric"].get(c) for c in cols
                        if r["numeric"].get(c) is not None}
                if len(nums) > 1:
                    top = max(nums.values())
                    leaders = [c for c, v in nums.items() if v == top]
                    # A tie is not a win. Bolding the leftmost of three identical numbers
                    # invents a winner out of column order.
                    best = leaders[0] if len(leaders) == 1 else None
            cells = []
            for c in cols:
                cls = "n cand" if c in cand else "n"
                v = vals[c]
                if best == c:
                    v = f"<b>{v}</b>"
                cells.append(f"<td class='{cls}'>{v}</td>")
            body.append(
                f"    <tr><td>{esc(label)}"
                + (f"<span class='hint'>{esc(sub)}</span>" if sub else "")
                + "</td>" + "".join(cells) + "</tr>")

        # --- 1. the decision ---------------------------------------------------------
        group(*GROUPS[0])
        headline = next((r for r in tab["rows"]
                         if r["label"].lower().startswith("weighted average f1")), None)
        if headline:
            metric("Weighted average F1", tidy(der.get("weighted_average_formula", "")),
                   headline, emphasise=True)
        # Tax-Invoice has three dimensions whose names all begin "Field extraction", so the
        # head alone would print the same bold label three times. Where a head repeats, the
        # full dimension name is used instead.
        f1_rows = [r for r in rows_by_section.get("BUSINESS OUTCOME", [])
                   if r["label"] == "F1-score"]
        heads = [split_label(r["dimension"] or "F1")[0] for r in f1_rows]
        for r, hd in zip(f1_rows, heads):
            lab, sub = split_label(r["dimension"] or "F1")
            if heads.count(hd) > 1:
                lab, sub = tidy(r["dimension"]), ""
            metric(lab, (sub + " · F1") if sub else "F1", r, emphasise=True)

        # --- 2. what ran -------------------------------------------------------------
        what_ran = rows_by_section.get("WHAT RAN", [])
        if what_ran:
            group(*GROUPS[1])
            for r in what_ran:
                metric(tidy(r["label"]), SUBLABEL.get(r["label"], ""), r)

        # --- 3. cost -----------------------------------------------------------------
        cost = rows_by_section.get("LATENCY", []) + rows_by_section.get("TOKENS", [])
        if cost:
            group(*GROUPS[2])
            for r in cost:
                lab, sub = split_label(r["label"])
                metric(lab, SUBLABEL.get(r["label"], sub), r)

        # --- 4. the free text --------------------------------------------------------
        rest = [r for sec, rs in rows_by_section.items() for r in rs
                if sec not in ("", "WHAT RAN", "BUSINESS OUTCOME", "LATENCY", "TOKENS")]
        if rest:
            group(*GROUPS[3])
            for r in rest:
                lab, sub = split_label(r["label"])
                metric(lab, sub, r)

        headers = "".join(
            f"<th class='n{' cand' if c in cand else ''}'>{esc(tidy(c))}"
            f"<span class='hint'>{'our GPU &middot; candidate' if c in cand else 'production today'}</span></th>"
            for c in cols)

        tables.append(f"""
<h2><span class="n">{len(tables) + 1}</span>{esc(name)}</h2>
<p class="lede">{esc(WHAT_IT_IS.get(name, ''))}</p>

<div class="card">
<table class="uc">
  <thead><tr><th>Metric</th>{headers}</tr></thead>
  <tbody>
{chr(10).join(body)}
  </tbody>
</table>
</div>
""")

    # A single footnote carrying the two things that would otherwise mislead a reader.
    degenerate = [n for n, t in d["tabs"].items()
                  if t["derived"]["recall_exactly_one"]["cells"]]
    body_html = "".join(tables)

    return head + "\n" + f"""
<div class="sheet">

<div class="masthead">
  <div>
    <h1>Six use cases, side by side</h1>
    <p class="sub">The incumbent against our internally hosted models &middot; every figure as
    published in <code>{esc(d['source_file'])}</code></p>
  </div>
  <div class="stamp">
    {STAMP}<br>
    {len(d['tabs'])} use cases<br>
    figures parsed, not typed
  </div>
</div>

<div class="note">
  <p><b>How to read these tables.</b> The incumbent column is production as it runs today; the
  tinted columns are what we host. Within a row the larger number is bolded on the score rows
  only &mdash; <b>no winner is named per use case</b>, because several of these benchmarks
  cannot separate the arms and the two sides are not fed the same input (the incumbent hears
  the audio; ours reads a transcript of it).</p>
</div>
{body_html}
<div class="note risk">
  <p><b>Two things that are not visible in the numbers above.</b></p>
  <ul>
    <li><b>Recall and accuracy are not independent measurements on
    {esc(', '.join(degenerate))}.</b> Those confusion matrices are built with FN&nbsp;=&nbsp;0,
    which pins recall at exactly 1.0000 and makes accuracy identical to precision. Only the F1
    and precision columns carry information, which is why this page prints F1 alone.</li>
    <li><b>The two sides are not fed the same thing.</b> The incumbent receives raw audio, or a
    PDF with its text layer; ours receives an ASR transcript, or a 200-DPI image. Every gap in
    these tables is a whole-pipeline gap, not a model-quality gap.</li>
  </ul>
</div>

<p class="foot">
  Figures parsed from <code>{esc(d['source_file'])}</code> by
  <code>scripts/parallel_eval_workbook.py</code> and rendered by
  <code>scripts/usecase_tables.py</code>; no number on this page is typed by hand. The results
  are the parallel evaluation project's and are <b>not reproduced here</b>. For what the
  benchmarks can and cannot measure, see <code>eval-comparison.html</code>.
</p>

</div>
"""


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="usecase_tables")
    ap.add_argument("--check", action="store_true")
    args = ap.parse_args(argv if argv is not None else sys.argv[1:])

    text = build(json.loads(SRC.read_text(encoding="utf-8")))
    if args.check:
        current = OUT.read_text(encoding="utf-8") if OUT.exists() else ""
        ok = current == text
        print(f"{OUT.relative_to(REPO)} "
              + ("is up to date." if ok else "IS STALE -- regenerate it."))
        return 0 if ok else 1
    OUT.write_text(text, encoding="utf-8", newline="\n")
    print(f"wrote {OUT.relative_to(REPO)}  ({len(text):,} bytes)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
