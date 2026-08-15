"""Render a soak run's `analysis.json` as an HTML report.

    python scripts/soak_report_html.py out/soak/<run>                  # fragment, for Artifact
    python scripts/soak_report_html.py out/soak/<run> --standalone      # full document

Reads `analysis.json` and nothing else, so this and `report.md` cannot disagree: both are views
of the same computed object. No number is typed in here.

Output is ASCII-only by construction (entities in markup, \\uXXXX in script) so it renders the
same whether or not the host declares a charset. Design tokens match
`docs/reports/experiment17-report.html` -- same team, same reports, one visual identity.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ENT = {"—": "&mdash;", "·": "&middot;", "…": "&hellip;", "×": "&times;",
       "“": "&ldquo;", "”": "&rdquo;", "±": "&plusmn;", "→": "&rarr;",
       "–": "&ndash;", "’": "&rsquo;"}

STYLE = """
:root{--ground:#F4F6F8;--raised:#FFF;--sunken:#EDF0F3;--ink:#151B23;--ink-muted:#57626F;
--ink-faint:#838E9C;--hairline:#DDE2E8;--hairline-firm:#C3CBD4;--s1:#2E74B5;--s2:#C2661A;
--s3:#7B4BB7;--st-bad:#B3261E;--st-bad-bg:#FBEDEC;--st-good:#1F6F4A;--st-good-bg:#EAF4EF;
--st-warn:#8A5A00;--st-warn-bg:#FBF2E3;--track:#E4E8ED;
--shadow:0 1px 2px rgba(21,27,35,.05),0 4px 14px rgba(21,27,35,.045);
--serif:"Charter","Bitstream Charter","Iowan Old Style",Cambria,Georgia,serif;
--sans:system-ui,-apple-system,"Segoe UI",Roboto,"Helvetica Neue",Arial,sans-serif;
--mono:ui-monospace,"Cascadia Mono","SF Mono",Menlo,Consolas,monospace}
@media (prefers-color-scheme:dark){:root:not([data-theme="light"]){--ground:#11151A;
--raised:#191F26;--sunken:#151A20;--ink:#E4E9EF;--ink-muted:#9AA5B2;--ink-faint:#6F7B89;
--hairline:#29313A;--hairline-firm:#3A444F;--s1:#4E90CE;--s2:#CE7C2E;--s3:#9C71D2;
--st-bad:#E8776C;--st-bad-bg:#2C1B1A;--st-good:#5FBF90;--st-good-bg:#14261E;
--st-warn:#D9A441;--st-warn-bg:#2A2113;--track:#232B33;
--shadow:0 1px 2px rgba(0,0,0,.4),0 4px 16px rgba(0,0,0,.3)}}
:root[data-theme="dark"]{--ground:#11151A;--raised:#191F26;--sunken:#151A20;--ink:#E4E9EF;
--ink-muted:#9AA5B2;--ink-faint:#6F7B89;--hairline:#29313A;--hairline-firm:#3A444F;
--s1:#4E90CE;--s2:#CE7C2E;--s3:#9C71D2;--st-bad:#E8776C;--st-bad-bg:#2C1B1A;
--st-good:#5FBF90;--st-good-bg:#14261E;--st-warn:#D9A441;--st-warn-bg:#2A2113;
--track:#232B33;--shadow:0 1px 2px rgba(0,0,0,.4),0 4px 16px rgba(0,0,0,.3)}
*{box-sizing:border-box}
body{background:var(--ground);color:var(--ink);font-family:var(--sans);font-size:16px;
line-height:1.6;margin:0;padding:0 20px 96px;-webkit-font-smoothing:antialiased}
.wrap{max-width:1120px;margin:0 auto}.prose{max-width:68ch}
.masthead{padding:56px 0 26px;border-bottom:2px solid var(--ink)}
.kicker{font-family:var(--mono);font-size:11px;letter-spacing:.16em;text-transform:uppercase;
color:var(--ink-muted);margin:0 0 18px}
h1{font-family:var(--serif);font-weight:700;font-size:clamp(32px,5vw,50px);line-height:1.03;
letter-spacing:-.02em;margin:0 0 14px;text-wrap:balance}
.standfirst{font-family:var(--serif);font-size:clamp(17px,2.1vw,21px);line-height:1.5;
color:var(--ink-muted);margin:0;max-width:62ch}
.verdict{display:inline-flex;align-items:center;gap:10px;font-family:var(--mono);font-size:15px;
font-weight:700;letter-spacing:.06em;padding:10px 18px;border-radius:3px;margin:22px 0 0}
.verdict::before{content:"";width:10px;height:10px;border-radius:50%;background:currentColor}
.v-pass{color:var(--st-good);background:var(--st-good-bg)}
.v-warn{color:var(--st-warn);background:var(--st-warn-bg)}
.v-fail{color:var(--st-bad);background:var(--st-bad-bg)}
.grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(150px,1fr));gap:1px;
background:var(--hairline);border:1px solid var(--hairline);margin:26px 0 0}
.grid div{background:var(--ground);padding:12px 14px}
.grid dt{font-family:var(--mono);font-size:10px;letter-spacing:.12em;text-transform:uppercase;
color:var(--ink-faint);margin:0 0 4px}
.grid dd{font-family:var(--mono);font-size:15px;margin:0;font-variant-numeric:tabular-nums;
overflow-wrap:anywhere;font-weight:600}
section{padding-top:50px}
.sec-head{display:flex;align-items:baseline;gap:14px;margin-bottom:6px}
.sec-num{font-family:var(--mono);font-size:12px;color:var(--ink-faint);letter-spacing:.1em;
padding-top:4px;flex:none}
h2{font-family:var(--serif);font-size:clamp(22px,3vw,30px);font-weight:700;letter-spacing:-.015em;
line-height:1.15;margin:0;text-wrap:balance}
h3{font-size:15px;font-weight:650;margin:28px 0 10px}
.lede{color:var(--ink-muted);margin:10px 0 0}p{margin:0 0 14px}
.note{font-size:13.5px;line-height:1.55;color:var(--ink-muted);border-left:2px solid
var(--hairline-firm);padding:2px 0 2px 14px;margin:16px 0 0;max-width:72ch}
.note-bad{border-left-color:var(--st-bad)}
.scroller{overflow-x:auto;-webkit-overflow-scrolling:touch}
.chartbox{background:var(--raised);border:1px solid var(--hairline);padding:20px 22px}
figure{margin:24px 0 0}
figcaption{font-family:var(--mono);font-size:11px;letter-spacing:.11em;text-transform:uppercase;
color:var(--ink-faint);margin-bottom:14px}
svg{display:block;max-width:100%;height:auto}svg text{font-family:var(--mono);fill:var(--ink-muted)}
table{border-collapse:collapse;width:100%;font-size:13px;font-variant-numeric:tabular-nums;
background:var(--raised);min-width:560px}
caption{text-align:left;font-family:var(--mono);font-size:11px;letter-spacing:.11em;
text-transform:uppercase;color:var(--ink-faint);padding-bottom:12px}
th,td{padding:9px 12px;text-align:right;border-bottom:1px solid var(--hairline)}
th:first-child,td:first-child{text-align:left}
thead th{font-family:var(--mono);font-size:10.5px;letter-spacing:.08em;text-transform:uppercase;
color:var(--ink-faint);font-weight:500;border-bottom:1px solid var(--hairline-firm);
white-space:nowrap}
tbody td{font-family:var(--mono);white-space:nowrap}
tbody th{font-family:var(--mono);font-weight:600;font-size:13px;white-space:nowrap}
tbody tr:last-child td,tbody tr:last-child th{border-bottom:0}
.best{font-weight:700;color:var(--ink)}
.bad{color:var(--st-bad);font-weight:700}
.rec td,.rec th{background:var(--st-good-bg)}
ul.findings{list-style:none;padding:0;margin:18px 0 0;max-width:74ch}
ul.findings li{position:relative;padding:12px 0 12px 30px;border-bottom:1px solid var(--hairline);
font-size:14.5px;line-height:1.55}
ul.findings li::before{position:absolute;left:0;top:12px;font-family:var(--mono);font-size:13px}
li.ok::before{content:"+";color:var(--st-good)}
li.warn::before{content:"!";color:var(--st-warn)}
li.fail::before{content:"x";color:var(--st-bad)}
footer{margin-top:56px;padding-top:20px;border-top:2px solid var(--ink);font-family:var(--mono);
font-size:11.5px;color:var(--ink-faint);line-height:1.7}
#tip{position:fixed;pointer-events:none;opacity:0;transition:opacity .1s;background:var(--ink);
color:var(--ground);font-family:var(--mono);font-size:11.5px;padding:6px 9px;border-radius:3px;
z-index:50;white-space:nowrap;font-variant-numeric:tabular-nums}
@media (prefers-reduced-motion:reduce){*{transition:none!important}}
.hit{cursor:crosshair}
"""


def esc(text: str) -> str:
    out = str(text)
    for k, v in ENT.items():
        out = out.replace(k, v)
    return "".join(c if ord(c) < 128 else f"&#{ord(c)};" for c in out)


def f(x, digits=2, dash="&mdash;"):
    if x is None:
        return dash
    if isinstance(x, float):
        return f"{x:,.{digits}f}"
    return f"{x:,}"


def pctf(x, digits=2, dash="&mdash;"):
    return dash if x is None else f"{x * 100:.{digits}f}%"


def build(a: dict) -> str:
    m = a["meta"]
    o = a["overall"]
    verdict = a["verdict"]
    vclass = {"PASS": "v-pass", "PASS WITH ISSUES": "v-warn", "FAIL": "v-fail"}[verdict]

    def perf_rows(mapping, label_fmt=lambda k, s: k):
        out = []
        rec = str(a.get("recommended_concurrency"))
        for key, s in mapping.items():
            if not s.get("requests"):
                continue
            t, e = s.get("ttft_s") or {}, s.get("e2e_s") or {}
            err = s.get("post_retry_error_rate") or 0
            cor = (s.get("correctness") or {}).get("rate")
            klass = ' class="rec"' if str(key) == rec else ""
            out.append(
                f"<tr{klass}><th scope=row>{esc(label_fmt(key, s))}</th>"
                f"<td>{f(s.get('requests'), 0)}</td>"
                f"<td>{f(s.get('requests_per_s'), 3)}</td>"
                f"<td>{f(t.get('p50'), 3)}</td><td>{f(t.get('p95'), 3)}</td>"
                f"<td>{f(e.get('p50'), 2)}</td><td>{f(e.get('p95'), 2)}</td>"
                f"<td>{f(e.get('p99'), 2)}</td>"
                f"<td>{f((s.get('output_tokens_per_s') or {}).get('p50'), 1)}</td>"
                f"<td class='{'bad' if err > 0.01 else ''}'>{pctf(err)}</td>"
                f"<td>{pctf(cor, 1)}</td></tr>")
        return "".join(out)

    HEAD = ("<thead><tr><th>level</th><th>reqs</th><th>req/s</th><th>TTFT p50</th>"
            "<th>TTFT p95</th><th>e2e p50</th><th>e2e p95</th><th>e2e p99</th>"
            "<th>tok/s</th><th>err</th><th>correct</th></tr></thead>")

    findings = []
    for r in a["fail_reasons"]:
        findings.append(f'<li class="fail">{esc(r)}</li>')
    for i in a["issues"]:
        findings.append(f'<li class="warn">{esc(i)}</li>')
    if not findings:
        findings.append('<li class="ok">No failures and no issues against the '
                        'pre-registered criteria.</li>')

    deg_blocks = []
    for key, d in (a.get("degradation") or {}).items():
        def sg(x):
            if x is None:
                return "<td>&mdash;</td>"
            cls = " class='bad'" if x > 0.20 else ""
            return f"<td{cls}>{x * 100:+.1f}%</td>"
        deg_blocks.append(
            f"<tr><th scope=row>{esc(d['early'])} &rarr; {esc(d['late'])}</th>"
            f"{sg(d['e2e_p50_drift'])}{sg(d['e2e_p95_drift'])}"
            f"{sg(d['ttft_p50_drift'])}{sg(d['ttft_p95_drift'])}"
            f"{sg(d['throughput_drift'])}"
            f"<td>{d['truncated_early']} &rarr; {d['truncated_late']}</td>"
            f"<td>{pctf(d['error_rate_early'])} &rarr; {pctf(d['error_rate_late'])}</td></tr>")

    det = a["determinism"]
    det_rows = "".join(
        f"<tr><th scope=row>c{esc(c)}</th><td>{v['n']}</td><td>{v['mismatch']}</td>"
        f"<td class='{'bad' if (v['rate'] or 0) > 0 else ''}'>{pctf(v['rate'], 1)}</td></tr>"
        for c, v in det["by_concurrency"].items())

    err_rows = "".join(f"<tr><th scope=row><code>{esc(k)}</code></th><td>{v}</td></tr>"
                       for k, v in sorted(a["error_taxonomy"].items(), key=lambda kv: -kv[1]))

    # A phase the ramp aborted did not run to completion, so the requests that DID finish are
    # the ones that happened not to queue -- a survivorship sample whose percentiles flatter
    # the level rather than describe it. Say so next to the numbers, or a reader compares them
    # with the complete levels above.
    killed = [p for p in (a["meta"].get("phases_executed") or []) if p.get("aborted")]
    aborted_note = ""
    if killed:
        items = "; ".join(
            f"<strong>c{esc(p['concurrency'])}</strong> ({esc(p['aborted'])})" for p in killed)
        aborted_note = (
            f'<div class="note note-bad"><strong>Aborted level(s): {items}.</strong> Those '
            "phases stopped early, so the requests that completed are the ones that happened "
            "not to queue. Read their row as evidence that the level breaks, not as a "
            "description of how it performs &mdash; the percentiles are survivorship-biased "
            "and are not comparable with the levels that ran to completion.</div>")

    av = a["availability"]
    payload = json.dumps({
        "by_concurrency": a["by_concurrency"],
        "phases": a["phases"],
        "recommended": a.get("recommended_concurrency"),
    })

    return f"""<title>GPU Soak Readout</title>
<style>{STYLE}</style>
<div id="tip" role="status" aria-live="off"></div>
<div class="wrap">

<header class="masthead">
  <p class="kicker">True internal GPU &middot; Token Factory &middot; {esc(m.get('started_utc'))}</p>
  <h1>5-Hour Soak Readout</h1>
  <p class="standfirst">Sustained and escalating load against <code>{esc(m.get('model'))}</code>
  on True&rsquo;s internal vLLM serving, measuring whether it holds and where it breaks.</p>
  <div class="verdict {vclass}">{esc(verdict)}</div>
  <dl class="grid">
    <div><dt>Duration</dt><dd>{f(m.get('elapsed_minutes'), 1)} min</dd></div>
    <div><dt>Requests</dt><dd>{f(o.get('requests'), 0)}</dd></div>
    <div><dt>Attempts</dt><dd>{f(o.get('attempts'), 0)}</dd></div>
    <div><dt>Error rate</dt><dd>{pctf(o.get('post_retry_error_rate'))}</dd></div>
    <div><dt>Max tested</dt><dd>c{esc(a.get('max_concurrency_tested'))}</dd></div>
    <div><dt>Recommended</dt><dd>c{esc(a.get('recommended_concurrency'))}</dd></div>
    <div><dt>Input tokens</dt><dd>{f(o['tokens']['input_total'], 0)}</dd></div>
    <div><dt>Output tokens</dt><dd>{f(o['tokens']['output_total'], 0)}</dd></div>
  </dl>
</header>

<section id="summary">
  <div class="sec-head"><span class="sec-num">01</span><h2>Executive summary</h2></div>
  <p class="lede prose">Measured against pass criteria fixed in the plan <em>before</em> the run:
  post-retry error rate under {pctf(a['pass_criteria']['post_retry_error_rate_max'], 0)} at the
  recommended concurrency, p95 drift under
  {pctf(a['pass_criteria']['p95_drift_max'], 0)} between the first and last normal-load phase,
  no availability gap, no unexplained error.</p>
  <ul class="findings">{''.join(findings)}</ul>
  <div class="note note-bad"><strong>GPU utilization, VRAM, temperature and power are not in
  this report.</strong> Not an omission &mdash; unobtainable. {esc(m.get('gpu_telemetry', ''))}</div>
</section>

<section id="concurrency">
  <div class="sec-head"><span class="sec-num">02</span><h2>Results by concurrency</h2></div>
  <p class="lede prose">Closed loop: N workers, each issuing its next request the moment its
  previous one returns. The highlighted row is the recommended sustainable level &mdash; the
  fastest level that held the error rate inside the bar.</p>
  <figure>
    <figcaption>Throughput and tail latency against concurrency</figcaption>
    <div class="chartbox scroller"><div id="c-conc"></div></div>
  </figure>
  <div class="scroller" style="margin-top:22px">
    <table><caption>By concurrency level</caption>{HEAD}
    <tbody>{perf_rows(a['by_concurrency'], lambda k, s: f'c{k}')}</tbody></table>
  </div>
  <div class="note">Maximum tested <strong>c{esc(a.get('max_concurrency_tested'))}</strong>;
  recommended sustainable <strong>c{esc(a.get('recommended_concurrency'))}</strong>
  {esc('(' + a['recommendation_basis'] + ')') if a.get('recommendation_basis') else ''}.</div>
  {aborted_note}
</section>

<section id="phases">
  <div class="sec-head"><span class="sec-num">03</span><h2>Stability over five hours</h2></div>
  <p class="lede prose">Phases run in order. <code>normal</code> and <code>normal_end</code> are
  the same configuration hours apart, as are <code>baseline_a</code> and
  <code>baseline_b</code> &mdash; any difference between them is drift, not workload.</p>
  <figure>
    <figcaption>Latency and error rate through the run</figcaption>
    <div class="chartbox scroller"><div id="c-time"></div></div>
  </figure>
  <div class="scroller" style="margin-top:22px">
    <table><caption>By phase, in execution order</caption>{HEAD}
    <tbody>{perf_rows(a['phases'], lambda k, s: f"{k} (c{s.get('concurrency')})")}</tbody></table>
  </div>
  <div class="scroller" style="margin-top:24px">
    <table><caption>Degradation &mdash; identical configuration, hours apart</caption>
    <thead><tr><th>comparison</th><th>e2e p50</th><th>e2e p95</th><th>TTFT p50</th>
    <th>TTFT p95</th><th>throughput</th><th>truncated</th><th>error rate</th></tr></thead>
    <tbody>{''.join(deg_blocks) or '<tr><td colspan=8>no paired phases completed</td></tr>'}</tbody>
    </table>
  </div>
  <div class="note">Availability: {av['polls']} health polls, {av['non_200']} non-200,
  {len(av['gaps'])} gap(s) longer than 60&nbsp;s. Health polling is unauthenticated on this
  deployment, so it runs on its own thread and never competes with the load.</div>
</section>

<section id="determinism">
  <div class="sec-head"><span class="sec-num">04</span><h2>Determinism under load</h2></div>
  <p class="lede prose">Three prompts re-sent byte-identically every ten minutes and hashed.
  The shakedown caught this model family flipping on an idle box and blamed vLLM continuous
  batching &mdash; which predicts the rate <em>rises</em> with concurrency. This is the test of
  that prediction.</p>
  <div class="scroller" style="margin-top:20px">
    <table><caption>{det['total_mismatches']} of {det['total_probes']} probes diverged from
    their first response</caption>
    <thead><tr><th>concurrency at send</th><th>probes</th><th>diverged</th><th>rate</th></tr>
    </thead><tbody>{det_rows or '<tr><td colspan=4>no probes recorded</td></tr>'}</tbody></table>
  </div>
</section>

<section id="errors">
  <div class="sec-head"><span class="sec-num">05</span><h2>Errors</h2></div>
  <div class="scroller" style="margin-top:18px">
    <table><caption>Error taxonomy, all attempts</caption>
    <thead><tr><th>outcome</th><th>count</th></tr></thead>
    <tbody>{err_rows or '<tr><th scope=row>none</th><td>0</td></tr>'}</tbody></table>
  </div>
  <div class="note">Raw per-attempt error rate {pctf(o.get('raw_error_rate'))}; post-retry
  {pctf(o.get('post_retry_error_rate'))}. Both are reported because they answer different
  questions: the first is what the server did, the second is what a client experiences.
  Retries follow the vendor policy &mdash; honour <code>Retry-After</code>, exponential backoff
  with jitter, never retry a 4xx other than 429.</div>
</section>

<section id="classes">
  <div class="sec-head"><span class="sec-num">06</span><h2>By prompt class</h2></div>
  <p class="lede prose">Seven shapes of work, so a single averaged latency cannot hide a
  class that behaves differently under load.</p>
  <div class="scroller" style="margin-top:20px">
    <table><caption>By prompt class</caption>{HEAD}
    <tbody>{perf_rows(a['by_class'])}</tbody></table>
  </div>
</section>

<section id="baseline">
  <div class="sec-head"><span class="sec-num">07</span><h2>Baseline for the next model</h2></div>
  <p class="lede prose">Copy this block forward. Re-running
  <code>scripts/soak_test.py</code> against another model or another endpoint produces the same
  shape, so the comparison is like for like.</p>
  <div class="chartbox scroller" style="margin-top:18px"><pre style="margin:0;font-family:
  var(--mono);font-size:12.5px;line-height:1.6">{esc(json.dumps({
      'model': m.get('model'), 'endpoint': m.get('endpoint'),
      'verdict': verdict,
      'recommended_concurrency': a.get('recommended_concurrency'),
      'max_concurrency_tested': a.get('max_concurrency_tested'),
      'requests_per_s_at_recommended': (a['by_concurrency'].get(
          str(a.get('recommended_concurrency'))) or {}).get('requests_per_s'),
      'ttft_p50_s': (o.get('ttft_s') or {}).get('p50'),
      'ttft_p95_s': (o.get('ttft_s') or {}).get('p95'),
      'e2e_p95_s': (o.get('e2e_s') or {}).get('p95'),
      'output_tokens_per_s_p50': (o.get('output_tokens_per_s') or {}).get('p50'),
      'aggregate_output_tokens_per_s': o['tokens'].get('aggregate_output_per_s'),
      'post_retry_error_rate': o.get('post_retry_error_rate'),
      'correctness_rate': (o.get('correctness') or {}).get('rate'),
  }, indent=2))}</pre></div>
</section>

<footer>
Run directory <code>{esc(a['run_dir'])}</code> &middot; analysis generated
{esc(a['generated_utc'])}<br>
Raw artifacts: <code>requests.jsonl</code> (one row per attempt), <code>timeline.jsonl</code>
(30s buckets), <code>health.jsonl</code> (15s polls + determinism probes),
<code>run.json</code>, <code>analysis.json</code>, <code>report.md</code>.
Scripts: <code>scripts/soak_test.py</code>, <code>scripts/soak_report.py</code>,
<code>scripts/soak_report_html.py</code>. Prompts:
<code>tests/fixtures/soak/prompts_authored.jsonl</code>. Plan:
<code>docs/soak-test-plan.md</code>.<br>
Raw logs live under <code>out/</code> and are never committed.
</footer>
</div>
<script>
(function(){{
"use strict";
var D = {payload};
var NS="http://www.w3.org/2000/svg";
function el(n,a,t){{var e=document.createElementNS(NS,n);for(var k in a){{if(a[k]!==null)
e.setAttribute(k,a[k]);}}if(t!==undefined)e.textContent=t;return e;}}
function v(n){{return "var("+n+")";}}
var tip=document.getElementById("tip");
function bind(node,text){{node.classList.add("hit");
node.addEventListener("mousemove",function(e){{tip.textContent=text;tip.style.opacity="1";
var x=e.clientX+14,y=e.clientY-34;
if(x+tip.offsetWidth>window.innerWidth-8)x=e.clientX-tip.offsetWidth-14;
tip.style.left=x+"px";tip.style.top=y+"px";}});
node.addEventListener("mouseleave",function(){{tip.style.opacity="0";}});
node.appendChild(el("title",{{}},text));}}

// ---- throughput and tail latency against concurrency -------------------------------
(function(){{
  var levels=Object.keys(D.by_concurrency).map(Number).sort(function(a,b){{return a-b;}})
    .filter(function(c){{return (D.by_concurrency[String(c)].requests||0)>=5;}});
  if(!levels.length)return;
  var W=780,padL=64,padR=64,padT=30,padB=46,H=300;
  var plotW=W-padL-padR,plotH=H-padT-padB;
  var tp=levels.map(function(c){{return D.by_concurrency[String(c)].requests_per_s||0;}});
  var lat=levels.map(function(c){{return (D.by_concurrency[String(c)].e2e_s||{{}}).p95||0;}});
  var maxTp=Math.max.apply(null,tp)*1.15||1, maxLat=Math.max.apply(null,lat)*1.15||1;
  var svg=el("svg",{{viewBox:"0 0 "+W+" "+H,width:W,height:H,role:"img",
    "aria-label":"Throughput and p95 latency against concurrency"}});
  var bw=Math.min(46,plotW/levels.length*0.42);
  levels.forEach(function(c,i){{
    var cx=padL+plotW*(i+0.5)/levels.length;
    var h=plotH*(tp[i]/maxTp);
    var r=el("rect",{{x:cx-bw-2,y:padT+plotH-h,width:bw,height:Math.max(h,1),rx:2,
      fill:v("--s1")}});
    bind(r,"c"+c+" \\u2014 "+tp[i].toFixed(3)+" requests/s");
    svg.appendChild(r);
    var h2=plotH*(lat[i]/maxLat);
    var r2=el("rect",{{x:cx+2,y:padT+plotH-h2,width:bw,height:Math.max(h2,1),rx:2,
      fill:v("--s2")}});
    bind(r2,"c"+c+" \\u2014 p95 end-to-end "+lat[i].toFixed(2)+" s");
    svg.appendChild(r2);
    svg.appendChild(el("text",{{x:cx,y:H-26,"font-size":11.5,"text-anchor":"middle",
      "font-weight":String(c)===String(D.recommended)?700:400,
      fill:String(c)===String(D.recommended)?v("--st-good"):v("--ink-muted")}},"c"+c));
  }});
  svg.appendChild(el("line",{{x1:padL,y1:padT+plotH,x2:padL+plotW,y2:padT+plotH,
    stroke:v("--hairline-firm"),"stroke-width":1}}));
  svg.appendChild(el("text",{{x:padL,y:16,"font-size":10,"letter-spacing":"1.1",
    fill:v("--s1")}},"REQUESTS / S"));
  svg.appendChild(el("text",{{x:padL+plotW,y:16,"font-size":10,"text-anchor":"end",
    "letter-spacing":"1.1",fill:v("--s2")}},"P95 END-TO-END (S)"));
  svg.appendChild(el("text",{{x:padL+plotW/2,y:H-6,"font-size":9.5,"text-anchor":"middle",
    "letter-spacing":"1.2",fill:v("--ink-faint")}},
    "TWO SCALES, TWO BARS \\u2014 NEVER ONE AXIS FOR BOTH"));
  document.getElementById("c-conc").appendChild(svg);
}})();

// ---- phases through the run ---------------------------------------------------------
(function(){{
  var names=Object.keys(D.phases);
  if(!names.length)return;
  var W=780,padL=118,padR=70,padT=28,rowH=34,padB=34;
  var H=padT+names.length*rowH+padB;
  var maxP95=Math.max.apply(null,names.map(function(n){{
    return (D.phases[n].e2e_s||{{}}).p95||0;}}))*1.1||1;
  var svg=el("svg",{{viewBox:"0 0 "+W+" "+H,width:W,height:H,role:"img",
    "aria-label":"p95 latency by phase"}});
  var trackW=W-padL-padR;
  names.forEach(function(n,i){{
    var s=D.phases[n],y=padT+i*rowH;
    var p95=(s.e2e_s||{{}}).p95||0, err=s.post_retry_error_rate||0;
    svg.appendChild(el("text",{{x:padL-12,y:y+14,"font-size":11,"text-anchor":"end",
      fill:v("--ink")}},n));
    svg.appendChild(el("line",{{x1:padL,y1:y+10,x2:padL+trackW,y2:y+10,stroke:v("--track"),
      "stroke-width":2}}));
    var w=trackW*(p95/maxP95);
    var r=el("rect",{{x:padL,y:y+3,width:Math.max(w,1),height:15,rx:2,
      fill:err>0.01?v("--st-bad"):v("--s1")}});
    bind(r,n+" (c"+s.concurrency+") \\u2014 p95 "+p95.toFixed(2)+" s, "+
      (s.requests||0)+" requests, error "+(err*100).toFixed(2)+"%");
    svg.appendChild(r);
    svg.appendChild(el("text",{{x:padL+w+8,y:y+15,"font-size":10.5,fill:v("--ink-faint")}},
      p95.toFixed(1)+"s"));
  }});
  svg.appendChild(el("text",{{x:padL,y:14,"font-size":9.5,"letter-spacing":"1.2",
    fill:v("--ink-faint")}},"P95 END-TO-END BY PHASE \\u2014 RED IF ERROR RATE > 1%"));
  document.getElementById("c-time").appendChild(svg);
}})();
}})();
</script>"""


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="soak_report_html")
    ap.add_argument("run_dir")
    ap.add_argument("--standalone", action="store_true",
                    help="emit a full document rather than an Artifact fragment")
    ap.add_argument("--out", default=None)
    args = ap.parse_args(argv)

    directory = Path(args.run_dir)
    analysis = directory / "analysis.json"
    if not analysis.is_file():
        print(f"no analysis.json in {directory}; run scripts/soak_report.py first")
        return 2

    html = build(json.loads(analysis.read_text(encoding="utf-8")))
    if args.standalone:
        cut = html.index("</style>") + len("</style>")
        html = ('<!DOCTYPE html>\n<html lang="en">\n<head>\n<meta charset="utf-8">\n'
                '<meta name="viewport" content="width=device-width, initial-scale=1">\n'
                + html[:cut] + "\n</head>\n<body>\n" + html[cut:] + "\n</body>\n</html>\n")

    out = Path(args.out) if args.out else directory / (
        "report_standalone.html" if args.standalone else "report_fragment.html")
    out.write_text(html, encoding="utf-8")
    print(f"wrote {out} ({len(html):,} bytes, ascii-only: {html.isascii()})")
    return 0


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
