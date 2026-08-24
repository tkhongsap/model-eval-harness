"""Render `docs/reports/eval-comparison.html` from `docs/reports/eval-comparison.json`.

WHAT THIS REPORT ANSWERS, and how it differs from the other two. `gpu-performance.html` answers
"what did the models score". This one answers the question underneath it: **how does production
actually evaluate them, and is that comparison fair.** It is built from production's own
evaluation code in `production-reference/`, surveyed area by area and adversarially verified,
joined to the published results already parsed in `parallel-eval-workbook.json`.

Every figure is read from the JSON. The prose lives here because the prose is an argument about
the evidence, and a diff should show when the argument changes. `--check` fails if the committed
HTML is stale.

Usage:
    python scripts/eval_comparison_report.py            # write
    python scripts/eval_comparison_report.py --check    # staleness only
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
SRC = REPO / "docs" / "reports" / "eval-comparison.json"
OUT = REPO / "docs" / "reports" / "eval-comparison.html"
HEAD = REPO / "scripts" / "assets" / "gpu-report-head.html"

SEVERITY_RANK = {"metric-cannot-fail": 0, "silently-wrong": 1, "cosmetic": 2}
SEVERITY_LABEL = {
    "metric-cannot-fail": "cannot fail",
    "silently-wrong": "silently wrong",
    "cosmetic": "cosmetic",
}
VERDICT_TONE = {
    "fair": "", "fair-with-caveats": "", "not-comparable": " risk", "unknown": " risk",
}


def esc(s) -> str:
    """Escape markup AND every non-ASCII character to a numeric reference.

    The survey text is full of em-dashes. A page that carries them as raw UTF-8 renders as
    mojibake wherever the charset is not declared or is overridden, which is exactly what
    happened the first time this report was rendered. Emitting pure ASCII makes the file
    correct under any encoding, whatever wraps it.
    """
    out = str(s).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
    return "".join(c if ord(c) < 127 else f"&#{ord(c)};" for c in out)


def code(s) -> str:
    """A file:line reference, rendered as code."""
    return f"<code>{esc(s)}</code>" if s else "&mdash;"


def split_id(s: str, limit: int = 92) -> tuple[str, str]:
    """Reduce a survey's model/stack string to the identifier chain, plus the leftover note.

    The survey packs the identifier, its citation and a caveat into one string. Cutting at the
    first bracket is WRONG for the internal side: "qwen3-asr-1.7b (baseline) or
    typhoon-whisper-large-v3 (...) -> qwen3.8-27b-fp8" would print as "qwen3-asr-1.7b" and
    silently drop the labelling model, which is the half a reader most needs. So parenthetical
    asides are removed and the arrow chain is preserved; only a trailing clause after ';' or a
    dash is demoted to the note.
    """
    s = re.sub(r"\s*\([^()]*\)", "", str(s)).strip()
    s = re.sub(r"\s+", " ", s)
    note = ""
    for sep in (";", " — ", " -- "):
        i = s.find(sep)
        if 0 < i:
            s, note = s[:i].strip(), s[i:].lstrip("; —-").strip()
            break
    if len(s) > limit:
        s, note = s[:limit].rstrip() + "…", (s[limit:].strip() + " " + note).strip()
    return s, note


def clip(s, n: int = 190) -> str:
    s = str(s or "").strip()
    return s if len(s) <= n else s[: n - 1].rstrip() + "…"


def build(d: dict) -> str:
    areas = d["areas"]
    cross = d.get("cross_cutting", [])
    fair = d["fairness_verdict"]
    mustnot = d.get("what_the_report_must_not_claim", [])
    results = d.get("results_by_task", {})

    defects = [(a, x) for a in areas for x in a.get("defects", [])]
    defects.sort(key=lambda p: SEVERITY_RANK.get(p[1].get("severity"), 9))
    cannot_fail = [p for p in defects if p[1].get("severity") == "metric-cannot-fail"]

    same_scorer = [a.get("same_scorer_both_sides", "unknown") for a in areas]
    n_same = same_scorer.count("yes")

    head = HEAD.read_text(encoding="utf-8").rstrip("\n")
    head = head.replace("<title>Internal GPU Model Performance</title>",
                        "<title>How Production Evaluates These Models</title>", 1)

    # ---- what runs on each side ------------------------------------------------------
    def run_row(a: dict) -> str:
        inc_id, _ = split_id(a["incumbent_model"])
        int_id, _ = split_id(a["internal_stack"])
        same = a.get("same_scorer_both_sides", "unknown")
        same_cell = (f"<span class='up'>yes</span>" if same == "yes"
                     else f"<span class='dn'>{esc(same)}</span>")
        return (
            f"    <tr><td>{esc(a['label'])}"
            f"<span class='hint'>{esc(a['area'])} &middot; {a['dimensions_scored']} dimension"
            f"{'s' if a['dimensions_scored'] != 1 else ''} &middot; "
            f"confidence {esc(a.get('confidence','?'))}</span></td>"
            f"<td><b>{esc(inc_id)}</b>"
            f"<span class='hint'>{esc(clip(a.get('incumbent_input','')))}</span></td>"
            f"<td><b>{esc(int_id)}</b>"
            f"<span class='hint'>{esc(clip(a.get('internal_input','')))}</span></td>"
            f"<td>{same_cell}</td></tr>")

    run_rows = "\n".join(run_row(a) for a in areas)

    # ---- results, joined from the workbook -------------------------------------------
    if results:
        res_rows = "\n".join(
            "    <tr" + (" class='ours'" if r.get("winner") == "internal" else "") + ">"
            f"<td>{esc(name)}</td>"
            f"<td class='n'>{r['internal']:.5f}</td><td class='n'>{r['incumbent']:.5f}</td>"
            f"<td class='n'>{r['margin']:+.5f}</td>"
            f"<td class='n'>{r.get('slowdown_x','&mdash;')}&times;</td></tr>"
            for name, r in results.items())
        results_block = f"""
<h2><span class="n">2</span>What they scored</h2>

<div class="card">
<table>
  <thead><tr><th>Task</th><th class="n">Internal</th><th class="n">Incumbent</th>
    <th class="n">Margin</th><th class="n">Our latency</th></tr></thead>
  <tbody>
{res_rows}
  </tbody>
</table>
</div>

<div class="note">
  <p>Results as published, from the same workbook, parsed by
  <code>scripts/parallel_eval_workbook.py</code>. They are here so the methodology above and the
  numbers can be read together &mdash; <b>section 3 is why several of these columns carry less
  information than they appear to.</b></p>
</div>
"""
    else:
        results_block = ""

    # ---- defects ---------------------------------------------------------------------
    # The survey writes paragraphs; a table row is not a paragraph. Clipping happens HERE and
    # not in the JSON, so the full text stays available in eval-comparison.json for anyone who
    # wants the whole finding.
    defect_rows = "\n".join(
        f"    <tr><td>{esc(a['label'])}</td>"
        f"<td>{esc(clip(x['what'], 230))}"
        f"<span class='hint'>{esc(clip(x['consequence'], 190))}</span></td>"
        f"<td>{code(clip(x.get('file_line'), 110))}</td>"
        f"<td><span class='pill down'>"
        f"{esc(SEVERITY_LABEL.get(x.get('severity'), x.get('severity','?')))}</span></td></tr>"
        for a, x in defects) or (
        "    <tr><td colspan='4'>No scorer defect survived verification.</td></tr>")

    cross_items = "\n".join(
        f"    <li><b>{esc(clip(x['finding'], 200))}</b> &mdash; "
        f"{esc(clip(x['why_it_matters'], 300))}"
        f"<span class='hint'>affects: {esc(', '.join(x.get('areas_affected', [])))}</span></li>"
        for x in cross)

    mustnot_items = "\n".join(f"    <li>{esc(clip(x, 320))}</li>" for x in mustnot)

    fairness_items = "\n".join(
        f"    <li><b>{esc(a['label'])}</b> &mdash; "
        f"{esc(clip('; '.join(a.get('fairness_notes', [])), 300))}</li>"
        for a in areas if a.get("fairness_notes"))

    tone = VERDICT_TONE.get(fair["verdict"], "")

    body = f"""
<div class="sheet">

<div class="masthead">
  <div>
    <h1>How production evaluates these models</h1>
    <p class="sub">The incumbent against our internal stack &middot; read from production's own
    evaluation code, area by area &middot; <b>not a re-run of their results</b></p>
  </div>
  <div class="stamp">
    {esc(d['generated'])}<br>
    {len(areas)} task areas surveyed<br>
    verdict: {esc(fair['verdict'])}
  </div>
</div>

<div class="strip">
  <div class="tile">
    <div class="big">{len(areas)}</div>
    <div class="cap">task areas read on <b>both</b> sides<br>incumbent and internal, in the same
    codebase</div>
  </div>
  <div class="tile risk">
    <div class="big">{len(cannot_fail)}</div>
    <div class="cap">metrics that <b>cannot fail</b><br>of {len(defects)} scoring defects
    confirmed</div>
  </div>
  <div class="tile{' risk' if n_same < len(areas) else ''}">
    <div class="big">{n_same} of {len(areas)}</div>
    <div class="cap">areas where <b>both sides run through<br>the same scorer</b></div>
  </div>
  <div class="tile{tone}">
    <div class="big" style="font-size:14px;line-height:1.35">{esc(fair['verdict'])}</div>
    <div class="cap">on whether the comparison is fair<br>see section 4</div>
  </div>
</div>

<h2><span class="n">1</span>What actually runs on each side</h2>

<div class="card">
<table class="fixed">
  <colgroup><col style="width:19%"><col style="width:33%"><col style="width:33%">
    <col style="width:15%"></colgroup>
  <thead><tr>
    <th>Task area</th><th>Incumbent<span class="hint">model &middot; what it is fed</span></th>
    <th>Internal<span class="hint">stack &middot; what it is fed</span></th>
    <th>Same scorer<br>both sides?</th>
  </tr></thead>
  <tbody>
{run_rows}
  </tbody>
</table>
</div>

<div class="note">
  <p>Every row was read out of <code>production-reference/</code> by an agent that cited
  file:line for each claim, then attacked by a second agent whose instruction was to
  <b>refute</b> it. Corrections from that pass are already applied; the confidence column is
  lowered where the adversary refuted more than two claims.</p>
</div>
{results_block}
<h2><span class="n">3</span>What the scoring can and cannot measure</h2>

<div class="card">
<table class="fixed">
  <colgroup><col style="width:14%"><col style="width:47%"><col style="width:27%">
    <col style="width:12%"></colgroup>
  <thead><tr><th>Area</th><th>Defect<span class="hint">and what it does to the number</span></th>
    <th>Where</th><th>Severity</th></tr></thead>
  <tbody>
{defect_rows}
  </tbody>
</table>
</div>

<div class="note risk">
  <p><b>&ldquo;Cannot fail&rdquo; is the category that matters.</b> A confusion matrix built with
  FN&nbsp;=&nbsp;0 pins recall at 1.0000 and makes accuracy identical to precision, so four
  reported metrics carry <b>one</b> measurement. Against a threshold, that indicator can never
  trip &mdash; the gate is decorative. This is not a criticism of the models; it is a statement
  about what the published numbers are able to tell anyone.</p>
</div>

<h2><span class="n">4</span>Is the comparison fair?</h2>

<div class="note{tone}">
  <p><b>Verdict: {esc(fair['verdict'])}.</b> {esc(clip(fair['reasoning'], 1500))}</p>
  <p><b>Biggest asymmetry.</b>
  {esc(clip(fair.get('biggest_asymmetry', 'none identified'), 500))}</p>
</div>

<div class="note">
  <p><b>Per-area asymmetries found:</b></p>
  <ul>
{fairness_items or '    <li>None recorded.</li>'}
  </ul>
</div>

<h2><span class="n">5</span>What only shows up across areas</h2>

<div class="note">
  <ul>
{cross_items or '    <li>No cross-cutting finding survived verification.</li>'}
  </ul>
</div>

<h2><span class="n">6</span>What a decision built on this must not claim</h2>

<div class="note risk">
  <ul>
{mustnot_items or '    <li>Nothing recorded.</li>'}
  </ul>
</div>

<p class="foot">
  Methodology surveyed from <code>production-reference/ai-local-eval-sentiment_project_v2</code>
  by {len(areas)} independent agents, each adversarially verified by a second, and consolidated
  with the adversary's corrections taking precedence. Results joined from
  <code>docs/reports/parallel-eval-workbook.json</code>. This page is rendered by
  <code>scripts/eval_comparison_report.py</code>; no figure on it is typed by hand. <b>Their
  results are not reproduced here</b> &mdash; this describes and audits their method, it does
  not re-run it.
</p>

</div>
"""
    return head + "\n" + body


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="eval_comparison_report")
    ap.add_argument("--check", action="store_true")
    args = ap.parse_args(argv if argv is not None else sys.argv[1:])

    if not SRC.is_file():
        print(f"{SRC.relative_to(REPO)} does not exist -- nothing to render.")
        return 1
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
