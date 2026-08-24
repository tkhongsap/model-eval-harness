"""Render `docs/reports/usecase-matrix.html` -- ONE wide table, every use case and arm.

WHY ONE TABLE. Six separate tables answer "how did this use case go" six times. One matrix
answers a question none of them can: how the same measure moves across all six at once. Columns
are (use case, arm); rows are the handful of measures that mean the same thing everywhere.

Wide and short is the point, so the table scrolls sideways inside its card and the measure
column is pinned to the left edge. Every figure comes from `usecase-matrix.json`.

Cost is printed only where a rate has been established from a primary source and the arm's
tokens reconcile against the workbook's own total. Anything else prints WHY it is absent -- a
blank cell in a cost row invites the reader to assume zero.

Usage:
    python scripts/usecase_matrix.py            # write
    python scripts/usecase_matrix.py --check    # staleness only
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
SRC = REPO / "docs" / "reports" / "usecase-matrix.json"
SUMMARY = REPO / "docs" / "reports" / "summary.json"
OUT = REPO / "docs" / "reports" / "usecase-matrix.html"
HEAD = REPO / "scripts" / "assets" / "gpu-report-head.html"


def summary_block(s: dict) -> str:
    """The two-column summary that opens the page.

    Six use cases is too much to hold in your head in a meeting, so this collapses them. Every
    row that CANNOT honestly collapse says so in the cell rather than being left out, because a
    summary that quietly drops the rows which do not aggregate is how a reader ends up trusting
    the ones that do not either.
    """
    q, e, g = s["quality"], s["error_basis"], s["where_the_gap_is"]
    sp, c, sel = s["speed"], s["cost"], s["selection_exposure"]
    sens, par = s["sensitivity_excluding_retention"], s["parity_not_wins"]
    without, with_asr = g["without_asr"], g["with_asr"]
    acc, adisc = s["accuracy"], s["accuracy_disclosure"]
    tok = s["tokens"]

    # --- grouped header: Task | F1(2) | Accuracy(2) | Input tok(2) | Output tok(2) | Cost(2) --
    groups = [("", 1), ("F1", 2), ("Accuracy", 2), ("Input tokens", 2), ("Output tokens", 2),
              ("Cost per item", 2)]
    hdr1 = "".join(
        (f"<th class='ml' rowspan='2'>Task<span class='hint'>best internal "
         f"configuration</span></th>") if not name else
        f"<th class='grp' colspan='{span}'>{esc(name)}</th>"
        for name, span in groups)
    sub = ["Gemini", "Internal", "Gemini", "Internal",
           "Gemini", "Internal", "Gemini", "Internal", "Gemini", "Internal"]
    sub_cls = ["inc", "cand", "inc", "cand", "inc", "cand", "inc", "cand", "inc", "cand"]
    hdr2 = "".join(f"<th class='n {cls}'>{lab}</th>" for lab, cls in zip(sub, sub_cls))

    def cost_cell(per_item_usd, unit, model_assumed=False, upper_bound=False) -> str:
        """Cost per ITEM, never a run total and never a sum.

        The unit noun rides in the cell because it differs per row -- a submission, a page and
        a call are not the same thing -- so a bare column of dollars would invite exactly the
        cross-row comparison this table cannot support.
        """
        if per_item_usd is None:
            return "<span class='muted'>no rate</span>"
        return (f"<b>${per_item_usd:,.4f}</b>"
                f"{cost_markers(model_assumed, upper_bound)}"
                f"<span class='hint'>per {esc(unit or 'item')}</span>")

    def task_row(p: dict) -> str:
        cls = " class='emph'" if p["leader"] == "internal" else ""
        return (
            f"    <tr{cls}><td class='ml'>{esc(p['use_case'])}"
            f"<span class='hint'>{esc(p['internal_arm'])}</span></td>"
            f"<td class='n inc'>{p['incumbent_f1']:.4f}</td>"
            f"<td class='n cand'>{p['internal_f1']:.4f}</td>"
            f"<td class='n inc'>{p['accuracy_incumbent']:.4f}</td>"
            f"<td class='n cand'>{p['accuracy_internal']:.4f}</td>"
            f"<td class='n inc'>{p['input_tokens_incumbent']:,}</td>"
            f"<td class='n cand'>{p['input_tokens_internal']:,}</td>"
            f"<td class='n inc'>{p['output_tokens_incumbent']:,}</td>"
            f"<td class='n cand'>{p['output_tokens_internal']:,}</td>"
            f"<td class='n inc'>{cost_cell(p['cost_per_item_incumbent_usd'], p['cost_unit'], p['cost_model_assumed'], p['cost_is_upper_bound'])}</td>"
            f"<td class='n cand'><span class='muted'>not metered</span></td></tr>")

    task_rows = "\n".join(
        task_row(p) for p in sorted(s["per_use_case"], key=lambda p: -p["pct_of_incumbent"]))

    # The All-six cost cell deliberately carries NO number. The six per-item figures are
    # denominated in three different units (submission / page / call); a mean of them divides
    # dollars by submissions-plus-pages-plus-calls, which is not a quantity, and a sum of the
    # run totals answers a question nobody asked once the column is per-item.
    all_six_row = (
        "    <tr class='emph'><td class='ml'>All six<span class='hint'>one task, one vote for "
        "F1 and Accuracy; sum for tokens</span></td>"
        f"<td class='n inc'><b>{q['incumbent']:.4f}</b></td>"
        # Both constructions of the ratio stay here. They differ by about a tenth of a point,
        # and a reader who works out the other one unaided should not conclude it was hidden.
        f"<td class='n cand'><b>{q['internal']:.4f}</b>"
        f"<span class='hint'>{q['pct_ratio_of_means']}% of production"
        f"<br>{q['pct_mean_of_ratios']}% as mean of ratios</span></td>"
        f"<td class='n inc'><b>{acc['incumbent']:.4f}</b></td>"
        f"<td class='n cand'><b>{acc['internal']:.4f}</b>"
        f"<span class='hint'>{acc['pct_ratio_of_means']}% of production"
        f"<br>{acc['pct_mean_of_ratios']}% as mean of ratios</span></td>"
        f"<td class='n inc'><b>{tok['incumbent']['input']:,}</b></td>"
        f"<td class='n cand'><b>{tok['internal']['input']:,}</b></td>"
        f"<td class='n inc'><b>{tok['incumbent']['output']:,}</b></td>"
        f"<td class='n cand'><b>{tok['internal']['output']:,}</b></td>"
        f"<td class='n inc'><span class='muted'>not aggregated<br>"
        f"range ${c['per_item_range']['min']:,.4f}&ndash;${c['per_item_range']['max']:,.4f}"
        f"</span></td>"
        "<td class='n cand'><span class='muted'>not metered</span></td></tr>")

    return f"""
<h2><span class="n">1</span>Internal models against production, six tasks</h2>

<div class="strip">
  <div class="tile win">
    <div class="big">{q['pct_ratio_of_means']}%</div>
    <div class="cap">of production quality<br>across all six tasks</div>
  </div>
  <div class="tile win">
    <div class="big">{without['pct_of_incumbent']}%</div>
    <div class="cap">on the {len(without['use_cases'])} tasks with <b>no speech stage</b><br>
    where the model reads the source directly</div>
  </div>
  <div class="tile">
    <div class="big">{len(par['tasks'])} of 6</div>
    <div class="cap">tasks at parity or ahead<br>{esc(', '.join(t.replace('Sentiment ','') for t in par['tasks']))}</div>
  </div>
  <div class="tile risk">
    <div class="big">{sp['range'][0]}&ndash;{sp['widest_known_ratio']}&times;</div>
    <div class="cap">slower per item<br>the number to be ready for</div>
  </div>
</div>

<div class="card">
<table class="mx">
  <thead>
    <tr>{hdr1}</tr>
    <tr>{hdr2}</tr>
  </thead>
  <tbody>
{task_rows}
{all_six_row}
  </tbody>
</table>
</div>
<p class="lede">Tokens are whole-round totals, including thinking tokens on the output side.
Cost is the average for <b>one item</b> &mdash; one submission, one page or one call, named in
each cell &mdash; not the run total, and the six are not addable because those units differ.</p>

<div class="note">
  <p><b>Accuracy is not independent information on most tasks.</b> {esc(adisc['note'])}</p>
  <ul>
{chr(10).join(f"    <li><b>{esc(t)}</b> &mdash; {esc(v['summary'])}</li>"
             for t, v in adisc['per_task'].items())}
  </ul>
</div>

<div class="note">
  <p><b>The gap is concentrated in one stage, and it is not the labelling model.</b> On the
  {len(without['use_cases'])} tasks where the internal model reads the source directly
  &mdash; {esc(' and '.join(without['use_cases']))} &mdash; it reaches
  <b>{without['pct_of_incumbent']}%</b> of production. On the {len(with_asr['use_cases'])} that
  put a <b>speech-to-text stage in front of it</b>, {with_asr['pct_of_incumbent']}%. The
  workbook corroborates the mechanism from the inside: the internal transcript on Retention
  scores a mean character error rate <i>above 1.0</i>, and Sentiment QA's transcript quality
  tracks which speech model ran rather than which prompt.</p>
  <p><b>{esc(g['caveat'])}</b></p>
</div>

<div class="note risk">
  <p><b>The four questions this page has to survive.</b></p>
  <ul>
    <li><b>&ldquo;97% of an F1 is not 97% as good.&rdquo;</b> Correct. On an error basis the
    same six results read as <b>{e['internal_errors_relative']}&times; the errors</b>
    ({e['incumbent_mean_error']:.4f} against {e['internal_mean_error']:.4f} mean 1&minus;F1).
    Both figures are true and both belong in the room.</li>
    <li><b>&ldquo;You picked the best model per task.&rdquo;</b> Partly &mdash; and the exposure
    is small. On {len(sel['tasks_with_only_one_internal_arm'])} of the six tasks only one
    internal arm ran, so nothing was selected. On two more the candidates are the same model
    under different prompts. Only {esc(sel['only_genuine_two_model_pick'])}. Against the mean of
    every internal arm ({sel['mean_of_all_internal_arms']:.4f}) this selection flatters by
    {sel['best_per_task_flatters_by']:.4f}.</li>
    <li><b>&ldquo;How much slower?&rdquo;</b> {sp['range'][0]}&times; to
    <b>{sp['widest_known_ratio']}&times;</b> per item. {esc(sp['note'])}</li>
    <li><b>&ldquo;What do we save?&rdquo;</b> {esc(c['break_even_is_not_computable'])}</li>
  </ul>
</div>

<div class="note">
  <p><b>Two tasks are shown as parity rather than as wins</b>
  ({esc(', '.join(par['tasks']))}). {esc(par['why'])}</p>
  <p><b>Sentiment Retention is the widest gap at
  {[p['pct_of_incumbent'] for p in s['per_use_case'] if p['use_case'] == 'Sentiment Retention'][0]:.1f}%
  and it stays in the headline.</b> Excluding it would read
  {sens['pct_of_incumbent']}%. {esc(sens['why_it_is_not_the_headline'])}</p>
  <p><b>Cost.</b> The incumbent's side of this evaluation cost
  <b>${c['incumbent_usd']:,.2f}</b> at list rates. {esc(c['internal_note'])}</p>
</div>

<div class="note risk">
  <p><b>What this page must not be read as saying.</b></p>
  <ul>
{chr(10).join(f"    <li>{esc(x)}</li>" for x in s['must_not_claim'])}
  </ul>
  <p class="hint">Internal column is the best-scoring internal configuration per task, named in
  each row. The models are Qwen3.8-27B-FP8 throughout, with Gemma-4-12B as a second candidate
  on two tasks; the four speech tasks run a separate transcription model in front. Figures are
  the parallel evaluation project's, parsed from their workbook, not reproduced here.</p>
</div>
"""


def cost_markers(model_assumed: bool, upper_bound: bool) -> str:
    """The star/dagger suffixes for a cost figure, identical wherever a cost is shown.

    One function so the summary table (section 1) and the detail table (section 2) can never
    assign a different meaning to the same marker, even though they read it off differently
    shaped dicts (summary.json's per-task records vs usecase-matrix.json's per-arm columns).
    """
    star = "<sup>*</sup>" if model_assumed else ""
    dagger = "<sup>&dagger;</sup>" if upper_bound else ""
    return star + dagger


def esc(s) -> str:
    out = str(s).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
    return "".join(c if ord(c) < 127 else f"&#{ord(c)};" for c in out)


def n(v) -> str:
    return f"{v:,}" if isinstance(v, int) else "&mdash;"


def f3(v) -> str:
    return f"{v:.4f}" if isinstance(v, (int, float)) else "&mdash;"


def secs(v) -> str:
    if not isinstance(v, (int, float)):
        return "&mdash;"
    return f"{v:,.1f}" if v < 1000 else f"{v:,.0f}"


def shown_columns(all_cols: list[dict]) -> tuple[list[dict], list[str]]:
    """Two columns per use case: the incumbent, and the best-scoring internal arm.

    The workbook ran up to three internal configurations on some tasks, which made a 16-column
    table nobody reads to the end. The detail table now matches the first page -- same
    selection, same arms -- so the two cannot tell different stories.

    Nothing is deleted: `usecase-matrix.json` keeps every arm, the six-table page shows them
    all, and the dropped arms are named in the caption so the narrowing is visible rather than
    silent. That matters most on Tax-Invoice, where the arm being dropped is Gemma-4-12B at
    0.6205 -- evidence that the choice of internal model is not incidental.
    """
    by_tab: dict = {}
    for c in all_cols:
        by_tab.setdefault(c["use_case"], []).append(c)

    keep, dropped = [], []
    for tab, cs in by_tab.items():
        inc = [c for c in cs if c["side"] == "incumbent"]
        internal = [c for c in cs if c["side"] == "internal"]
        best = max(internal, key=lambda c: c["headline_f1"]) if internal else None
        keep.extend(inc)
        if best:
            keep.append(best)
        dropped.extend(f"{tab}: {c['arm']} ({c['headline_f1']:.4f})"
                       for c in internal if c is not best)
    return keep, dropped


def build(d: dict) -> str:
    cols, dropped = shown_columns(d["columns"])
    summary_html = (summary_block(json.loads(SUMMARY.read_text(encoding="utf-8")))
                    if SUMMARY.is_file() else "")
    head = HEAD.read_text(encoding="utf-8").rstrip("\n")
    head = head.replace("<title>Internal GPU Model Performance</title>",
                        "<title>All Use Cases, One Matrix</title>", 1)

    # --- two header rows: use case (spanning), then arm ------------------------------
    groups: list[tuple[str, int]] = []
    for c in cols:
        if groups and groups[-1][0] == c["use_case"]:
            groups[-1] = (groups[-1][0], groups[-1][1] + 1)
        else:
            groups.append((c["use_case"], 1))

    hdr1 = "".join(
        f"<th class='grp' colspan='{k}'>{esc(name)}</th>" for name, k in groups)
    hdr2 = "".join(
        f"<th class='n{' cand' if c['side'] == 'internal' else ' inc'}'>"
        f"{esc(c['arm'].replace('QWEN3.8-27B-FP8', 'Qwen3.8-27B').replace('GEMMA-4-12B-IT', 'Gemma-4-12B').replace('GEMINI-2.5-FLASH', 'Gemini 2.5 Flash').replace('GEMINI-3.1-PRO-PREVIEW', 'Gemini 3.1 Pro').replace('GEMINI', 'Gemini'))}"
        f"<span class='hint'>{'ours' if c['side'] == 'internal' else 'incumbent'}</span></th>"
        for c in cols)

    def row(label: str, sub: str, cell, *, emph: bool = False) -> str:
        tds = []
        for c in cols:
            cls = "n cand" if c["side"] == "internal" else "n inc"
            tds.append(f"<td class='{cls}'>{cell(c)}</td>")
        return (f"    <tr{' class=\"emph\"' if emph else ''}><td class='ml'>{esc(label)}"
                + (f"<span class='hint'>{esc(sub)}</span>" if sub else "")
                + "</td>" + "".join(tds) + "</tr>")

    def per_item_cell(c: dict) -> str:
        """The page's only cost figure, so everything it depends on rides with it.

        The unit noun is in the cell because it differs per column -- a page, a submission and
        a call are not the same thing, and a bare row of dollars invites subtraction across
        them. The star marks a figure resting on an assumed model; the dagger marks one priced
        at the audio rate, where the floor is roughly half.
        """
        v = c.get("cost_per_item_usd")
        if v is None:
            return ("<span class='muted'>not metered</span>" if c["side"] == "internal"
                    else "<span class='muted'>no rate</span>")
        marks = cost_markers(bool(c.get("model_assumed")), bool(c.get("cost_per_item_usd_floor")))
        return (f"<b>${v:,.4f}</b>{marks}"
                f"<span class='hint'>per {esc(c.get('cost_unit') or 'item')}</span>")

    body = "\n".join([
        row("Weighted average F1", "the published headline",
            lambda c: f3(c["headline_f1"]), emph=True),
        row("Items scored", "calls, model calls or pages", lambda c: n(c["items_scored"])),
        row("Input tokens", "whole round", lambda c: n(c["input_tokens"])),
        # Thinking tokens are folded in here rather than given their own row. They bill as
        # OUTPUT, so this is where they belong; putting them in the input row would misstate
        # both counts, and a row of their own was almost all dashes because only one run
        # reports any.
        row("Output tokens", "including thinking tokens where reported",
            lambda c: n(c["billable_output_tokens"])),
        row("Total tokens", "input + output",
            lambda c: n(c["total_tokens_computed"])),
        # Where a timing is absent the workbook says WHY, and its words go in the cell. A bare
        # dash reads as "we forgot to measure"; "cannot meansure" and "resumed over several
        # days" say the run could not be timed, which is itself a finding about the pipeline.
        row("Latency", "seconds per item",
            lambda c: (secs(c["latency_s"]) if c["latency_s"] is not None
                       else f"<span class='muted'>{esc(c.get('latency_raw') or 'not published')}"
                            "</span>")),
        row("Whole round", "seconds",
            lambda c: (secs(c["round_s"]) if c["round_s"] is not None
                       else f"<span class='muted'>{esc(c.get('round_raw') or 'not published')}"
                            "</span>")),
        row("Cost per item scored",
            "USD - each column divided by its own unit, which is not the same unit. "
            "Not comparable across columns.",
            per_item_cell, emph=True),
    ])

    rec = d["reconciliation"]
    priced = [c for c in cols if c["cost_usd"] is not None]
    total = sum(c["cost_usd"] for c in priced)
    floor = sum(c["cost_usd_floor"] if c["cost_usd_floor"] is not None else c["cost_usd"]
                for c in priced)
    audio_runs = [c for c in priced if c["input_rate_kind"] == "audio"]
    assumed = [c for c in priced if c.get("model_assumed")]
    uncosted = [c for c in cols if c["side"] == "incumbent" and c not in priced]

    rate_lines = []
    seen = set()
    for c in cols:
        r = c.get("rate_used")
        if not r or c["model"] in seen:
            continue
        seen.add(c["model"])
        # Only quote the audio rate for a model that was actually fed audio. The document
        # model carries one too, and printing a rate no figure on this page used invites a
        # reader to check the arithmetic against the wrong number.
        audio = (r.get("input_audio_usd_per_1m")
                 if any(x["model"] == c["model"] and x["input_rate_kind"] == "audio"
                        for x in priced) else None)
        slug = r.get("openrouter_slug") or c["model"]
        rate_lines.append(
            f"    <li><code>{esc(slug)}</code> &mdash; "
            f"${r['input_usd_per_1m']:,.2f} per 1M text input, "
            + (f"<b>${audio:,.2f} per 1M audio input</b>, " if audio else "")
            + f"${r['output_usd_per_1m']:,.2f} per 1M output "
            f"({esc(r.get('mode') or 'unknown')} tier)</li>")

    if priced:
        cost_note = f"""
<div class="note">
  <p><b>What the incumbent's runs cost: ${total:,.2f} across all
  {len(priced)} of them.</b> Each is
  <code>input&nbsp;&times;&nbsp;input_rate&nbsp;+&nbsp;(output&nbsp;+&nbsp;thinking)&nbsp;&times;&nbsp;output_rate</code>,
  at <b>OpenRouter standard-tier rates</b> read from
  <code>openrouter.ai/api/v1/models</code> on 2026-08-24:</p>
  <ul>
{chr(10).join(rate_lines)}
  </ul>
  <p><b>These runs went through Vertex AI batch, not OpenRouter</b>, so this answers what they
  would cost on OpenRouter at the standard tier rather than what was invoiced. OpenRouter also
  carries <code>:batch</code> slugs at roughly half, which would put the same six runs near
  <b>${total / 2:,.2f}</b>; its own documentation hedges batch as &ldquo;typically billed at
  50%&rdquo; and routes it through a separate endpoint, so the standard tier is the figure
  quoted here.</p>
  <p><b>What each run cost in total</b>, since the table now shows only the per-item average:
  {', '.join(f"{esc(c['use_case'])} <b>${c['cost_usd']:,.2f}</b> over {c['cost_denominator']:,} {esc(c['cost_unit'])}s" for c in priced)}.</p>
  <p><sup>&dagger;</sup><b>The {len(audio_runs)} audio use cases are priced at the audio input
  rate</b>, which OpenRouter charges at $1.00 per 1M against $0.30 for text &mdash; 3.3&times;.
  Those runs send the call recording straight into the model, so the audio is what their input
  total is mostly made of. The system prompt inside that total is text and cheaper, and the
  workbook publishes one input figure that cannot be split &mdash; so <b>those four per-item
  figures are upper bounds</b>. Pricing every input token as text instead gives
  {', '.join(f"{esc(c['use_case'].replace('Sentiment ', ''))} ${c['cost_per_item_usd_floor']:,.4f}" for c in audio_runs)}
  per call &mdash; roughly half. The truth sits between, nearer the printed figure.</p>
  <p><b>Thinking tokens are inside the billable output, not the printed one.</b> On Sentiment
  MNP the incumbent prints 176,626 output tokens and 227,974 thinking tokens separately;
  costing the printed line alone would understate that run's billable output by 129%.
  OpenRouter's <code>internal_reasoning</code> rate equals its completion rate on both models
  here, so those tokens cost exactly what ordinary output costs.</p>
  <p><b>This is a rate-card estimate, not a bill.</b> OpenRouter discounts cached input heavily
  &mdash; $0.03 per 1M against $0.30 on 2.5&nbsp;Flash &mdash; and cached tokens sit inside the
  reported input total where a run aggregate cannot separate them, so the input side is an
  upper bound on that count too. Tax invoice extraction is priced on the cheaper &le;200K-token
  band, which its mean of
  {next((f"{c['tokens_per_request']:,}" for c in cols if c.get('tokens_per_request') and c.get('under_long_context_tier')), '?')}
  tokens per request supports &mdash; that is a mean, so a few long requests could still have
  repriced at double. That override is in OpenRouter's JSON only; its model page does not show
  it.</p>
  {(
    '<p><sup>*</sup><b>RTR-Fraud rests on an assumption.</b> That tab names no model for its '
    'incumbent column &mdash; it has a runtime row but no model row, unlike every other tab. '
    'It is costed as <code>google/gemini-2.5-flash</code> because that is the model named in four of '
    'the five other tabs. If it actually ran a Pro-tier model the same tokens cost 6.2&times; '
    'more &mdash; $0.0077 per submission rather than $0.0012, and $1.53 for the run rather '
    'than $0.25.</p>'
  ) if assumed else ''}
</div>"""
    else:
        cost_note = """
<div class="note risk">
  <p><b>No cost is printed because no rate has been established from a primary source.</b>
  The cost row says &ldquo;no rate&rdquo; rather than leaving a blank a reader would take for
  zero. Establish the rates in <code>docs/reports/gemini-pricing.json</code> and this page fills
  itself in.</p>
</div>"""

    return head + "\n" + f"""
<div class="sheet wide">

<div class="masthead">
  <div>
    <h1>All use cases, one matrix</h1>
    <p class="sub">Every measure that means the same thing across all six &middot; columns are
    use case &times; model &middot; figures parsed from
    <code>{esc(d['source_file'])}</code></p>
  </div>
  <div class="stamp">
    {esc(d['generated'])}<br>
    {len(cols)} columns<br>
    tokens reconciled {rec['passed']}/{rec['checked']}
  </div>
</div>

{summary_html}
<h2><span class="n">2</span>The same measures, use case by use case</h2>
<p class="lede">Where the summary above comes from &mdash; the same arms, two columns per task.
{('Other internal configurations ran and are not shown here: '
  + esc('; '.join(dropped)) + '. All of them appear in the six-table report.')
 if dropped else ''}</p>

<div class="card">
<table class="mx">
  <thead>
    <tr><th class="ml" rowspan="2">Measure</th>{hdr1}</tr>
    <tr>{hdr2}</tr>
  </thead>
  <tbody>
{body}
  </tbody>
</table>
</div>
{cost_note}
<div class="note risk">
  <p><b>Three things that would mislead if read straight off this table.</b></p>
  <ul>
    <li><b>Token counts are not comparable between the two sides.</b> The incumbent's input
    includes the audio itself, charged as tokens; ours is a transcript, so its input is text
    for a second model. Fewer tokens on our side is a different pipeline shape, not efficiency.</li>
    <li><b>&ldquo;Not metered&rdquo; is not free.</b> Self-hosted runs produce no invoice, but
    the GPU hours behind them are real and are not in this table.</li>
    <li><b>The weighted average F1 is comparable within a column pair, not across use cases.</b>
    Each use case scores different dimensions against different label spaces, and several of
    these benchmarks cannot separate the arms at all.</li>
    <li><b>Neither is the cost row, and for a blunter reason: the units differ.</b> RTR-Fraud's
    figure is per <b>submission</b> &mdash; one RTR_Code folder holding up to three photos, so
    it is <i>not</i> a cost per image and no per-image figure can be derived, because the
    corpus never publishes how many photos each folder holds. Tax-Invoice's is per <b>page</b>,
    and its 147 pages sit inside 32 files, so a cost per document is 4.6&times; higher. The
    four sentiment figures are per call.</li>
  </ul>
</div>

<p class="foot">
  Figures parsed from <code>{esc(d['source_file'])}</code> by
  <code>scripts/parallel_eval_workbook.py</code>, costed by
  <code>scripts/usecase_matrix_figures.py</code> and rendered by
  <code>scripts/usecase_matrix.py</code>; no number on this page is typed by hand. Token totals
  are reconciled against the workbook's own &ldquo;Total tokens&rdquo; row where it publishes
  one &mdash; {rec['passed']} of {rec['checked']} exact. Results are the parallel evaluation
  project's and are <b>not reproduced here</b>.
</p>

</div>
"""


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="usecase_matrix")
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
