"""One page covering both Retention evaluation sets, in the per-set report's own template.

    PYTHONPATH=src python scripts/combined_report.py

WHY THIS EXISTS

There are two published reports, one per set, and no single document answering "how do the
models we host compare to production Gemini". This is that document. It is a third VIEW over
data that has already been computed and verified; it is not a third computation.

IT RECOMPUTES NOTHING. Every measured figure is copied from one of the two published metrics
files:

    docs/reports/model-comparison-metrics.json            retention_v3,           138 items
    docs/reports/model-comparison-challenge-metrics.json  retention_challenge_v1,  50 items

Both were independently re-derived from the raw per-call logs by verification workflows -- F1
values, paired verdicts and the dashboard's own atoms all agreed to floating point. A combined
page that recalculated would be a third derivation with a third chance to be wrong.

IT DRAWS THE SAME CHARTS. `model_comparison_html.charts_js` is the per-set report's own chart
code, extracted so it can be called once per set here. Growing a second chart implementation
would guarantee the two reports drifted apart; extracting the first guarantees they cannot.
The extraction is verified: the per-set page regenerates byte-identical apart from the four
scoped container ids.

THE FIVE THINGS THAT WOULD MAKE A COMBINED REPORT WRONG

  1. POOLING. Different testsets, different denominators (call_result 150 vs 64; product 138
     vs 50; reason 157-164 vs 65-66, varying PER MODEL because over-labelling inflates it) and
     different difficulty. Averaging 0.966 and 0.930 into "0.948" describes a measurement
     nobody made. Every figure is shown under its own set and never merged.

  2. THE STABILITY CONFOUND. Gemini's scored-unstable is 29 on set A and 0 on set B -- but the
     set changed at the same time as the date. Worse, set A's own arms are not one day either:
     Gemini, Qwen3.6 and Gemma ran 2026-08-14, Qwen3.8 ran 2026-08-15. The comparison this
     report leans on hardest crosses the boundary it identifies as a confound, so it says so
     next to the table AND next to the results.

  3. TWO DIFFERENT ABSENCES. Gemma is missing from set B because its vLLM backend returned
     HTTP 500 on all 150 calls -- an outage; its accuracy there is unmeasured. Qwen3.6 is
     missing because it was never in that comparison and has since been replaced on the host.
     One marker for both would transfer the failure explanation to a model that never failed.

  4. UNDERPOWERED READ AS A TIE. A verdict travels inside every F1 cell, because on set B the
     raw numbers favour Gemini on call outcome while the two models disagreed on ONE call.

  5. UNCONTROLLED VARIABLES PRESENTED AS CONTROLLED. The reference came through OpenRouter, the
     Qwen arms are fp8, and reasoning effort differs. Quantisation and reasoning effort can
     move accuracy, so this is a comparison of models AS DEPLOYED, and the page says that
     rather than implying the arms differ only by model.

Also carried deliberately: `cost_usd: null` means the endpoint reports no cost, not that the
run was free, so it renders as NOT METERED and is never summed; costs are RUN TOTALS over all
repeats, not per-call; and F1 is scored on REPLICATE 1 ONLY (cli.py:25-31) -- the other two
replicates earn their keep in the consistency section.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "scripts"))

from model_comparison_html import (  # noqa: E402
    STYLE, VERDICT_WORDS, charts_js, esc, num,
)

DOCS = REPO / "docs" / "reports"
PACK_A = DOCS / "model-comparison-metrics.json"
PACK_B = DOCS / "model-comparison-challenge-metrics.json"
OUT_STEM = "model-comparison-combined"

DIMS = ("call_result", "reason", "product")
DIM_LABEL = {"call_result": "Call outcome", "reason": "Reason", "product": "Product"}

# Two absences, two causes. See the module docstring.
ABSENT = {
    "gemma": ("not measured", "backend outage &mdash; all 150 calls returned HTTP 500"),
    "qwen36": ("not run", "never in this comparison; since replaced on the host"),
}
FULL_NAME = {"gemma": "Gemma 4 12B", "qwen36": "Qwen3.6 27B"}


# Plain-English names for the two sets. `retention_v3` and `retention_challenge_v1` are file
# names, not descriptions -- a reader seeing "retention_v3 - 138 calls" on eight captions
# learns nothing about what separates the two sets. The technical id stays available in the
# footer and in the run index, so nothing becomes untraceable.
SET_NAME = {
    "retention_v3": "the coverage set",
    "retention_challenge_v1": "the messy-conversation set",
}
SET_TITLE = {
    "retention_v3": "The coverage set",
    "retention_challenge_v1": "The messy-conversation set",
}
SET_ONELINER = {
    "retention_v3": "one of everything that can go wrong, taken a fault at a time",
    "retention_challenge_v1": "calls where the conversation itself is a mess &mdash; the "
                              "customer has called before, changes their mind, gets "
                              "interrupted",
}


def set_label(doc: dict, tag: str) -> str:
    """`Set A - the everyday set - 138 calls`, with the file name kept small beside it."""
    pack = doc["pack"]
    return (f'Set {tag} &middot; {SET_TITLE[pack]} &middot; '
            f'{doc["shared_contract"]["items"]} calls '
            f'<span class="packid">{esc(pack)}</span>')


def set_cap(doc: dict, tag: str) -> str:
    """Short form for a table caption."""
    return f'Set {tag} &middot; {SET_NAME[doc["pack"]]}'


def run_date(run_id: str) -> str:
    """`20260814-132425Z-e17-gemini` -> `2026-08-14`."""
    return f"{run_id[0:4]}-{run_id[4:6]}-{run_id[6:8]}"


class Refused(SystemExit):
    def __init__(self, why: str) -> None:
        super().__init__(f"REFUSING to write the combined report: {why}")


def load() -> tuple[dict, dict]:
    a = json.loads(PACK_A.read_text(encoding="utf-8"))
    b = json.loads(PACK_B.read_text(encoding="utf-8"))

    # REFUSAL 1. Same scorer and same prompt is what makes this ONE evaluation across two sets
    # rather than two unrelated exercises. It is the only cross-set claim the data supports.
    for field in ("scoring_code_sha", "prompt_sha", "repeats"):
        if a["shared_contract"][field] != b["shared_contract"][field]:
            raise Refused(
                f"the sets disagree on {field}. They are then two unrelated measurements and "
                "this page's premise is wrong.")

    # REFUSAL 2. If the testsets matched, this is one set twice wearing two labels.
    if a["shared_contract"]["testset_sha"] == b["shared_contract"]["testset_sha"]:
        raise Refused("both sets carry the same testset_sha; this is one set twice.")

    # REFUSAL 3. Every paired row must sum to ITS OWN set's item count -- the arithmetic that
    # catches a figure rendered against the wrong set.
    for tag, doc in (("A", a), ("B", b)):
        items = doc["shared_contract"]["items"]
        for key, m in doc["models"].items():
            for dim, p in (m.get("paired_vs_gemini") or {}).items():
                total = (p["both_right"] + p["both_wrong"]
                         + p["incumbent_only"] + p["candidate_only"])
                if total != items:
                    raise Refused(f"set {tag}, {key}/{dim}: buckets sum to {total}, not {items}")
    return a, b


def payload_for(doc: dict, stab_max: int | None = None) -> dict:
    """The shape `charts_js` expects. Same fields the per-set report builds."""
    order = doc["order"]
    return {
        "order": order,
        "labels": doc["labels"],
        "f1": {k: doc["models"][k]["f1"] for k in order},
        "tokens": {k: {"inp": doc["models"][k]["input_tokens_per_call"],
                       "out": doc["models"][k]["output_tokens_per_call"]} for k in order},
        "lat": {k: doc["models"][k]["latency_s"] for k in order},
        "stab": {k: (doc["models"][k]["instability"] or {}).get("scored_unstable")
                 for k in order},
        # Both sets' consistency charts share one ceiling. Scaled independently, set B's worst
        # arm (2 changes) drew the same bar as set A's worst (29) with every tick above zero
        # suppressed -- the reader saw two identical bars meaning wildly different things.
        "stab_max": stab_max,
    }


def absent_rows(doc: dict, cols: int) -> str:
    """A row per model that is not in this set, each carrying its OWN reason."""
    out = ""
    for key, (short, why) in ABSENT.items():
        if key in doc["models"]:
            continue
        out += (f'<tr class="absent-row"><th scope=row>{FULL_NAME[key]}'
                f'<span class="who">our GPU</span></th>'
                f'<td colspan={cols} class="absent">{short} &mdash; {why}</td></tr>')
    return out


def f1_rows(doc: dict) -> str:
    lab = doc["labels"]
    rows = ""
    for key in doc["order"]:
        cells = ""
        for dim in DIMS:
            v = doc["models"][key]["f1"][dim]
            p = (doc["models"][key].get("paired_vs_gemini") or {}).get(dim)
            if p:
                word, klass = VERDICT_WORDS[p["verdict"]]
                tag = f'<span class="tag {klass}">{word}</span>'
            elif key == doc["incumbent"]:
                tag = '<span class="tag t-ref">REFERENCE</span>'
            else:
                tag = ""
            cells += f'<td><span class="v">{v:.3f}</span>{tag}</td>'
        rows += (f'<tr{"" if lab[key]["ours"] else " class=prod-row"}>'
                 f'<th scope=row>{esc(lab[key]["name"])}'
                 f'<span class="who">{"our GPU" if lab[key]["ours"] else "production"}</span>'
                 f'</th>{cells}</tr>')
    return rows + absent_rows(doc, 3)


def paired_rows(doc: dict) -> str:
    lab, inc = doc["labels"], doc["incumbent"]
    body = ""
    for key in doc["order"]:
        p = doc["models"][key].get("paired_vs_gemini")
        if not p:
            continue
        for dim in DIMS:
            e = p[dim]
            word, klass = VERDICT_WORDS[e["verdict"]]
            need = (e["band"] or "").lstrip("+/-")
            why = (f'{e["discordant"]} disagreement{"s" if e["discordant"] != 1 else ""}, need 6'
                   if e["verdict"] == "UNDERPOWERED"
                   else f'gap {abs(e["net"])}, need {need}')
            body += (
                f'<tr><th scope=row>{esc(lab[inc]["short"])} vs {esc(lab[key]["short"])}</th>'
                f'<td>{esc(DIM_LABEL[dim])}</td>'
                f'<td>{e["both_right"]}</td><td>{e["both_wrong"]}</td>'
                f'<td>{e["incumbent_only"]}</td><td>{e["candidate_only"]}</td>'
                f'<td style="text-align:left"><span class="tag {klass}">{word}</span>'
                f'<span class=sub>{esc(why)}</span></td></tr>')
    return body


def token_rows(doc: dict) -> str:
    lab = doc["labels"]
    out = ""
    for key in doc["order"]:
        m = doc["models"][key]
        out += (f'<tr><th scope=row>{esc(lab[key]["name"])}</th>'
                f'<td>{m["input_tokens_total"]:,}</td>'
                f'<td>{m["input_tokens_per_call"]:,.1f}</td>'
                f'<td>{m["output_tokens_total"]:,}</td>'
                f'<td>{m["output_tokens_per_call"]:,.1f}</td></tr>')
    return out + absent_rows(doc, 4)


def speed_rows(doc: dict) -> str:
    """Our own models only -- the incumbent ran at a different concurrency over a different
    network, so charting or tabling it here would draw exactly the comparison the section says
    the measurement cannot support."""
    lab = doc["labels"]
    out = ""
    for key in doc["order"]:
        if not lab[key]["ours"]:
            continue
        m = doc["models"][key]
        out += (f'<tr><th scope=row>{esc(lab[key]["name"])}</th>'
                f'<td>{num(m["latency_s"]["p50"])}</td><td>{num(m["latency_s"]["p95"])}</td>'
                f'<td>{num(m["latency_s"]["p99"])}</td><td>{num(m["latency_s"]["max"])}</td>'
                f'<td>{m["throughput_calls_per_s"]}</td><td>{m["attempts_total"]}</td></tr>')
    return out


def stab_rows(doc: dict) -> str:
    lab = doc["labels"]
    out = ""
    for key in doc["order"]:
        i = doc["models"][key]["instability"] or {}
        out += (f'<tr><th scope=row>{esc(lab[key]["name"])}</th>'
                f'<td>{i.get("raw_unstable")} / {i.get("observable")}</td>'
                f'<td><span class="v">{i.get("scored_unstable")}</span></td>'
                f'<td>{doc["models"][key]["n_flip"]}</td></tr>')
    return out + absent_rows(doc, 3)


def families_rows(doc: dict) -> str:
    return "".join(
        f'<tr><th scope=row>{esc(f["name"])}</th><td>{f["count"]}</td>'
        f'<td style="text-align:left">{esc(f["what"])}</td></tr>'
        for f in doc["dataset"]["families"])


EXTRA_CSS = """
.who{display:block;font-family:var(--sans);font-size:10.5px;font-weight:400;
color:var(--ink-faint);text-transform:uppercase;letter-spacing:.06em;margin-top:2px}
.v{font-family:var(--mono);font-size:14px;font-weight:600;font-variant-numeric:tabular-nums;
display:block;margin-bottom:5px}
.absent{color:var(--ink-faint);font-style:italic;font-family:var(--sans);font-size:12.5px}
.absent-row th{color:var(--ink-faint)}
.t-ref{color:var(--ink-muted);background:var(--sunken)}
.packid{font-family:var(--mono);font-size:9.5px;letter-spacing:0;text-transform:none;
color:var(--ink-faint);opacity:.75;margin-left:8px}
.setlab{display:inline-block;font-family:var(--mono);font-size:10px;letter-spacing:.12em;
text-transform:uppercase;color:var(--ink-faint);border:1px solid var(--hairline);
padding:3px 8px;border-radius:2px;margin:26px 0 10px}
.packs{display:grid;gap:16px;grid-template-columns:repeat(auto-fit,minmax(320px,1fr));
margin-top:18px}
.pack{background:var(--raised);border:1px solid var(--hairline);padding:16px 18px}
.pack h4{margin:0 0 4px;font-size:14.5px;font-weight:650}
.pack .n{font-family:var(--mono);font-size:11px;color:var(--ink-faint);
letter-spacing:.06em;text-transform:uppercase}
.pack p{font-size:13.5px;color:var(--ink-muted);margin:8px 0 0}
"""


def render(a: dict, b: dict) -> str:
    sa, sb = a["shared_contract"], b["shared_contract"]
    ga, gb = a["models"]["gemini"], b["models"]["gemini"]
    qa, qb = a["models"]["qwen38"], b["models"]["qwen38"]
    gem, q36 = a["models"]["gemma"], a["models"]["qwen36"]
    h2h = a["qwen36_vs_qwen38"]

    stab_ceiling = max(
        (m["instability"] or {}).get("scored_unstable") or 0
        for doc in (a, b) for m in doc["models"].values())
    chart_a = charts_js(payload_for(a, stab_ceiling), "-a")
    chart_b = charts_js(payload_for(b, stab_ceiling), "-b")

    # Counted over the rows this page RENDERS -- not over the JSONs, which carry three more
    # (the Qwen3.6-vs-Qwen3.8 head-to-head) that appear nowhere here.
    paired_n = sum(len(m["paired_vs_gemini"]) for doc in (a, b)
                   for m in doc["models"].values() if m.get("paired_vs_gemini"))
    underpowered = sum(1 for doc in (a, b) for m in doc["models"].values()
                       for e in (m.get("paired_vs_gemini") or {}).values()
                       if e["verdict"] == "UNDERPOWERED")

    return f"""<title>Retention Labelling Evaluation</title>
<style>{STYLE}{EXTRA_CSS}</style>
<div id="tip" role="status" aria-live="off"></div>
<div class="wrap">

<header>
  <p class="kicker">True &middot; Model evaluation &middot; Retention labelling &middot; two
  test sets &middot; {sa['items']} + {sb['items']} evaluation test cases</p>
  <h1>Model evaluation &mdash; can we run this labelling job on our own GPUs?</h1>
  <p class="standfirst">Production Gemini&nbsp;2.5&nbsp;Flash against models running on
  True&rsquo;s own hardware. Marked on accuracy, tokens, speed and consistency, over two sets of
  <strong>evaluation test cases we wrote ourselves</strong>: <strong>a broad coverage set</strong>
  and <strong>deliberately messy ones</strong>. Every figure comes from the recorded logs of this
  evaluation.</p>
  <div class="safe">An evaluation on written test cases &middot; no production traffic &middot;
  no customer data</div>
</header>

<section id="sets">
  <h2>1. What we tested it on</h2>
  <p class="lede">We wrote two sets of <strong>evaluation test cases</strong>. Each test case is
  a Thai call-centre transcript we made up, with the right answers filled in by hand, so we can
  mark a model's homework against them. <strong>Nothing here is a production call</strong>: no
  customer data, no real transcripts, nothing confidential.</p>
  <p class="lede"><em>A note on wording.</em> Below, &ldquo;call&rdquo; means the conversation a
  test case depicts &mdash; never a real one. Everything that was actually run was an evaluation
  test case.</p>
  <p class="lede">Two sets rather than one because a model can look good on ordinary calls and
  fall apart on awkward ones. <strong>One caveat on that logic, up front:</strong> the two sets
  were run on different days, and the Gemini we measured against did not behave the same way on
  both (section&nbsp;6). Read the second set as a second attempt to find a difference, not as
  independent confirmation of the first.</p>
  <div class="packs">
    <div class="pack"><span class="n">Set A &middot; {sa['items']} test cases
      <span class="packid">{esc(a['pack'])}</span></span>
      <h4>{SET_TITLE[a['pack']]}</h4>
      <p><strong>One of everything that can go wrong.</strong> Thirty straightforward calls as a
      baseline, and then <strong>eight</strong> deliberate faults, each isolated so a failure can
      be blamed on a cause: Thai negation and who-said-what (a save-shaped phrase spoken by the
      agent, not the customer), speech-to-text garble left in on purpose, Thai and English mixed
      mid-sentence, quotes that break JSON output, two plausible reasons where only one is
      correct, and long transcripts &mdash; up to about 15,600 characters.</p>
      <p>If a model cannot do this set, it cannot do the job.</p></div>
    <div class="pack"><span class="n">Set B &middot; {sb['items']} test cases
      <span class="packid">{esc(b['pack'])}</span></span>
      <h4>{SET_TITLE[b['pack']]}</h4>
      <p><strong>The sentences are easy; the conversation is the problem.</strong> Five
      situations, ten calls each. The customer rang last week and that changes the answer. They
      agree to stay, then change their mind. They get put on hold, transferred, interrupted, and
      wander off topic. They discuss several products in one call, ending differently on each.
      And they negotiate an ending that sits right on the line between two labels.</p>
      <p>Every call is exactly 18 turns, and more of them carry several products &mdash; 11 of
      these 50, against 8 of the 138 in set&nbsp;A. Doing well here means following a
      conversation, not just reading a sentence.</p></div>
  </div>
  <p style="margin-top:26px">Here is exactly what is in each set, and why each part is there.</p>
  <div class="scroller">
    <table><caption>{set_cap(a, "A")} &middot; how the {sa['items']} transcripts are made up</caption>
    <thead><tr><th>What it stresses</th><th>Calls</th>
    <th style="text-align:left">Why it is in the set</th></tr></thead>
    <tbody>{families_rows(a)}</tbody></table>
  </div>
  <div class="scroller" style="margin-top:20px">
    <table><caption>{set_cap(b, "B")} &middot; how the {sb['items']} transcripts are made up</caption>
    <thead><tr><th>What it stresses</th><th>Calls</th>
    <th style="text-align:left">Why it is in the set</th></tr></thead>
    <tbody>{families_rows(b)}</tbody></table>
  </div>
  <div class="note">Because both sets are synthetic, every accuracy figure here &mdash;
  Gemini&rsquo;s included &mdash; is an <strong>upper bound</strong>. Real calls are messier.
  This ranks the models against each other; it does not predict production accuracy.</div>
  <div class="note"><strong>What was held constant, and what was not.</strong> Held constant:
  the prompt, the ground truth, the scoring code and {sa['repeats']} repeats. Not held constant
  &mdash; the reference was reached through OpenRouter rather than the production path, so its
  latency and price are a router's; the Qwen arms are fp8-quantised builds
  (<code>{esc(qa['model_requested'])}</code>) while Gemma is
  <code>{esc(gem['model_requested'])}</code>; and reasoning effort is
  <code>{esc(ga['reasoning_effort'])}</code> on the reference against
  <code>{esc(qa['reasoning_effort'])}</code> on ours. <strong>Quantisation and reasoning effort
  can move accuracy, so read this as a comparison of models <em>as deployed</em></strong>
  &mdash; the operationally useful question, but a different serving choice could change these
  numbers.</div>
</section>

<section id="labels">
  <h2>2. What each model had to get right</h2>
  <p class="lede">For every call the model reads the transcript and fills in three things.
  Each is marked separately, and they are never rolled into a single score &mdash; they are
  counted over different sets of rows, so an average of the three would not mean anything.</p>
  <div class="cards">
    <div class="card"><h3>Call outcome</h3><p class="cls">save / churn / unknown / undefined</p>
    <p>What the call ended as. Marked once per product, so a call about two products is marked
    twice &mdash; which is why set&nbsp;A has
    <strong>{ga['scoring_detail']['call_result']['denominator']}</strong> things to mark across
    {sa['items']} test cases, and set&nbsp;B
    <strong>{gb['scoring_detail']['call_result']['denominator']}</strong> across
    {sb['items']}.</p></div>
    <div class="card"><h3>Reason</h3><p class="cls">11 values, up to 3 per call</p>
    <p>Why. Marked over every row the model produced as well as every row it should have, so
    the number of things to mark <em>moves with the model</em>
    ({ga['scoring_detail']['reason']['denominator']}&ndash;{gem['scoring_detail']['reason']['denominator']}
    in set&nbsp;A): a model that over-labels adds rows to its own denominator.</p></div>
    <div class="card"><h3>Product</h3><p class="cls">postpaid / TOL / TVS / unknown</p>
    <p>Which product. Scored per call as an exact set &mdash; two right out of three counts as
    wrong. <strong>{ga['scoring_detail']['product']['denominator']}</strong> and
    <strong>{gb['scoring_detail']['product']['denominator']}</strong>.</p></div>
  </div>
  <div class="note">Each transcript was sent <strong>{sa['repeats']} times</strong>. The accuracy
  figures are scored on the <strong>first run only</strong>; the other two are what
  section&nbsp;6 measures. An F1 averaged over repeats would hide exactly the variation that
  section exists to show.</div>
</section>

<section id="accuracy">
  <h2>3. Accuracy</h2>
  <p class="lede">Weighted F1, 0 to 1, higher is better. One chart per label type, because the
  three are scored over different rows and must not be averaged together. <strong>The two sets
  are shown separately and are never combined</strong> &mdash; different test data, different
  denominators, different difficulty, so a score from one is not on the same scale as a score
  from the other.</p>
  <div class="legend">
    <span><i class="sw" style="background:var(--s-prod)"></i>{esc(a['labels']['gemini']['name'])}
    &mdash; production</span>
    <span><i class="sw" style="background:var(--s-ours)"></i>Running on our own GPU</span>
  </div>

  <p class="setlab">{set_label(a, "A")}</p>
  <figure>
    <figcaption>Weighted F1 by label type &middot; each scale starts at zero</figcaption>
    <div class="chartbox scroller"><div id="c-f1-a"></div></div>
  </figure>
  <div class="scroller" style="margin-top:20px">
    <table><caption>{set_cap(a, "A")} &middot; weighted F1, scored on replicate&nbsp;1 of {sa['repeats']}
    &middot; verdict is against production</caption>
    <thead><tr><th>Model</th><th>Call outcome</th><th>Reason</th><th>Product</th></tr></thead>
    <tbody>{f1_rows(a)}</tbody></table>
  </div>
  <div class="note">Three of the four set&nbsp;A arms ran {run_date(ga['run_id'])};
  <strong>Qwen3.8 ran {run_date(qa['run_id'])}</strong>. That matters because the reference
  moved between those two dates &mdash; see section&nbsp;6.</div>

  <p class="setlab">{set_label(b, "B")}</p>
  <figure>
    <figcaption>Weighted F1 by label type &middot; each scale starts at zero</figcaption>
    <div class="chartbox scroller"><div id="c-f1-b"></div></div>
  </figure>
  <div class="scroller" style="margin-top:20px">
    <table><caption>{set_cap(b, "B")} &middot; weighted F1, scored on replicate&nbsp;1 of {sb['repeats']}
    &middot; verdict is against production</caption>
    <thead><tr><th>Model</th><th>Call outcome</th><th>Reason</th><th>Product</th></tr></thead>
    <tbody>{f1_rows(b)}</tbody></table>
  </div>
  <div class="note"><strong>Read the verdict, not the decimal.</strong> On set&nbsp;B Gemini
  leads call outcome {num(gb['f1']['call_result'])} to {num(qb['f1']['call_result'])} &mdash;
  but the two models disagreed on <strong>exactly one call out of {sb['items']}</strong>, and on
  product they disagreed on <strong>none at all</strong>. A gap that rests on one call is not a
  result, which is what the next block is for.</div>

  <h3 style="font-size:16px;font-weight:650;margin:34px 0 6px">How the verdict is decided</h3>
  <p class="lede">On most calls both models give the same answer, and those say nothing about
  which is better. Only the calls where <em>one was right and the other wrong</em> carry
  information.</p>
  <p class="lede">The test is the one you would do by hand: if the models disagreed on ten calls
  and one model won nine of them, is that skill or luck? We call it real only if a split that
  lopsided would happen <strong>less than once in 64 times</strong> by chance &mdash; the odds of
  six coin flips all landing the same way. That has a consequence worth knowing:
  <strong>below six disagreements no result can clear the bar</strong>, however one-sided, and
  the margin needed grows with the number of disagreements. Each row below shows its own
  threshold, so you can check it.</p>
  <div class="scroller" style="margin-top:22px">
    <table><caption>{set_cap(a, "A")} &middot; every row totals {sa['items']} test cases</caption>
    <thead><tr><th>Comparison</th><th>Label type</th><th>Both<br>right</th><th>Both<br>wrong</th>
    <th>Only<br>Gemini</th><th>Only<br>ours</th><th style="text-align:left">Verdict</th></tr>
    </thead><tbody>{paired_rows(a)}</tbody></table>
  </div>
  <div class="scroller" style="margin-top:22px">
    <table><caption>{set_cap(b, "B")} &middot; every row totals {sb['items']} test cases</caption>
    <thead><tr><th>Comparison</th><th>Label type</th><th>Both<br>right</th><th>Both<br>wrong</th>
    <th>Only<br>Gemini</th><th>Only<br>ours</th><th style="text-align:left">Verdict</th></tr>
    </thead><tbody>{paired_rows(b)}</tbody></table>
  </div>
  <div class="note"><strong>&ldquo;Too few to call&rdquo; is not a pass and not a tie.</strong>
  It means the two models agreed so often that the test could not separate them even in
  principle. <strong>{underpowered} of the {paired_n} rows above</strong> are in that state,
  including both of set&nbsp;B's strongest-looking numbers. Set&nbsp;B's finding is that
  {sb['items']} test cases cannot separate these models &mdash; that is the result, not a failure of
  the run. Note also how often reason defeats both models at once: taking the same pairing in each set,
  Gemini and Qwen3.8 were <em>both</em> wrong on
  {qa['paired_vs_gemini']['reason']['both_wrong']} of {sa['items']} set&nbsp;A calls and
  {qb['paired_vs_gemini']['reason']['both_wrong']} of {sb['items']} set&nbsp;B calls. That
  label type is hard for everything we tested.</div>
</section>

<section id="tokens">
  <h2>4. Input and output tokens</h2>
  <p class="lede">How much text each model consumed and produced for the identical workload.
  Shown per call; totals are in the tables. Two panels with two scales, because input is
  roughly an order of magnitude larger than output and a shared axis would flatten the output
  panel to nothing.</p>

  <p class="setlab">{set_label(a, "A")}</p>
  <figure>
    <figcaption>Tokens per call &middot; two scales, never one axis for both</figcaption>
    <div class="chartbox scroller"><div id="c-tok-a"></div></div>
  </figure>
  <div class="scroller" style="margin-top:20px">
    <table><caption>{set_cap(a, "A")} &middot; totals are over all {sa['rows']} API calls
    ({sa['items']} transcripts &times; {sa['repeats']} repeats)</caption>
    <thead><tr><th>Model</th><th>Input total</th><th>Input per call</th>
    <th>Output total</th><th>Output per call</th></tr></thead>
    <tbody>{token_rows(a)}</tbody></table>
  </div>

  <p class="setlab">{set_label(b, "B")}</p>
  <figure>
    <figcaption>Tokens per call &middot; two scales, never one axis for both</figcaption>
    <div class="chartbox scroller"><div id="c-tok-b"></div></div>
  </figure>
  <div class="scroller" style="margin-top:20px">
    <table><caption>{set_cap(b, "B")} &middot; totals are over all {sb['rows']} API calls
    ({sb['items']} transcripts &times; {sb['repeats']} repeats)</caption>
    <thead><tr><th>Model</th><th>Input total</th><th>Input per call</th>
    <th>Output total</th><th>Output per call</th></tr></thead>
    <tbody>{token_rows(b)}</tbody></table>
  </div>
  <div class="note">Against production, Qwen3.8 needs
  <strong>{(ga['input_tokens_per_call'] - qa['input_tokens_per_call']) / ga['input_tokens_per_call'] * 100:.1f}%
  fewer input tokens</strong> on set&nbsp;A and
  <strong>{(gb['input_tokens_per_call'] - qb['input_tokens_per_call']) / gb['input_tokens_per_call'] * 100:.1f}%
  fewer</strong> on set&nbsp;B for the same Thai text. On a metered API that is money; on our own
  box it is prefill time on a machine section&nbsp;5 shows is throughput-bound, so it is
  cheaper there too &mdash; just not billed.</div>
</section>

<section id="latency">
  <h2>5. Speed on our own GPU</h2>
  <p class="lede">End-to-end response time &mdash; request sent to complete response received.
  Only our own models are charted: production Gemini ran at concurrency
  {ga['concurrency']} over the public internet while ours ran at concurrency
  {qa['concurrency']} against a single shared box on our network, so putting them in one chart
  would invite a comparison the measurement cannot support.</p>
  <div class="note" style="margin-top:12px"><strong>For reference, production Gemini answered in
  {num(ga['latency_s']['p50'])}&nbsp;s at the median on set&nbsp;A and
  {num(gb['latency_s']['p50'])}&nbsp;s on set&nbsp;B</strong>, and cost
  ${ga['cost_usd']:.4f} and ${gb['cost_usd']:.4f} respectively. Those are <strong>run
  totals</strong> over all {sa['rows']} and {sb['rows']} API calls, not per-call figures, and
  they are OpenRouter prices rather than direct Google ones. <strong>Our own endpoint reports no
  cost field at all</strong>, so there is no figure to give for the GPU models and none is shown
  &mdash; an absence of data, not a zero, and not something to read as &ldquo;free&rdquo;.</div>

  <p class="setlab">{set_label(a, "A")}</p>
  <figure>
    <figcaption>End-to-end latency per call &middot; median, 95th percentile, maximum</figcaption>
    <div class="chartbox scroller"><div id="c-lat-a"></div></div>
  </figure>
  <div class="scroller" style="margin-top:20px">
    <table><caption>{set_cap(a, "A")} &middot; our GPU models over {sa['rows']} API calls each, concurrency
    {qa['concurrency']}</caption>
    <thead><tr><th>Model</th><th>p50</th><th>p95</th><th>p99</th><th>Max</th>
    <th>Calls/s</th><th>API attempts</th></tr></thead>
    <tbody>{speed_rows(a)}</tbody></table>
  </div>

  <p class="setlab">{set_label(b, "B")}</p>
  <figure>
    <figcaption>End-to-end latency per call &middot; median, 95th percentile, maximum</figcaption>
    <div class="chartbox scroller"><div id="c-lat-b"></div></div>
  </figure>
  <div class="scroller" style="margin-top:20px">
    <table><caption>{set_cap(b, "B")} &middot; our GPU models over {sb['rows']} API calls each, concurrency
    {qb['concurrency']}</caption>
    <thead><tr><th>Model</th><th>p50</th><th>p95</th><th>p99</th><th>Max</th>
    <th>Calls/s</th><th>API attempts</th></tr></thead>
    <tbody>{speed_rows(b)}</tbody></table>
  </div>
  <div class="note"><strong>There is no time-to-first-token here, and none was invented.</strong>
  The harness does not stream responses, so the only interval ever measured is the complete
  round trip. A partial-response time cannot be recovered from these logs, so the column simply
  does not exist.</div>
</section>

<section id="stability">
  <h2>6. Consistency</h2>
  <p class="lede">Each transcript was sent {sa['repeats']} times, character for character
  identical. A stable model gives the same answer every time. Three columns: how often the
  model's <em>wording</em> moved at all, how many calls had an <em>answer</em> change, and how
  many individual fields moved &mdash; one call can change more than one field, so the last
  number is the largest. The chart plots the middle one, which is the one that matters.</p>

  <p class="setlab">{set_label(a, "A")}</p>
  <figure>
    <figcaption>Scored label changes across {sa['repeats']} identical repeats &middot; lower is
    better</figcaption>
    <div class="chartbox scroller"><div id="c-stab-a"></div></div>
  </figure>
  <div class="scroller" style="margin-top:20px">
    <table><caption>{set_cap(a, "A")} &middot; text changed / scored label changed / cells changed</caption>
    <thead><tr><th>Model</th><th>Calls whose text moved</th><th>Scored labels changed</th>
    <th>Individual fields changed</th></tr></thead>
    <tbody>{stab_rows(a)}</tbody></table>
  </div>

  <p class="setlab">{set_label(b, "B")}</p>
  <figure>
    <figcaption>Scored label changes across {sb['repeats']} identical repeats &middot; lower is
    better</figcaption>
    <div class="chartbox scroller"><div id="c-stab-b"></div></div>
  </figure>
  <div class="scroller" style="margin-top:20px">
    <table><caption>{set_cap(b, "B")} &middot; text changed / scored label changed / cells changed</caption>
    <thead><tr><th>Model</th><th>Calls whose text moved</th><th>Scored labels changed</th>
    <th>Individual fields changed</th></tr></thead>
    <tbody>{stab_rows(b)}</tbody></table>
  </div>
  <div class="note"><strong>Do not read the two sets as a before and after.</strong> Gemini
  changed a scored label {ga['instability']['scored_unstable']} times on set&nbsp;A and
  {gb['instability']['scored_unstable']} times on set&nbsp;B, but the set changed at the same
  time as the date, so nothing here is controlled. The set&nbsp;A Gemini run is
  {run_date(ga['run_id'])} &mdash; the day it stopped being deterministic &mdash; and the
  set&nbsp;B run is {run_date(gb['run_id'])}. That the variation had gone by the second run is
  suggestive, not settled; a byte-identical replay of the original workload is what would
  settle it. <strong>Set&nbsp;A's own arms are not one day either:</strong> Gemini, Qwen3.6 and
  Gemma ran {run_date(ga['run_id'])}, Qwen3.8 ran {run_date(qa['run_id'])}.</div>
  <div class="note">On set&nbsp;A every arm rewrote its wording constantly and what differed was
  whether the rewrite reached a label: Qwen3.8 moved its text on
  {qa['instability']['raw_unstable']} of {qa['instability']['observable']} calls but a scored
  label only {qa['instability']['scored_unstable']} times. This is <em>not</em> a self-hosting
  artefact &mdash; on the same set the hosted reference rewrote
  {ga['instability']['raw_unstable']} and moved a scored label
  {ga['instability']['scored_unstable']} times, the highest of any arm here.</div>
</section>

<section id="missing">
  <h2>7. What is missing, and why</h2>
  <div class="callout warn">
    <h3>Gemma 4 12B was not measured on set B</h3>
    <p>{esc((b.get('copy') or {}).get('note') or '')}</p>
    <p><strong>This is an outage, not a result.</strong> Gemma's accuracy on set&nbsp;B is
    unmeasured, and nothing here should be read as evidence about it either way. Re-running
    takes about eight minutes and carries no metered charge, once that backend is restarted.</p>
  </div>
  <div class="callout">
    <h3>Qwen3.6 27B is absent for a different reason</h3>
    <p>It was measured on set&nbsp;A &mdash; {num(q36['f1']['call_result'])} /
    {num(q36['f1']['reason'])} / {num(q36['f1']['product'])} &mdash; and has since been
    <strong>replaced on the GPU host by Qwen3.8</strong>, so it cannot be run on set&nbsp;B.
    A deployment decision, not a failure. <strong>The upgrade is not measurably an improvement
    in accuracy:</strong> Qwen3.8's F1 is higher on all three label types, but the paired test
    between them returns
    {esc(VERDICT_WORDS[h2h['call_result']['verdict']][0].lower())},
    {esc(VERDICT_WORDS[h2h['reason']['verdict']][0].lower())} and
    {esc(VERDICT_WORDS[h2h['product']['verdict']][0].lower())} &mdash; none of the three
    differences is resolvable, and on reason the disagreements lean the other way (Qwen3.6 right
    on {h2h['reason']['incumbent_only']} calls Qwen3.8 missed, against
    {h2h['reason']['candidate_only']} the other way). What did improve is consistency:
    {qa['instability']['scored_unstable']} scored-label changes against
    {q36['instability']['scored_unstable']}.</p>
  </div>
</section>

<section id="bottom">
  <h2>Bottom line</h2>
  <p><strong>Qwen3.8 27B on our own GPU is not measurably behind production Gemini on either
  set.</strong> On set&nbsp;A it is indistinguishable on the one label type with enough
  disagreements to judge, and the other two could not be called. On set&nbsp;B the same pattern
  holds on a second set built to be harder. Be precise about what that is worth: two of set&nbsp;B's three
  verdicts could not be called at all, so it is better read as <em>a second set failing to find
  a difference</em> than as corroboration. It is still worth having &mdash; a set built to be
  harder did not expose one either.</p>
  <p><strong>Gemma 4 12B is measurably behind, on the set where we have data.</strong> BEHIND on
  all three label types on set&nbsp;A &mdash; {gem['f1']['call_result']:.3f} /
  {gem['f1']['reason']:.3f} / {gem['f1']['product']:.3f} against Gemini's
  {ga['f1']['call_result']:.3f} / {ga['f1']['reason']:.3f} / {ga['f1']['product']:.3f}. Each
  verdict lands exactly on its threshold, so this is the weakest form of &ldquo;worse&rdquo;
  rather than a rout &mdash; but on product Gemini won
  <strong>all {gem['paired_vs_gemini']['product']['discordant']}</strong> of the calls where
  exactly one of them was right.</p>
  <div class="note"><strong>If you have heard that Gemma is indistinguishable from Gemini, that
  came from an earlier assessment and is superseded.</strong> That reading rested on an 89-item
  subset of set&nbsp;A where two of the three label types had too few disagreements to resolve
  &mdash; a limit of that measurement, not evidence of parity. Measured on the full
  {sa['items']} test cases the verdict is BEHIND on all three. Gemma's accuracy on set&nbsp;B remains
  <em>unmeasured</em>; nothing here stands in for it.</div>
  <p><strong>What still blocks a migration decision.</strong> Three things, and the first
  matters most. <strong>The reference itself moved.</strong> On set&nbsp;A Gemini changed a
  scored label on {ga['instability']['scored_unstable']} of
  {ga['instability']['observable']} calls, so &ldquo;indistinguishable from Gemini&rdquo;
  describes Gemini as it behaved on {run_date(ga['run_id'])}, not a settled incumbent &mdash;
  and Gemma's three verdicts each land exactly on their threshold, close enough that a reference
  which moved could plausibly move them. Second, throughput on our own hardware is roughly an
  order of magnitude below the hosted API at the concurrency we tested. Third, both sets are
  synthetic. Before a production call we would need this comparison against real labelled calls,
  with the reference measured on the same day.</p>
</section>

<footer>
  Set&nbsp;A {esc(a['pack'])} &middot; {sa['items']} test cases &middot; {sa['rows']} API calls per
  model &middot; testset {esc(sa['testset_sha'][:12])}&hellip;<br>
  Set&nbsp;B {esc(b['pack'])} &middot; {sb['items']} test cases &middot; {sb['rows']} API calls per
  model &middot; testset {esc(sb['testset_sha'][:12])}&hellip;<br>
  Shared across both: prompt {esc(sa['prompt_sha'][:12])}&hellip; &middot; scorer
  {esc(sa['scoring_code_sha'][:12])}&hellip; &middot; {sa['repeats']} repeats. Accuracy scored on
  replicate 1.<br>
  Every measured figure is copied without recomputation from
  <code>docs/reports/model-comparison-metrics.json</code> and
  <code>docs/reports/model-comparison-challenge-metrics.json</code>, each independently
  re-derived from the raw per-call logs; the charts are the per-set report's own, called once
  per set. Three kinds of figure come from elsewhere and are flagged rather than left looking
  like measurements: dataset properties (turn counts, multi-product density) are counted from
  the testset fixtures, the &ldquo;need&nbsp;N&rdquo; thresholds are the bands the harness
  computed, and the 89-item subset is from the earlier assessment it supersedes. Percentiles by
  {esc(a['percentile_method'])}.<br>
  No result here is a migration verdict until reconciled against production on real labelled
  calls. <strong>RECONCILED: NO.</strong>
</footer>
</div>
{chart_a}
{chart_b}
"""


def main() -> int:
    a, b = load()
    fragment = render(a, b)
    DOCS.mkdir(parents=True, exist_ok=True)
    (DOCS / f"{OUT_STEM}-fragment.html").write_text(fragment, encoding="utf-8")
    cut = fragment.index("</style>") + len("</style>")
    standalone = ('<!DOCTYPE html>\n<html lang="en">\n<head>\n<meta charset="utf-8">\n'
                  '<meta name="viewport" content="width=device-width, initial-scale=1">\n'
                  + fragment[:cut] + "\n</head>\n<body>\n" + fragment[cut:]
                  + "\n</body>\n</html>\n")
    (DOCS / f"{OUT_STEM}.html").write_text(standalone, encoding="utf-8")

    print("all refusals passed")
    print(f"  shared prompt {a['shared_contract']['prompt_sha'][:12]} "
          f"scorer {a['shared_contract']['scoring_code_sha'][:12]}")
    for tag, doc in (("A", a), ("B", b)):
        print(f"  set {tag}  {doc['pack']:24} {doc['shared_contract']['items']:>4} items  "
              f"{len(doc['order'])} models: {', '.join(doc['order'])}")
    print(f"\nwrote docs/reports/{OUT_STEM}.html, -fragment.html "
          f"(ascii-only: {standalone.isascii()})")
    return 0


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
