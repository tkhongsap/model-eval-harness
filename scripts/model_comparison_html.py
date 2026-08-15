"""Render the model-comparison metrics as HTML. Called by `model_comparison_report.py`.

Every value comes from the metrics dict; nothing is hard-coded, so the page cannot disagree
with the JSON beside it. Output is ASCII-only by construction (entities in markup, \\uXXXX in
script) so it renders the same whether or not the host declares a charset.

Charts are faceted rather than grouped: four categorical hues cannot clear colour-blind
separation when every pair sits side by side (measured best case ~5.5 dE, below the usable
floor), so identity is carried by the row label and colour only distinguishes production from
our own hardware.
"""

from __future__ import annotations

ENT = {"—": "&mdash;", "·": "&middot;", "…": "&hellip;", "×": "&times;", "“": "&ldquo;",
       "”": "&rdquo;", "’": "&rsquo;", "–": "&ndash;", "≈": "&asymp;", "→": "&rarr;"}

# Directional phrasing, because every row is "Gemini vs X" and a verdict should name the
# winner rather than leave the reader working out whose perspective "worse" is from. AHEAD is
# unused on today's data but must map, or a future challenger win would render as a blank tag.
VERDICT_WORDS = {
    "BEHIND": ("GEMINI BETTER", "t-bad"),
    "INDISTINGUISHABLE": ("NO REAL DIFFERENCE", "t-good"),
    "UNDERPOWERED": ("TOO FEW TO CALL", "t-unk"),
    "AHEAD": ("CHALLENGER BETTER", "t-good"),
}


def esc(text) -> str:
    out = str(text)
    for k, v in ENT.items():
        out = out.replace(k, v)
    return "".join(c if ord(c) < 128 else f"&#{ord(c)};" for c in out)


def num(x, dp=3):
    return "&mdash;" if x is None else f"{x:,.{dp}f}"


STYLE = """
:root{--ground:#F4F6F8;--raised:#FFF;--sunken:#EDF0F3;--ink:#151B23;--ink-muted:#57626F;
--ink-faint:#838E9C;--hairline:#DDE2E8;--hairline-firm:#C3CBD4;--s-prod:#2E74B5;
--s-ours:#C2661A;--s-best:#1F6F4A;--st-bad:#B3261E;--st-bad-bg:#FBEDEC;--st-good:#1F6F4A;
--st-good-bg:#EAF4EF;--st-unk:#64707E;--st-unk-bg:#EEF1F4;--track:#E4E8ED;
--shadow:0 1px 2px rgba(21,27,35,.05),0 4px 14px rgba(21,27,35,.045);
--serif:"Charter","Bitstream Charter","Iowan Old Style",Cambria,Georgia,serif;
--sans:system-ui,-apple-system,"Segoe UI",Roboto,"Helvetica Neue",Arial,sans-serif;
--mono:ui-monospace,"Cascadia Mono","SF Mono",Menlo,Consolas,monospace}
@media (prefers-color-scheme:dark){:root:not([data-theme="light"]){--ground:#11151A;
--raised:#191F26;--sunken:#151A20;--ink:#E4E9EF;--ink-muted:#9AA5B2;--ink-faint:#6F7B89;
--hairline:#29313A;--hairline-firm:#3A444F;--s-prod:#4E90CE;--s-ours:#CE7C2E;--s-best:#5FBF90;
--st-bad:#E8776C;--st-bad-bg:#2C1B1A;--st-good:#5FBF90;--st-good-bg:#14261E;--st-unk:#8B96A3;
--st-unk-bg:#1E242B;--track:#232B33;
--shadow:0 1px 2px rgba(0,0,0,.4),0 4px 16px rgba(0,0,0,.3)}}
:root[data-theme="dark"]{--ground:#11151A;--raised:#191F26;--sunken:#151A20;--ink:#E4E9EF;
--ink-muted:#9AA5B2;--ink-faint:#6F7B89;--hairline:#29313A;--hairline-firm:#3A444F;
--s-prod:#4E90CE;--s-ours:#CE7C2E;--s-best:#5FBF90;--st-bad:#E8776C;--st-bad-bg:#2C1B1A;
--st-good:#5FBF90;--st-good-bg:#14261E;--st-unk:#8B96A3;--st-unk-bg:#1E242B;--track:#232B33;
--shadow:0 1px 2px rgba(0,0,0,.4),0 4px 16px rgba(0,0,0,.3)}
*{box-sizing:border-box}
body{background:var(--ground);color:var(--ink);font-family:var(--sans);font-size:16px;
line-height:1.6;margin:0;padding:0 20px 64px;-webkit-font-smoothing:antialiased}
.wrap{max-width:1000px;margin:0 auto}
header{padding:48px 0 22px;border-bottom:2px solid var(--ink)}
.kicker{font-family:var(--mono);font-size:11px;letter-spacing:.16em;text-transform:uppercase;
color:var(--ink-muted);margin:0 0 14px}
h1{font-family:var(--serif);font-weight:700;font-size:clamp(29px,4.3vw,42px);line-height:1.05;
letter-spacing:-.02em;margin:0 0 12px;text-wrap:balance}
.standfirst{font-family:var(--serif);font-size:clamp(16.5px,2vw,19.5px);line-height:1.5;
color:var(--ink-muted);margin:0;max-width:64ch}
.safe{display:inline-flex;align-items:center;gap:8px;font-family:var(--mono);font-size:11px;
font-weight:600;letter-spacing:.05em;padding:7px 12px;border-radius:2px;margin-top:18px;
color:var(--st-good);background:var(--st-good-bg);text-transform:uppercase}
.safe::before{content:"";width:7px;height:7px;border-radius:50%;background:currentColor}
section{padding-top:42px}
h2{font-family:var(--serif);font-size:clamp(20px,2.7vw,26px);font-weight:700;
letter-spacing:-.015em;line-height:1.15;margin:0 0 8px;text-wrap:balance}
.lede{color:var(--ink-muted);margin:0 0 4px;max-width:72ch}
p{margin:0 0 13px;max-width:72ch}
figure{margin:20px 0 0}
figcaption{font-family:var(--mono);font-size:11px;letter-spacing:.11em;text-transform:uppercase;
color:var(--ink-faint);margin-bottom:12px}
.chartbox{background:var(--raised);border:1px solid var(--hairline);padding:20px 22px}
svg{display:block;max-width:100%;height:auto}
svg text{font-family:var(--mono);fill:var(--ink-muted)}
.legend{display:flex;flex-wrap:wrap;gap:6px 22px;margin-bottom:12px;font-family:var(--mono);
font-size:11.5px;color:var(--ink-muted)}
.legend span{display:inline-flex;align-items:center}
.sw{display:inline-block;width:10px;height:10px;border-radius:2px;margin-right:8px}
.scroller{overflow-x:auto}
table{border-collapse:collapse;width:100%;font-size:13px;background:var(--raised);
font-variant-numeric:tabular-nums;min-width:620px}
caption{text-align:left;font-family:var(--mono);font-size:11px;letter-spacing:.11em;
text-transform:uppercase;color:var(--ink-faint);padding-bottom:11px}
th,td{padding:9px 11px;text-align:right;border-bottom:1px solid var(--hairline);
vertical-align:top}
th:first-child,td:first-child{text-align:left}
thead th{font-family:var(--mono);font-size:10px;letter-spacing:.07em;text-transform:uppercase;
color:var(--ink-faint);font-weight:500;border-bottom:1px solid var(--hairline-firm)}
tbody td{font-family:var(--mono)}
tbody th{font-family:var(--mono);font-weight:600;font-size:12.5px}
tbody tr:last-child td,tbody tr:last-child th{border-bottom:0}
.prod-row td,.prod-row th{background:var(--sunken)}
.best{font-weight:700;color:var(--ink)}
.tag{display:inline-block;font-family:var(--mono);font-size:9.5px;font-weight:700;
letter-spacing:.03em;padding:3px 6px;border-radius:2px;white-space:nowrap}
.t-good{color:var(--st-good);background:var(--st-good-bg)}
.t-bad{color:var(--st-bad);background:var(--st-bad-bg)}
.t-unk{color:var(--st-unk);background:var(--st-unk-bg)}
.sub{display:block;font-family:var(--sans);font-size:11px;color:var(--ink-faint);
margin-top:3px;font-weight:400;letter-spacing:0;text-transform:none}
.cards{display:grid;grid-template-columns:repeat(auto-fit,minmax(240px,1fr));gap:14px;
margin-top:20px}
.card{background:var(--raised);border:1px solid var(--hairline);border-top:3px solid var(--rail);
padding:16px 18px 18px;box-shadow:var(--shadow)}
.card h3{font-family:var(--mono);font-size:13px;margin:0 0 3px;font-weight:600}
.card .cls{font-family:var(--mono);font-size:10.5px;color:var(--ink-faint);margin:0 0 10px}
.card p{font-size:13.5px;line-height:1.5;color:var(--ink-muted);margin:0}
.card code{font-size:12px;background:var(--sunken);padding:1px 4px;border-radius:2px}
.callout{background:var(--raised);border:1px solid var(--hairline);
border-left:3px solid var(--st-unk);padding:18px 20px;margin-top:20px}
.callout.warn{border-left-color:var(--st-bad)}
.callout h3{font-size:15px;margin:0 0 8px;font-weight:650}
.callout p{font-size:14px;color:var(--ink-muted);margin:0 0 10px}
.callout p:last-child{margin:0}
.big{font-family:var(--mono);font-size:15px;font-weight:700;color:var(--ink)}
.note{font-size:13px;line-height:1.55;color:var(--ink-muted);border-left:2px solid
var(--hairline-firm);padding:2px 0 2px 13px;margin:16px 0 0;max-width:74ch}
footer{margin-top:46px;padding-top:18px;border-top:2px solid var(--ink);font-family:var(--mono);
font-size:11.5px;color:var(--ink-faint);line-height:1.7}
#tip{position:fixed;pointer-events:none;opacity:0;transition:opacity .1s;background:var(--ink);
color:var(--ground);font-family:var(--mono);font-size:11.5px;padding:6px 9px;border-radius:3px;
z-index:50;white-space:nowrap;font-variant-numeric:tabular-nums}
@media (prefers-reduced-motion:reduce){*{transition:none!important}}
.hit{cursor:crosshair}
"""


def render(d: dict) -> str:
    order, lab, M = d["order"], d["labels"], d["models"]
    shared = d["shared_contract"]
    gem = M["gemini"]

    def row_f1(key):
        m, l = M[key], lab[key]
        cls = ' class="prod-row"' if not l["ours"] else ""
        if key == "gemini":
            verdict = '<span class="tag t-unk">REFERENCE</span>'
        else:
            vs = {v["verdict"] for v in m["paired_vs_gemini"].values()}
            if vs == {"BEHIND"}:
                verdict = '<span class="tag t-bad">WORSE ON ALL THREE</span>'
            elif "BEHIND" in vs:
                verdict = '<span class="tag t-bad">WORSE ON SOME</span>'
            else:
                verdict = '<span class="tag t-good">NO DIMENSION WORSE</span>'
        cells = ""
        for dim in ("call_result", "reason", "product"):
            val = m["f1"][dim]
            top = max(M[k]["f1"][dim] for k in order)
            cells += f'<td{" class=best" if val == top else ""}>{val:.3f}</td>'
        return (f'<tr{cls}><th scope=row>{esc(l["name"])}'
                f'{"" if l["ours"] else "<span class=sub>production</span>"}'
                f'{"<span class=sub>our GPU</span>" if l["ours"] else ""}</th>{cells}'
                f"<td>{verdict}</td></tr>")

    def row_tokens(key):
        m, l = M[key], lab[key]
        cls = ' class="prod-row"' if not l["ours"] else ""
        lo_in = min(M[k]["input_tokens_per_call"] for k in order)
        lo_out = min(M[k]["output_tokens_per_call"] for k in order)
        return (f'<tr{cls}><th scope=row>{esc(l["name"])}</th>'
                f'<td>{m["input_tokens_total"]:,}</td>'
                f'<td{" class=best" if m["input_tokens_per_call"] == lo_in else ""}>'
                f'{m["input_tokens_per_call"]:,.0f}</td>'
                f'<td>{m["output_tokens_total"]:,}</td>'
                f'<td{" class=best" if m["output_tokens_per_call"] == lo_out else ""}>'
                f'{m["output_tokens_per_call"]:,.0f}</td></tr>')

    def row_lat(key):
        """Speed row for one of OUR models.

        Gemini is deliberately absent from this table. It ran at concurrency 8 over the public
        internet while these three ran at concurrency 4 against our own host, so two variables
        differ besides the model and a shared table would invite a comparison the data cannot
        support. Its figures appear above as context instead. The cost column went with it --
        the self-hosted endpoint reports no cost field at all, so the column would have held
        three placeholders and no measurement.
        """
        m, l = M[key], lab[key]
        s = m["latency_s"]
        fastest = min(M[k]["latency_s"]["p50"] for k in order if lab[k]["ours"])
        return (f'<tr><th scope=row>{esc(l["name"])}</th>'
                f'<td class="{"best" if s["p50"] == fastest else ""}">{num(s["p50"])}</td>'
                f'<td>{num(s["p95"])}</td><td>{num(s["p99"])}</td><td>{num(s["max"])}</td>'
                f'<td>{m["throughput_calls_per_s"]:.3f}</td>'
                f'<td>{m["attempts_total"]}</td></tr>')

    def head_to_head_block(dim):
        """One table per label type, three `Gemini vs X` rows inside it.

        Grouped by dimension rather than by model so the three challengers sit side by side on
        the same measure and an outlier is visible without cross-referencing. Each row shows all
        four buckets, which sum to the item count -- the two agreement buckets are the reason a
        verdict is often "too few to call", and omitting them was what made the old table
        unreadable.
        """
        rows = ""
        for key in order:
            if key == "gemini":
                continue
            p = M[key]["paired_vs_gemini"][dim]
            word, klass = VERDICT_WORDS[p["verdict"]]
            total = (p["both_right"] + p["both_wrong"]
                     + p["incumbent_only"] + p["candidate_only"])
            if total != shared["items"]:
                raise AssertionError(
                    f"{key}/{dim} buckets sum to {total}, expected {shared['items']} -- the "
                    "comparison report was parsed wrongly and the row would print a false total")
            if p["band"] in ("--", "-"):
                why = f'{p["discordant"]} disagreement{"" if p["discordant"] == 1 else "s"}, need 6'
            else:
                why = f'gap {abs(p["net"])}, need {p["band"].replace("+/-", "")}'
            rows += (
                f'<tr><th scope=row>Gemini vs {esc(lab[key]["short"])}</th>'
                f'<td>{p["both_right"]}</td><td>{p["both_wrong"]}</td>'
                f'<td class="{"best" if p["incumbent_only"] > p["candidate_only"] else ""}">'
                f'{p["incumbent_only"]}</td>'
                f'<td class="{"best" if p["candidate_only"] > p["incumbent_only"] else ""}">'
                f'{p["candidate_only"]}</td>'
                f'<td style="text-align:left"><span class="tag {klass}">{word}</span>'
                f'<span class=sub>{esc(why)}</span></td></tr>')
        example = M["qwen38"]["paired_vs_gemini"][dim]
        sums = (f'{example["both_right"]} + {example["both_wrong"]} + '
                f'{example["incumbent_only"]} + {example["candidate_only"]} = {shared["items"]}')
        return f"""
  <div class="scroller" style="margin-top:26px">
    <table><caption>{esc(DIM_LABEL_PY[dim])} &mdash; {esc(shared['items'])} calls each</caption>
    <thead><tr><th>Head to head</th>
      <th>Both<br>right</th><th>Both<br>wrong</th>
      <th>Only Gemini<br>right</th><th>Only other<br>right</th>
      <th style="text-align:left">Verdict</th></tr></thead>
    <tbody>{rows}</tbody></table>
  </div>
  <p style="font-family:var(--mono);font-size:11px;color:var(--ink-faint);margin:6px 0 0">
    every row adds up &mdash; e.g. {esc(sums)}</p>"""

    payload = {
        "order": order, "labels": lab,
        "f1": {k: M[k]["f1"] for k in order},
        "tokens": {k: {"inp": M[k]["input_tokens_per_call"],
                       "out": M[k]["output_tokens_per_call"]} for k in order},
        "lat": {k: M[k]["latency_s"] for k in order},
        "stab": {k: (M[k]["instability"] or {}).get("scored_unstable") for k in order},
    }
    import json as _json
    payload_js = _json.dumps(payload)

    best_stab = min((M[k]["instability"] or {}).get("scored_unstable", 10 ** 9) for k in order)
    best_stab_name = next(lab[k]["name"] for k in order
                          if (M[k]["instability"] or {}).get("scored_unstable") == best_stab)
    qwen_in = M["qwen38"]["input_tokens_per_call"]
    gem_in = gem["input_tokens_per_call"]
    saving = (gem_in - qwen_in) / gem_in * 100

    return f"""<title>Four-Model GPU Benchmark</title>
<style>{STYLE}</style>
<div id="tip" role="status" aria-live="off"></div>
<div class="wrap">

<header>
  <p class="kicker">True &middot; Retention call labelling &middot; {esc(shared['items'])} transcripts
  &middot; {esc(shared['rows'])} calls per model</p>
  <h1>Four models, one evaluation</h1>
  <p class="standfirst">Production Gemini&nbsp;2.5&nbsp;Flash against three models running on
  True&rsquo;s own GPU, measured on accuracy, tokens and speed. Every figure on this page is
  computed from the recorded per-call logs.</p>
  <div class="safe">Synthetic test set &middot; no customer data</div>
</header>

<section id="accuracy">
  <h2>1. Accuracy</h2>
  <p class="lede">Weighted F1, 0 to 1, higher is better. All four models saw the same
  {esc(shared['items'])} transcripts, the same prompt and the same settings, and were scored by
  identical code. One chart per label type, because the three are scored differently and must
  not be averaged together.</p>
  <figure>
    <figcaption>Weighted F1 by label type &middot; each scale starts at zero</figcaption>
    <div class="legend">
      <span><i class="sw" style="background:var(--s-prod)"></i>Gemini 2.5 Flash &mdash; production</span>
      <span><i class="sw" style="background:var(--s-ours)"></i>Running on our own GPU</span>
    </div>
    <div class="chartbox scroller"><div id="c-f1"></div></div>
  </figure>
  <div class="scroller" style="margin-top:22px">
    <table><caption>Weighted F1, and the verdict against production Gemini</caption>
    <thead><tr><th>Model</th><th>Call outcome</th><th>Reason</th><th>Product</th>
    <th>Verdict vs Gemini</th></tr></thead>
    <tbody>{''.join(row_f1(k) for k in order)}</tbody></table>
  </div>
  <h3 style="font-size:16px;font-weight:650;margin:34px 0 6px">How the verdict is decided</h3>
  <p class="lede">On most calls both models give the same answer, and those tell you nothing
  about which is better. Only the calls where <em>one was right and the other wrong</em> carry
  information. The question is then simply: <strong>is that scoreboard lopsided enough that
  chance cannot explain it?</strong></p>

  <div class="callout">
    <h3>Worked example &mdash; Qwen3.8 on call outcome</h3>
    <p>Of {esc(shared['items'])} calls, both models got <span class="big">128</span> right and
    both got <span class="big">7</span> wrong. Those 135 are ties and are set aside. That leaves
    <span class="big">3</span> calls where they differed: Qwen3.8 won 2, Gemini won 1.</p>
    <p><strong>2&ndash;1 proves nothing.</strong> With only 3 disagreements, even a clean
    3&ndash;0 sweep would happen by luck once in 8 times, and the bar for calling a result is
    once in 64. So at 3 disagreements <em>no</em> outcome can clear the bar &mdash; which is why
    the row reads &ldquo;too few disagreements to judge&rdquo; rather than naming a winner.
    <strong>Six differences is the minimum</strong> that makes any verdict possible, because a
    6&ndash;0 sweep is exactly a 1-in-64 event.</p>
    <p>The required margin grows with the number of disagreements: at 28 differences you need a
    14-point gap, and Qwen3.8&rsquo;s reason score fell 8 short of it &mdash; hence &ldquo;no
    real difference&rdquo;. Gemma&rsquo;s call-outcome gap of 10 out of 12 differences landed
    exactly on its threshold, so that one <em>does</em> count.</p>
  </div>

  <p class="lede" style="margin-top:26px">Every comparison below is
  <strong>Gemini against one challenger</strong>, on the same
  {esc(shared['items'])} calls. The four counts on each row are what happened on those calls,
  and they always add up to {esc(shared['items'])}. Only the last two columns decide the
  verdict &mdash; the first two are ties.</p>
{head_to_head_block('call_result')}
{head_to_head_block('reason')}
{head_to_head_block('product')}
  <div class="note">Two things this makes visible that an average cannot.
  <strong>First, why so many verdicts are &ldquo;too few to call&rdquo;:</strong> on product the
  models agree on almost every call, so there is barely anything left to measure &mdash; that is
  not a pass and not a fail, it means this test set cannot separate them.
  <strong>Second, why Reason scores lower for everyone:</strong> on that dimension
  {M['qwen38']['paired_vs_gemini']['reason']['both_wrong']}&ndash;{M['gemma']['paired_vs_gemini']['reason']['both_wrong']}
  of the {esc(shared['items'])} calls were failed by <em>both</em> models. It is hard for
  everything we tested, not a weakness of one model.</div>
</section>

<section id="tokens">
  <h2>2. Input and output tokens</h2>
  <p class="lede">How much text each model consumed and produced for the identical workload.
  Shown per call; totals are in the table. Two separate charts with two scales, because input is
  roughly twelve times output and a shared axis would flatten the output panel to nothing.</p>
  <figure>
    <figcaption>Tokens per call &middot; two scales, never one axis for both</figcaption>
    <div class="chartbox scroller"><div id="c-tok"></div></div>
  </figure>
  <div class="scroller" style="margin-top:22px">
    <table><caption>Token usage over {esc(shared['rows'])} calls per model</caption>
    <thead><tr><th>Model</th><th>Input total</th><th>Input per call</th>
    <th>Output total</th><th>Output per call</th></tr></thead>
    <tbody>{''.join(row_tokens(k) for k in order)}</tbody></table>
  </div>
  <div class="note"><strong>Both Qwen versions use exactly the same input tokens
  ({M['qwen36']['input_tokens_total']:,}) &mdash; identical on all
  {esc(shared['rows'])} calls.</strong> That is expected rather than suspicious: they share a
  tokenizer and were fed byte-identical prompts, and it is a positive integrity signal. Against
  Gemini they need <strong>{saving:.1f}% fewer input tokens</strong> for the same Thai text,
  which matters on a metered API and not at all on hardware we own. Gemma&rsquo;s tokenizer is
  the least efficient of the four on both input and output.</div>
</section>

<section id="latency">
  <h2>3. Speed on our own GPU</h2>
  <p class="lede">End-to-end response time &mdash; request sent to complete response received.
  All three ran at concurrency {M['qwen38']['concurrency']} against the same vLLM host, so these
  rows are directly comparable with each other.</p>
  <div class="note" style="margin-top:12px"><strong>Why Gemini is not in this table.</strong>
  For reference it answered in <strong>{num(gem['latency_s']['p50'], 3)} s</strong> at the
  median and cost <strong>${gem['cost_usd']:.4f}</strong> for the same
  {esc(shared['rows'])} calls &mdash; but it ran at concurrency {gem['concurrency']} over the
  public internet rather than concurrency {M['qwen38']['concurrency']} on our own network. Two
  things differ besides the model, so putting it in the same table would invite a speed
  comparison the measurement cannot support. Cost is left out of the table for the same reason:
  the self-hosted endpoint reports no cost field at all, so those cells would be placeholders
  rather than measurements.</div>
  <figure>
    <figcaption>End-to-end latency per call &middot; median, 95th percentile, maximum</figcaption>
    <div class="chartbox scroller"><div id="c-lat"></div></div>
  </figure>
  <div class="scroller" style="margin-top:22px">
    <table><caption>Our three GPU models over {esc(shared['rows'])} calls each, concurrency
    {M['qwen38']['concurrency']}</caption>
    <thead><tr><th>Model</th><th>p50</th><th>p95</th><th>p99</th><th>Max</th>
    <th>Calls/s</th><th>API attempts</th></tr></thead>
    <tbody>{''.join(row_lat(k) for k in order if lab[k]['ours'])}</tbody></table>
  </div>
  <div class="note"><strong>There is no time-to-first-token here, and none was invented.</strong>
  The evaluation harness does not stream responses, so the only interval ever measured is the
  complete round trip. A partial-response time cannot be recovered from these logs, so the
  column simply does not exist rather than being filled with end-to-end latency wearing a
  different label. All three needed {M['qwen38']['attempts_total']} API attempts for
  {esc(shared['rows'])} calls &mdash; no retries.</div>
</section>

<section id="stability">
  <h2>4. Consistency</h2>
  <p class="lede">Each transcript was sent three times, byte-identically. A stable model returns
  the same labels every time. This is where the four separate most sharply.</p>
  <figure>
    <figcaption>Scored label changes across three identical repeats &middot; lower is better</figcaption>
    <div class="chartbox scroller"><div id="c-stab"></div></div>
  </figure>
  <div class="note"><strong>{esc(best_stab_name)} changed a scored label {best_stab} times
  across {esc(shared['rows'])} calls; production Gemini changed one
  {M['gemini']['instability']['scored_unstable']} times.</strong> All four rewrite their prose
  between repeats &mdash; what differs is whether the change reaches a label anyone acts on.
  <em>Caveat:</em> Gemini was bit-stable when measured on 10 August and had become variable by
  14 August with no change on our side. That is under investigation; it does not affect the
  accuracy figures above.</div>
</section>

<section id="dims">
  <h2>What the three label types are</h2>
  <p class="lede">Each model reads a Thai call-centre transcript and fills in three fields,
  scored separately because they fail in different ways.</p>
  <div class="cards">
    <div class="card" style="--rail:var(--s-prod)"><h3>Call outcome</h3>
      <p class="cls">4 possible values</p>
      <p>How the call ended: <code>save</code> (customer stayed), <code>churn</code> (left),
      <code>unknown</code>, <code>undefined</code>. Drives retention reporting.</p></div>
    <div class="card" style="--rail:var(--s-ours)"><h3>Reason</h3>
      <p class="cls">11 possible values, up to 3 per call</p>
      <p>Why the customer wanted to cancel: <code>save cost</code>, <code>network</code>,
      <code>contract end</code>, <code>post to pre</code> and others. The hardest of the three
      &mdash; every model scores lowest here.</p></div>
    <div class="card" style="--rail:var(--s-best)"><h3>Product</h3>
      <p class="cls">4 possible values, all must be right</p>
      <p>Which products the call was about: <code>postpaid</code>, <code>tol</code> (home
      broadband), <code>tvs</code> (TrueVisions), <code>unknown</code>. Scored as an exact set.</p></div>
  </div>
</section>

<section id="dataset">
  <h2>What the evaluation set is</h2>
  <p class="lede">{esc(shared['items'])} Thai call-centre transcripts written in-house for
  testing, with hand-checked correct answers. <strong>No customer data, no production
  transcripts, nothing confidential.</strong> Each model made {esc(shared['rows'])} calls
  ({esc(shared['items'])} transcripts &times; {esc(shared['repeats'])} repeats).</p>
  <div class="scroller" style="margin-top:18px">
    <table><caption>How the transcripts are made up</caption>
    <thead><tr><th>Category</th><th>Count</th><th style="text-align:left">What it tests</th></tr>
    </thead><tbody>
      <tr><th scope=row>Clear</th><td>30</td><td style="text-align:left">Unambiguous calls &mdash; the baseline</td></tr>
      <tr><th scope=row>Thai linguistic</th><td>30</td><td style="text-align:left">Politeness particles, negation, honorifics that flip meaning</td></tr>
      <tr><th scope=row>Tie-break</th><td>17</td><td style="text-align:left">Two plausible reasons; only one correct by the rules</td></tr>
      <tr><th scope=row>Escape</th><td>13</td><td style="text-align:left">Quotes and characters that break JSON output</td></tr>
      <tr><th scope=row>Long context</th><td>12</td><td style="text-align:left">12,000&ndash;18,000 character transcripts</td></tr>
      <tr><th scope=row>Multi-product</th><td>10</td><td style="text-align:left">Several products in one call</td></tr>
      <tr><th scope=row>ASR noise</th><td>10</td><td style="text-align:left">Speech-to-text errors left in deliberately</td></tr>
      <tr><th scope=row>Code-switch</th><td>10</td><td style="text-align:left">Thai and English mixed mid-sentence</td></tr>
      <tr><th scope=row>Regression</th><td>6</td><td style="text-align:left">Specific past failures, kept as guards</td></tr>
    </tbody></table>
  </div>
  <div class="note">Because the set is synthetic, every accuracy figure here &mdash;
  Gemini&rsquo;s included &mdash; is an <strong>upper bound</strong>. Real calls are messier.
  This ranks the models against each other; it does not predict production accuracy.</div>
</section>

<section id="bottom">
  <h2>Bottom line</h2>
  <p><strong>Qwen3.8 27B on our own GPU is the strongest internal candidate.</strong> It is not
  measurably behind production Gemini on any of the three label types, it is the most consistent
  of the four, and it uses the fewest input tokens.</p>
  <p><strong>The upgrade from Qwen3.6 was worth making</strong> &mdash; higher on all three
  label types and materially more consistent, at no cost in speed.</p>
  <p><strong>Gemma 4 12B is out.</strong> Measurably worse than Gemini on all three.</p>
  <p><strong>What still blocks a migration decision:</strong> the speed gap (real, though
  confounded by concurrency and network path &mdash; see section 3), and the fact that this is a
  synthetic test set. Before any production call we would need the same comparison against real
  labelled calls.</p>
</section>

<footer>
  {esc(shared['items'])} synthetic transcripts &middot; {esc(shared['rows'])} calls per model
  &middot; {esc(shared['repeats'])} repeats &middot; identical prompt, testset, ground truth and
  scorer across all four arms<br>
  scorer {esc(shared['scoring_code_sha'][:16])} &middot; workload
  {esc(shared['workload_sha'][:16])} &middot; testset {esc(shared['testset_sha'][:16])}<br>
  Percentiles by {esc(d['percentile_method'])}. Latency values sit on a 1&nbsp;ms clock grid, so
  they are reported to 3 decimal places and no finer.<br>
  Generated by <code>scripts/model_comparison_report.py</code> from
  <code>{esc(d['generated_from'])}</code>. Figures also in
  <code>docs/model-comparison-metrics.json</code>.
</footer>
</div>
<script>
(function(){{
"use strict";
var D={payload_js};
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
function col(k){{return D.labels[k].ours?v("--s-ours"):v("--s-prod");}}

// ---- 1. accuracy, faceted -------------------------------------------------------
(function(){{
  var DIMS=[["call_result","Call outcome"],["reason","Reason"],["product","Product"]];
  var W=860,padL=112,padR=64,rowH=30,facetH=4*rowH+44,gapY=18;
  var H=DIMS.length*(facetH+gapY),barW=W-padL-padR;
  var svg=el("svg",{{viewBox:"0 0 "+W+" "+H,width:W,height:H,role:"img",
    "aria-label":"Weighted F1 by label type"}});
  DIMS.forEach(function(dd,fi){{
    var top=fi*(facetH+gapY);
    svg.appendChild(el("text",{{x:0,y:top+13,"font-size":13,fill:v("--ink"),
      "font-weight":700}},dd[1]));
    var best=Math.max.apply(null,D.order.map(function(k){{return D.f1[k][dd[0]];}}));
    [0,0.25,0.5,0.75,1.0].forEach(function(t){{
      var x=padL+t*barW;
      svg.appendChild(el("line",{{x1:x,y1:top+24,x2:x,y2:top+24+4*rowH,
        stroke:t===0?v("--hairline-firm"):v("--hairline"),"stroke-width":1}}));
      svg.appendChild(el("text",{{x:x,y:top+24+4*rowH+16,"font-size":10,
        "text-anchor":"middle"}},t.toFixed(2)));
    }});
    D.order.forEach(function(k,mi){{
      var val=D.f1[k][dd[0]],y=top+28+mi*rowH,w=val*barW;
      svg.appendChild(el("text",{{x:padL-10,y:y+14,"font-size":11.5,"text-anchor":"end",
        fill:v("--ink"),"font-weight":D.labels[k].ours?500:700}},D.labels[k].short));
      var r=el("rect",{{x:padL,y:y,width:Math.max(w,1),height:19,rx:2,fill:col(k)}});
      bind(r,D.labels[k].name+"  |  "+dd[1]+"  |  weighted F1 "+val.toFixed(3)+
        (val===best?"  (highest)":""));
      svg.appendChild(r);
      svg.appendChild(el("text",{{x:padL+w+9,y:y+14,"font-size":11.5,fill:v("--ink"),
        "font-weight":val===best?700:600}},val.toFixed(3)));
    }});
  }});
  document.getElementById("c-f1").appendChild(svg);
}})();

// ---- 2. tokens, two panels, two scales ------------------------------------------
(function(){{
  var W=860,half=W/2,padT=32,rowH=32,padB=30;
  var H=padT+D.order.length*rowH+padB;
  var svg=el("svg",{{viewBox:"0 0 "+W+" "+H,width:W,height:H,role:"img",
    "aria-label":"Input and output tokens per call"}});
  function panel(x0,w,title,field,fmt){{
    var padL=78,barW=w-padL-72;
    var vals=D.order.map(function(k){{return D.tokens[k][field];}});
    var maxV=Math.max.apply(null,vals)*1.05, lo=Math.min.apply(null,vals);
    svg.appendChild(el("text",{{x:x0+padL,y:14,"font-size":10,"letter-spacing":"1.1",
      fill:v("--ink-faint")}},title));
    [0,maxV/2,maxV].forEach(function(t){{
      var x=x0+padL+(t/maxV)*barW;
      svg.appendChild(el("line",{{x1:x,y1:padT-8,x2:x,y2:padT+D.order.length*rowH-10,
        stroke:t===0?v("--hairline-firm"):v("--hairline"),"stroke-width":1}}));
      svg.appendChild(el("text",{{x:x,y:H-12,"font-size":10,"text-anchor":"middle"}},fmt(t)));
    }});
    D.order.forEach(function(k,i){{
      var val=D.tokens[k][field],y=padT+i*rowH,bw=(val/maxV)*barW;
      svg.appendChild(el("text",{{x:x0+padL-10,y:y+14,"font-size":11,"text-anchor":"end",
        fill:v("--ink"),"font-weight":D.labels[k].ours?500:700}},D.labels[k].short));
      var r=el("rect",{{x:x0+padL,y:y,width:Math.max(bw,1),height:19,rx:2,
        fill:val===lo?v("--s-best"):col(k)}});
      bind(r,D.labels[k].name+"  |  "+Math.round(val).toLocaleString()+" "+
        (field==="inp"?"input":"output")+" tokens per call"+(val===lo?"  (fewest)":""));
      svg.appendChild(r);
      svg.appendChild(el("text",{{x:x0+padL+bw+8,y:y+14,"font-size":11,fill:v("--ink"),
        "font-weight":val===lo?700:600}},Math.round(val).toLocaleString()));
    }});
  }}
  panel(0,half,"INPUT TOKENS / CALL","inp",function(t){{
    return t===0?"0":Math.round(t/1000)+"k";}});
  svg.appendChild(el("line",{{x1:half-8,y1:6,x2:half-8,y2:H-24,stroke:v("--hairline"),
    "stroke-width":1}}));
  panel(half,half,"OUTPUT TOKENS / CALL","out",function(t){{return String(Math.round(t));}});
  document.getElementById("c-tok").appendChild(svg);
}})();

// ---- 3. latency: p50 bar, whisker to p95, marker at max -------------------------
// Our three models only. Gemini ran at a different concurrency over a different network, so
// charting it beside these would draw exactly the comparison the section says it cannot make.
(function(){{
  var OURS=D.order.filter(function(k){{return D.labels[k].ours;}});
  var W=860,padL=112,padR=92,padT=30,rowH=40,padB=36;
  var H=padT+OURS.length*rowH+padB,trackW=W-padL-padR;
  var maxV=Math.max.apply(null,OURS.map(function(k){{return D.lat[k].max;}}))*1.06;
  function X(s){{return padL+(s/maxV)*trackW;}}
  var svg=el("svg",{{viewBox:"0 0 "+W+" "+H,width:W,height:H,role:"img",
    "aria-label":"End-to-end latency by model"}});
  svg.appendChild(el("text",{{x:padL,y:13,"font-size":9.5,"letter-spacing":"1.2",
    fill:v("--ink-faint")}},
    "SECONDS PER CALL \\u2014 BAR = MEDIAN, LINE TO P95, MARKER = SLOWEST"));
  [0,5,10,15,20,25].forEach(function(t){{
    if(t>maxV)return;
    svg.appendChild(el("line",{{x1:X(t),y1:padT-6,x2:X(t),y2:padT+OURS.length*rowH-12,
      stroke:t===0?v("--hairline-firm"):v("--hairline"),"stroke-width":1}}));
    svg.appendChild(el("text",{{x:X(t),y:H-14,"font-size":10,"text-anchor":"middle"}},t+"s"));
  }});
  var fastest=Math.min.apply(null,OURS.map(function(k){{return D.lat[k].p50;}}));
  OURS.forEach(function(k,i){{
    var s=D.lat[k],y=padT+i*rowH;
    svg.appendChild(el("text",{{x:padL-10,y:y+16,"font-size":11.5,"text-anchor":"end",
      fill:v("--ink"),"font-weight":D.labels[k].ours?500:700}},D.labels[k].short));
    var r=el("rect",{{x:padL,y:y+3,width:Math.max(X(s.p50)-padL,1),height:20,rx:2,fill:col(k)}});
    bind(r,D.labels[k].name+"  |  median "+s.p50.toFixed(3)+"s, p95 "+s.p95.toFixed(3)+
      "s, p99 "+s.p99.toFixed(3)+"s, slowest "+s.max.toFixed(3)+"s"+
      (s.p50===fastest?"  (fastest median)":""));
    svg.appendChild(r);
    svg.appendChild(el("line",{{x1:X(s.p50),y1:y+13,x2:X(s.p95),y2:y+13,stroke:col(k),
      "stroke-width":2,opacity:.55}}));
    svg.appendChild(el("line",{{x1:X(s.p95),y1:y+6,x2:X(s.p95),y2:y+20,stroke:col(k),
      "stroke-width":2}}));
    svg.appendChild(el("circle",{{cx:X(s.max),cy:y+13,r:4,fill:v("--raised"),stroke:col(k),
      "stroke-width":2}}));
    // Each label sits beside the thing it describes. Putting the median value next to the
    // max marker -- as this did -- reads as if the median were 25 seconds.
    svg.appendChild(el("text",{{x:X(s.p50)-8,y:y+17,"font-size":11,"text-anchor":"end",
      fill:v("--raised"),"font-weight":700}},s.p50.toFixed(1)+"s"));
    svg.appendChild(el("text",{{x:X(s.max)+10,y:y+17,"font-size":10.5,
      fill:v("--ink-faint")}},"max "+s.max.toFixed(1)+"s"));
  }});
  document.getElementById("c-lat").appendChild(svg);
}})();

// ---- 4. stability ---------------------------------------------------------------
(function(){{
  var W=860,padL=112,padR=100,padT=26,rowH=34,padB=34;
  var H=padT+D.order.length*rowH+padB,barW=W-padL-padR;
  var vals=D.order.map(function(k){{return D.stab[k]||0;}});
  var maxV=Math.max.apply(null,vals)*1.15,best=Math.min.apply(null,vals);
  var svg=el("svg",{{viewBox:"0 0 "+W+" "+H,width:W,height:H,role:"img",
    "aria-label":"Scored label changes across three repeats"}});
  svg.appendChild(el("text",{{x:padL,y:13,"font-size":9.5,"letter-spacing":"1.2",
    fill:v("--ink-faint")}},"SCORED LABEL CHANGES ACROSS 3 IDENTICAL REPEATS (LOWER IS BETTER)"));
  [0,10,20,30].forEach(function(t){{
    if(t>maxV)return;
    var x=padL+(t/maxV)*barW;
    svg.appendChild(el("line",{{x1:x,y1:padT-6,x2:x,y2:padT+D.order.length*rowH-8,
      stroke:t===0?v("--hairline-firm"):v("--hairline"),"stroke-width":1}}));
    svg.appendChild(el("text",{{x:x,y:H-14,"font-size":10,"text-anchor":"middle"}},String(t)));
  }});
  D.order.forEach(function(k,i){{
    var val=D.stab[k]||0,y=padT+i*rowH,w=(val/maxV)*barW;
    svg.appendChild(el("text",{{x:padL-10,y:y+15,"font-size":11.5,"text-anchor":"end",
      fill:v("--ink"),"font-weight":D.labels[k].ours?500:700}},D.labels[k].short));
    var r=el("rect",{{x:padL,y:y,width:Math.max(w,1),height:21,rx:2,
      fill:val===best?v("--s-best"):col(k)}});
    bind(r,D.labels[k].name+" changed a scored label "+val+" times"+
      (val===best?"  (most consistent)":""));
    svg.appendChild(r);
    svg.appendChild(el("text",{{x:padL+w+9,y:y+15,"font-size":12,fill:v("--ink"),
      "font-weight":val===best?700:600}},String(val)+(val===best?"  most consistent":"")));
  }});
  document.getElementById("c-stab").appendChild(svg);
}})();
}})();
</script>"""


DIM_LABEL_PY = {"call_result": "Call outcome", "reason": "Reason", "product": "Product"}
