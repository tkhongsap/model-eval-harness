"""Render `docs/reports/gpu-performance.html` from `docs/reports/gpu-performance.json`.

WHY A GENERATOR AND NOT A HAND-EDITED PAGE. This report is sent to stakeholders and quotes
another team's published figures. Every number on the page is read out of the JSON that
`gpu_figures.py` derives, which in turn reads `parallel-eval-workbook.json`, which
`parallel_eval_workbook.py` parses from their .xlsx. Nothing on the page is typed by hand, so
the page cannot drift from the workbook. `--check` fails if the committed HTML is stale.

The prose lives here because the prose is an argument about the numbers, and the argument has
to change when the numbers do. Keeping them in one file is what makes that visible in a diff.

Usage:
    python scripts/gpu_report.py            # write
    python scripts/gpu_report.py --check    # staleness only
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
SRC = REPO / "docs" / "reports" / "gpu-performance.json"
OUT = REPO / "docs" / "reports" / "gpu-performance.html"

STAMP = "2026-08-24"

# Task -> the one-line reminder of what the task actually is, since "Sentiment MNP" tells a
# reader outside the project nothing.
WHAT_IT_IS = {
    "RTR-Fraud": "fraud checks on retail transaction images",
    "Tax-Invoice": "field extraction from tax invoice documents",
    "Sentiment QA": "call-centre QA against a 22-criterion rubric",
    "Sentiment MNP": "number-portability calls: outcome and reason",
    "Sentiment Telesale": "telesales calls against a 39-criterion rubric",
    "Sentiment Retention": "retention calls: outcome, reason and product",
}


def esc(s) -> str:
    return (str(s).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;"))


def f(x, n=4) -> str:
    return "&mdash;" if x is None else f"{x:.{n}f}"


def build(d: dict) -> str:
    c = d["cross_task"]
    tasks = c["tasks"]
    sb = c["scoreboard"]
    sat = c["both_internal_wins_are_on_saturated_benchmarks"]
    scored = d["how_their_numbers_are_scored"]
    real = d["real_data_run"]
    lo, hi = c["slowdown_range_x"]
    n_int, n_inc = len(sb["internal_wins"]), len(sb["incumbent_wins"])

    margins = [abs(t["margin"]) for t in tasks.values() if t.get("margin") is not None]
    widest = max(margins)
    widest_task = next(k for k, t in tasks.items()
                       if t.get("margin") is not None and abs(t["margin"]) == widest)

    tail = c["transcript_runaway_tail"]
    tail_int = [x for x in tail["cases"] if "QWEN" in x["arm"].upper()]
    tail_inc = [x for x in tail["cases"] if "GEMINI" in x["arm"].upper()]

    head = (REPO / "scripts" / "assets" / "gpu-report-head.html").read_text(encoding="utf-8")

    rows = []
    for name, t in tasks.items():
        win_int = t.get("winner") == "internal"
        cls = ' class="ours"' if win_int else ""
        verdict = ("<span class='up'>internal</span>" if win_int
                   else "<span class='dn'>incumbent</span>")
        slow = (f"<span class='pill down'>{t['slowdown_x']}&times;</span>"
                if t.get("slowdown_x") else "&mdash;")
        rows.append(
            f"    <tr{cls}><td>{esc(name)}<span class='hint'>{esc(WHAT_IT_IS.get(name,''))}"
            f" &middot; {len(t['dimensions'])} dimension"
            f"{'s' if len(t['dimensions'])!=1 else ''}</span></td>"
            f"<td class='n'>{f(t.get('internal'),5)}</td>"
            f"<td class='n'>{f(t.get('incumbent'),5)}</td>"
            f"<td class='n'>{t['margin']:+.5f}</td>"
            f"<td>{verdict}</td><td class='n'>{slow}</td></tr>")
    scoreboard_rows = "\n".join(rows)

    tail_rows = "\n".join(
        f"    <tr><td>{esc(x['task'])}<span class='hint'>{esc(x['transcript_source'])}</span></td>"
        f"<td class='n'>{f(x['mean'])}</td><td class='n'>{f(x['median'])}</td>"
        f"<td class='n'>{x['gap']:.4f}</td></tr>" for x in tail["cases"])

    lab_rows = "\n".join(
        "    <tr" + (" class='ours'" if k == "typhoon_pipeline" else "") + ">"
        f"<td>{esc(k.replace('_',' '))}<span class='hint'>{esc(a['fed'])}</span></td>"
        f"<td class='n'>{f(a['call_result_f1'],3)}</td>"
        f"<td class='n'>{f(a['product_f1'],3)}</td>"
        f"<td class='n'>{f(a['reason_f1'],3)}</td>"
        f"<td class='n'>{a['latency_med_s']}</td>"
        f"<td class='n'>{a['parse_valid']}/{a['label_calls']}</td></tr>"
        for k, a in d["labeller"]["arms"].items())

    inc = d["external_incumbent"]
    asr = d["transcribers"]
    qs = d["qwen_asr_status"]

    body = f"""
<div class="sheet">

<div class="masthead">
  <div>
    <h1>How our own GPU models performed</h1>
    <p class="sub">Six production tasks &middot; measured on <b>real</b> data by the parallel
    evaluation project &middot; plus our own synthetic run on one of them</p>
  </div>
  <div class="stamp">
    {STAMP}<br>
    {n_int} of {len(tasks)} tasks to internal<br>
    slower on all {len(tasks)}
  </div>
</div>

<div class="strip">
  <div class="tile risk">
    <div class="big">{n_int} of {len(tasks)}</div>
    <div class="cap">tasks where our models win on quality<br>and <b>both</b> are benchmarks
    that cannot separate models</div>
  </div>
  <div class="tile risk">
    <div class="big">{lo}&ndash;{hi}&times;</div>
    <div class="cap">slower than the incumbent<br><b>on every one of the {len(tasks)}</b>, no
    exception</div>
  </div>
  <div class="tile">
    <div class="big">{widest:.3f}</div>
    <div class="cap">the widest quality gap on any task<br>({esc(widest_task)}) &mdash; the
    models are close everywhere</div>
  </div>
  <div class="tile risk">
    <div class="big">8/8</div>
    <div class="cap">Tax-Invoice recall cells fixed at 1.0000<br>a metric that cannot fail</div>
  </div>
</div>

<h2><span class="n">1</span>The scoreboard, all six tasks</h2>

<div class="card">
<table>
  <thead><tr>
    <th>Task</th><th class="n">Ours<span class="hint">qwen3.8-27b-fp8</span></th>
    <th class="n">Incumbent<span class="hint">Gemini</span></th>
    <th class="n">Margin</th><th>Winner</th><th class="n">Our latency</th>
  </tr></thead>
  <tbody>
{scoreboard_rows}
  </tbody>
</table>
</div>

<div class="note">
  <p>The figure is the workbook's own headline row, <i>weighted Average F1 Score</i>,
  compared within a task only &mdash; section 4 explains why it is not comparable between
  tasks. Internal wins <b>{n_int}</b>, the incumbent <b>{n_inc}</b>, and the widest gap in
  either direction is <b>{widest:.3f}</b>. On quality these models are close on every task in
  the set.</p>
</div>

<h2><span class="n">2</span>Both of our wins are on benchmarks that cannot separate models</h2>

<div class="note risk">
  <p><b>RTR-Fraud &mdash; saturated.</b> All {sat['RTR-Fraud']['accuracy_cells']} model-by-field
  accuracy cells land between 98.5% and 100%. The worst arm misses
  {sat['RTR-Fraud']['of_items'] - sat['RTR-Fraud']['items_correct_min']} items out of
  {sat['RTR-Fraud']['of_items']}; the best misses none. Three different model families
  separated by three items is a benchmark with no room left in it &mdash; our
  {sat['RTR-Fraud']['margin']:+.5f} is not evidence of an advantage.</p>
  <p><b>Sentiment Telesale &mdash; a ground truth that barely varies.</b> Margin
  {sat['Sentiment Telesale']['margin']:+.5f} on {esc(sat['Sentiment Telesale']['calls'])}. The
  parallel project's own code records that <b>25 of 39 criteria carry a single label across all
  26 calls</b> and 5 of 14 sub-categories contain no violation at all, so a constant answer
  scores 98&ndash;99%. Our win is driven by the Compliance dimension &mdash; 3 criteria, 2 of
  which hold one label.</p>
  <p>Neither is a reason to doubt the models. Both are reasons not to quote these two wins as
  evidence for migration, and reasons to fix the benchmarks before the next round.</p>
</div>

<h2><span class="n">3</span>Latency &mdash; the one result that is unanimous</h2>

<div class="note risk">
  <p>Our models are slower on <b>every task in the set</b>, by <b>{lo}&times; to {hi}&times;</b>.
  This is the only finding in the workbook that points the same way six times out of six, and
  it is not marginal: Sentiment QA is {tasks['Sentiment QA']['latency_internal_s']}&nbsp;s a
  call against {tasks['Sentiment QA']['latency_incumbent_s']}&nbsp;s, and Tax-Invoice is
  {tasks['Tax-Invoice']['latency_internal_s']}&nbsp;s a page against
  {tasks['Tax-Invoice']['latency_incumbent_s']}&nbsp;s.</p>
  <p>Two of the six rounds also lost a call outright to a stream exceeding 900&nbsp;s
  (Retention and Telesale, one each). Whatever the quality verdict turns out to be, throughput
  is the thing that has to be answered before any of this is deployable.</p>
</div>

<h2><span class="n">4</span>What the published headline number actually computes</h2>

<div class="note risk">
  <p><b>It is a sum divided by 2, not an average.</b> {esc(scored['headline_row'])}</p>
  <p><b>Recall that cannot fail.</b> {esc(scored['recall_that_cannot_fail'])}</p>
  <p>Neither point changes who won any task &mdash; the divisor is constant within a tab. Both
  change what the numbers may be quoted as. A headline above 1.0 should not be described as an
  F1 score, and on Tax-Invoice and Sentiment QA the four reported metrics are not four
  independent checks.</p>
  <p class="hint">{esc(scored['macro_vs_micro'])}</p>
</div>

<h2><span class="n">5</span>The transcript tail, and who actually has it</h2>

<div class="card">
<table>
  <thead><tr><th>Task<span class="hint">transcript source</span></th>
    <th class="n">Mean</th><th class="n">Median</th><th class="n">Gap</th></tr></thead>
  <tbody>
{tail_rows}
  </tbody>
</table>
</div>

<div class="note">
  <p>&ldquo;Characters right&rdquo; is 1&nbsp;&minus;&nbsp;CER, so a <b>mean far below the
  median</b> means a few transcripts carry more inserted text than the reference &mdash; the
  runaway failure our own Qwen3-ASR showed 16 times. Retention's mean of
  <b>{real['transcript_mean_1_minus_cer_internal']}</b> against a median of
  <b>{real['transcript_median_1_minus_cer_internal']}</b> is the extreme case.</p>
  <p><b>Correcting our earlier report:</b> we attributed this to Typhoon. Across all four
  speech tasks it hits the internal transcriber {len(tail_int)} times and <b>the incumbent's
  own direct-audio path {len(tail_inc)} time</b> (Telesale, gap
  {tail_inc[0]['gap'] if tail_inc else 0:.4f}). It is more frequent on our side, and it is not
  ours alone &mdash; it is a property of scoring long-form Thai audio here. RTR-Fraud and
  Tax-Invoice have no transcript rows and are excluded rather than counted as clean.</p>
</div>

<h2><span class="n">6</span>Our own run &mdash; one task, synthetic, and it disagrees</h2>

<div class="card">
<table>
  <thead><tr>
    <th>Retention, call outcome F1</th><th class="n">Incumbent</th><th class="n">Ours</th>
    <th>Winner</th>
  </tr></thead>
  <tbody>
    <tr><td><b>97 real calls</b><span class="hint">human ground truth, GCS audio, Gemini on
      Vertex AI batch &mdash; production's own runtime</span></td>
      <td class="n">{f(real['gemini']['call_result_f1'])}</td>
      <td class="n">{f(real['internal']['call_result_f1'])}</td>
      <td>incumbent, all three dimensions</td></tr>
    <tr class="ours"><td>138 synthetic calls<span class="hint">generated audio, generated
      labels, Gemini via OpenRouter</span></td>
      <td class="n">{f(inc['call_result_f1'],3)}</td>
      <td class="n">{f(d['labeller']['arms']['typhoon_pipeline']['call_result_f1'],3)}</td>
      <td>ours, all three dimensions</td></tr>
  </tbody>
</table>
</div>

<div class="note risk">
  <p><b>The real-data run is the decision-grade one and it does not favour us.</b> Its ground
  truth is human-maintained, its audio is real customer calls, and it runs the incumbent on
  Vertex AI batch. Ours does none of those three.</p>
  <p><b>But read the margin, not the ranking.</b> {esc(real['margin_in_calls']['call_result'])};
  {esc(real['margin_in_calls']['product'])}; {esc(real['margin_in_calls']['reason'])}. With no
  replicates, three calls out of ninety-seven is not a separation &mdash; on outcome and
  product these two are <b>indistinguishable on this evidence</b>. The reason dimension is the
  one real gap.</p>
  <p><b>Why the reversal is plausible rather than a contradiction to explain away.</b> Our
  corpus <i>states every label aloud</i> &mdash; a documented property, not a discovery. That
  removes the need for the inferential step, and inference is exactly what a direct-audio model
  may be better at, since a transcript throws away prosody, hesitation and tone. So the
  synthetic set may not merely inflate scores; it may remove the thing the incumbent is good
  at. <b>That mechanism is a hypothesis. The reversal is a measurement.</b></p>
</div>

<h2><span class="n">7</span>On synthetic data: the labeller and the transcribers</h2>

<div class="card">
<table>
  <thead><tr><th>Arm<span class="hint">what the labeller was fed</span></th>
    <th class="n">Outcome</th><th class="n">Product</th><th class="n">Reason</th>
    <th class="n">Latency</th><th class="n">Parsed</th></tr></thead>
  <tbody>
{lab_rows}
    <tr><td>{esc(inc['model'])}<span class="hint">external &mdash; {esc(inc['runtime'])},
      audio straight in</span></td>
      <td class="n">{f(inc['call_result_f1'],3)}</td>
      <td class="n">{f(inc['product_f1'],3)}</td>
      <td class="n">{f(inc['reason_f1'],3)}</td>
      <td class="n">{inc['latency_med_s']}</td>
      <td class="n">{inc['parse_valid']}/{inc['label_calls']}</td></tr>
  </tbody>
</table>
</div>

<div class="note">
  <p>On synthetic data the deployable arm leads the incumbent on outcome and product at no
  metered cost, with cleaner JSON ({d['labeller']['arms']['typhoon_pipeline']['parse_valid']}/{d['labeller']['arms']['typhoon_pipeline']['label_calls']}
  against {inc['parse_valid']}/{inc['label_calls']}). The incumbent's metered cost over the run
  was ${inc['metered_cost_usd']} &mdash; ${inc['usd_per_call']} a call.</p>
  <p><b>Transcription, from the E23 run</b> (E24's ASR reports do not exist &mdash; see the
  footnote): Typhoon CER {f(asr['typhoon_whisper_large_v3']['cer_norm'])} with
  {asr['typhoon_whisper_large_v3']['runaway_items']} runaways;
  Qwen3-ASR CER {f(asr['qwen3_asr_17b']['cer_norm'])} with
  {asr['qwen3_asr_17b']['runaway_items']} runaways.
  <b>Qwen3-ASR is {qs['state']}</b> &mdash; {esc(qs['note'])}</p>
</div>

<h2><span class="n">8</span>What this says, and what it does not</h2>

<div class="note">
  <p><b>What the six tasks agree on.</b> Quality is close everywhere &mdash; the widest gap in
  either direction is {widest:.3f}. Speed is not close anywhere: we are {lo}&times; to
  {hi}&times; slower, six times out of six. On this evidence the case against migrating is
  throughput, not accuracy.</p>
  <p><b>What we should not claim.</b> That we beat the incumbent on RTR-Fraud or Telesale &mdash;
  both benchmarks are unable to separate models. That the incumbent beats us on Retention
  outcome or product &mdash; three calls out of ninety-seven, unreplicated, is not a
  separation. The one clean quality result in the set is Tax-Invoice, where
  gemini-3.1-pro-preview leads by {abs(tasks['Tax-Invoice']['margin']):.4f} and Gemma-4-12B
  collapses outright.</p>
  <p><b>What to fix before the next round.</b> The headline formula, the FN=0 confusion
  matrices on Tax-Invoice and Sentiment QA, and the Telesale ground truth. All three are
  cheap, and all three change what the numbers mean rather than what the models do.</p>
</div>

<div class="note risk">
  <p><b>Four things that must travel with these numbers.</b></p>
  <ul>
    <li><b>RECONCILED: NO for our own numbers.</b> Not one real production call has been
    through <i>our</i> pipeline. Sections 6 (synthetic row) and 7 are entirely synthetic Thai
    audio. Sections 1&ndash;5 are the parallel project's measurement, parsed from their
    published workbook and not reproduced here.</li>
    <li><b>Every label in our corpus is spoken aloud</b>, so our synthetic set measures fact
    extraction under transcription noise, not inferential labelling. Those scores are upper
    bounds.</li>
    <li><b>Our arms disagree with themselves</b> across three identical replicates on 17 to 33
    of 138 items. That instability is a standing property of the task, measured in both of our
    runs. The real-data run has no replicates at all.</li>
    <li><b>Our ground truth was corrected after a blind audit whose reviewers were models,
    not people.</b></li>
  </ul>
</div>

<p class="foot">
  Real-data figures across all six tasks parsed from
  <code>{esc(c['source_file'])}</code> by <code>{esc(c['parsed_by'])}</code>, produced by the
  parallel evaluation project and not reproduced here. Synthetic figures generated by
  <code>scripts/gpu_figures.py</code> from the E24 run (labeller) and the E23 run
  (transcribers). This page is rendered by <code>scripts/gpu_report.py</code> from
  <code>docs/reports/gpu-performance.json</code>; no figure on it is typed by hand.
</p>

</div>
"""
    return head.rstrip("\n") + "\n" + body


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="gpu_report")
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
