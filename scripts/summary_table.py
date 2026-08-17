"""A one-page summary table: four models, both evaluation sets, the metrics that matter.

    PYTHONPATH=src python scripts/summary_table.py

The full report explains and caveats everything. This is the overview you send round first:
one table, models across the top, metrics down the side, grouped by evaluation set.

IT RECOMPUTES NOTHING. Every figure is copied from the two published metrics files, which were
independently re-derived from the raw per-call logs by verification workflows.

FOUR THINGS IT DOES DELIBERATELY, because a summary table is where nuance goes to die:

  1. IT DOES NOT BOLD A WINNER ON RESPONSE TIME. The hosted reference ran at concurrency 8 over
     the public internet; our models ran at concurrency 4 against one shared box. Bolding the
     fastest would draw exactly the comparison that measurement cannot support, so that row is
     marked and left unbolded.

  2. IT NEVER LEAVES A BLANK. Two models have no set-B result, for two different reasons -- one
     was an outage, one was never in that comparison. A dash reads as a zero, so each says
     which it is.

  3. IT SAYS WHICH ACCURACY GAPS ARE REAL. Bolding the highest number invites "ours is better
     by 0.011". On most of these rows the models disagreed on too few calls to call a winner at
     all. The note under the table says so, and names the one model that IS measurably behind.

  4. IT LEADS WITH WHAT THE TEST SET IS AND IS NOT. Synthetic calls written in-house for this
     benchmark: no production cases, no customer data, no IP.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "scripts"))

from asr_comparison_report import pooled_cer  # noqa: E402  one subset-pooling rule
from model_comparison_html import esc  # noqa: E402

DOCS = REPO / "docs" / "reports"
PACK_A = DOCS / "model-comparison-metrics.json"
PACK_B = DOCS / "model-comparison-challenge-metrics.json"
VOICE = [("gemini", "Gemini 2.5 Flash", "production today",
          REPO / "asr-eval" / "reports" / "gemini-2.5-flash-audio.json"),
         ("qwen3asr", "Qwen3-ASR 1.7B", "our GPU &middot; candidate",
          REPO / "asr-eval" / "reports" / "qwen3-asr-1.7b.json")]
OUT_STEM = "model-summary"

# Column order: the production reference first, then ours, best-known first.
COLUMNS = ["gemini", "qwen38", "qwen36", "gemma"]
HIGHLIGHT = "qwen38"          # our leading internal candidate
DISPLAY = {
    "gemini": ("Gemini 2.5 Flash", "production today"),
    "qwen38": ("Qwen3.8 27B", "our GPU &middot; candidate"),
    "qwen36": ("Qwen3.6 27B", "our GPU, retired"),
    "gemma": ("Gemma 4 12B", "our GPU"),
}

# Why a model has no figure in a set. Two absences, two causes -- never one marker for both.
# The short form goes in the cell so a screenshot of the table cannot be read as a score; the
# long form is the footnote. Two absences, two causes, never one marker for both.
ABSENT = {
    "gemma": ("GPU outage", "the GPU serving it was already down when the run started, so "
                            "every request came back a server error"),
    "qwen36": ("retired first", "was replaced on the host before this set existed"),
}

SETS = [
    ("A", PACK_A, "The coverage set",
     "One of everything that can go wrong &mdash; a baseline of straightforward calls plus "
     "eight isolated faults"),
    ("B", PACK_B, "The messy-conversation set",
     "The sentences are easy; the conversation is the problem &mdash; reversals, hold and "
     "transfer, prior contact"),
]

# (label, sub-label, how to read it, better direction, bold the best?)
ROWS = [
    # Not bolded: on almost every one of these rows the models disagreed on too few calls to
    # call a winner. Bolding the highest number asserts a result the evaluation does not have.
    ("Call outcome", "save / churn / unknown", "f1", "high", False),
    ("Reason", "11 values, up to 3 per test case", "f1", "high", False),
    ("Product", "exact set per test case", "f1", "high", False),
    ("Input tokens", "per test case", "in_tok", "low", True),
    ("Output tokens", "per test case", "out_tok", "low", True),
    # Not bolded on purpose -- see the module docstring.
    ("Median response time", "not like-for-like &mdash; see below", "p50", "low", False),
    ("Answers that changed", "across 3 identical repeats", "stab", "low", True),
]
F1_DIM = {"Call outcome": "call_result", "Reason": "reason", "Product": "product"}


# (label, sub-label, how to read it, better direction, bold the best?)
# Nothing on the three accuracy rows is bolded. See the module docstring: number rendering
# differs between the arms and moves CER one way and entity recovery the other, so all three
# measure formatting as much as hearing.
VOICE_ROWS = [
    ("Character error rate", "all 20 calls", "cer", "low", False),
    ("Character error rate", "same 19 calls, the runaway excluded", "cer_ex", "low", False),
    ("Entities recovered", "phone, amount, date, package", "ent", "high", False),
    ("Word error rate", "tokeniser-dependent &mdash; read CER first", "wer", "low", False),
    ("Calls with content dropped", "passages missing, no warning", "drop", "low", True),
    ("Calls with runaway repetition", "output far longer than the audio", "loop", "low", True),
]


def runaway_items(docs: list[tuple]) -> set:
    """Calls where ANY arm produced far more text than the audio contains.

    Excluded from every arm, not just the one that broke. Dropping each arm's own worst call
    would compare two different subsets of 19 and quietly flatter whichever arm had the
    milder worst case -- the numbers have to be over the same calls to sit in one row.
    """
    out = set()
    for _k, _n, _r, d in docs:
        out |= {r["item_id"] for r in d["items"] if r["hyp_chars"] > 3 * r["ref_chars"]}
    return out


def voice_value(doc: dict, field: str, excluded: set = frozenset()):
    items = doc["items"]
    if field == "cer":
        return doc["overall"]["cer_norm"]
    if field == "wer":
        return doc["overall"]["wer_norm"]
    if field == "ent":
        return doc["entity_overall"]["accuracy"]
    if field == "cer_ex":
        return pooled_cer(items, excluded)
    if field == "drop":
        # deletion-dominated with almost no insertions: content lost, not misheard
        return sum(1 for r in items
                   if r["cer_norm_sdi"][1] > 0.15 * r["ref_chars"]
                   and r["cer_norm_sdi"][2] < 0.02 * r["ref_chars"])
    if field == "loop":
        return sum(1 for r in items if r["hyp_chars"] > 3 * r["ref_chars"])
    raise Refused(f"unknown voice field {field}")


def voice_fmt(field: str, v) -> str:
    if field in ("cer", "cer_ex", "wer", "ent"):
        return f"{v * 100:.1f}%"
    return str(v)


def voice_table(docs: list[tuple]) -> tuple[str, str]:
    excluded = runaway_items(docs)
    head = "".join(
        f'<th class="{"hi" if key == "qwen3asr" else ""}">'
        f'<span class="m">{esc(name)}</span><span class="s">{role}</span></th>'
        for key, name, role, _doc in docs)
    body = (f'<tr class="grp setb"><th colspan={len(docs) + 1}>'
            f'<span class="gn">The voice set</span>'
            f'<span class="gc">20 calls &middot; 123.6 minutes of synthetic Thai call audio '
            f'&mdash; can a model on our GPU transcribe what production sends to Gemini?'
            f'</span></th></tr>')
    for label, sub, field, direction, do_bold in VOICE_ROWS:
        vals = [voice_value(d, field, excluded) for _k, _n, _r, d in docs]
        present = [v for v in vals if v is not None]
        best = (max(present) if direction == "high" else min(present)) if present else None
        cells = ""
        for (key, _n, _r, _d), v in zip(docs, vals):
            klass = "hi" if key == "qwen3asr" else ""
            mark = " b" if (do_bold and best is not None and v == best) else ""
            cells += f'<td class="{klass}{mark}">{voice_fmt(field, v)}</td>'
        body += (f'<tr class="setb"><th scope=row><span class="rl">{esc(label)}</span>'
                 f'<span class="rs">{sub}</span></th>{cells}</tr>')
    return head, body


class Refused(SystemExit):
    def __init__(self, why: str) -> None:
        super().__init__(f"REFUSING to write the summary: {why}")


def value(doc: dict, key: str, row: tuple):
    """The raw number for one model / one row, or None if that model is not in this set."""
    m = doc["models"].get(key)
    if m is None:
        return None
    label, _sub, field, _dir, _bold = row
    if field == "f1":
        return m["f1"][F1_DIM[label]]
    if field == "in_tok":
        return m["input_tokens_per_call"]
    if field == "out_tok":
        return m["output_tokens_per_call"]
    if field == "p50":
        return m["latency_s"]["p50"]
    if field == "stab":
        return (m["instability"] or {}).get("scored_unstable")
    raise Refused(f"unknown field {field}")


def fmt(field: str, v) -> str:
    if field == "f1":
        return f"{v:.3f}"
    if field in ("in_tok", "out_tok"):
        return f"{v:,.0f}"
    if field == "p50":
        return f"{v:.2f}s"
    return str(v)


def render(a: dict, b: dict, voice: list[tuple]) -> str:
    docs = {"A": a, "B": b}
    vhead, vbody = voice_table(voice)
    sa, sb = a["shared_contract"], b["shared_contract"]

    # REFUSAL. Same prompt and same scorer is what lets one table hold both sets.
    for f in ("prompt_sha", "scoring_code_sha", "repeats"):
        if sa[f] != sb[f]:
            raise Refused(f"the two sets disagree on {f}; they cannot share a table")

    head = "".join(
        f'<th class="{"hi" if k == HIGHLIGHT else ""}">'
        f'<span class="m">{esc(DISPLAY[k][0])}</span>'
        f'<span class="s">{esc(DISPLAY[k][1])}</span></th>'
        for k in COLUMNS)

    body = ""
    for tag, _path, set_name, set_blurb in SETS:
        doc = docs[tag]
        n = doc["shared_contract"]["items"]
        band = " setb" if tag == "B" else ""
        body += (f'<tr class="grp{band}"><th colspan={len(COLUMNS) + 1}>'
                 f'<span class="gn">{esc(set_name)}</span>'
                 f'<span class="gc">{n} test cases &middot; {esc(set_blurb)}</span></th></tr>')
        for row in ROWS:
            label, sub, field, direction, do_bold = row
            vals = {k: value(doc, k, row) for k in COLUMNS}
            present = [v for v in vals.values() if v is not None]
            best = (max(present) if direction == "high" else min(present)) if present else None
            cells = ""
            for k in COLUMNS:
                v = vals[k]
                klass = "hi" if k == HIGHLIGHT else ""
                if v is None:
                    short, _why = ABSENT[k]
                    cells += f'<td class="{klass} na">{short}</td>'
                    continue
                mark = " b" if (do_bold and best is not None and v == best) else ""
                cells += f'<td class="{klass}{mark}">{fmt(field, v)}</td>'
            note = ' <span class="dag">&dagger;</span>' if field == "p50" else ""
            body += (f'<tr class="{band.strip()}"><th scope=row>'
                     f'<span class="rl">{esc(label)}{note}</span>'
                     f'<span class="rs">{sub}</span></th>{cells}</tr>')

    return f"""<title>Retention Labelling Evaluation &mdash; Summary</title>
<style>
:root{{color-scheme:light dark;
--bg:#FAF9F7;--card:#FFF;--ink:#12100E;--ink2:#5A554E;--ink3:#8B857C;
--line:#E7E3DD;--line2:#CFC9C0;--hi:#F9E4DC;--hi-line:#E4795A;--grp:#F3F1ED;
--serif:"Charter","Iowan Old Style",Cambria,Georgia,serif;
--sans:system-ui,-apple-system,"Segoe UI",Roboto,Helvetica,Arial,sans-serif;
--mono:ui-monospace,"Cascadia Mono","SF Mono",Menlo,Consolas,monospace}}
@media (prefers-color-scheme:dark){{:root:not([data-theme="light"]){{
--bg:#131211;--card:#1B1A18;--ink:#EFEDE9;--ink2:#A9A39A;--ink3:#7C766D;
--line:#2C2A27;--line2:#403D38;--hi:#3A211A;--hi-line:#C4664A;--grp:#211F1C}}}}
:root[data-theme="dark"]{{--bg:#131211;--card:#1B1A18;--ink:#EFEDE9;--ink2:#A9A39A;
--ink3:#7C766D;--line:#2C2A27;--line2:#403D38;--hi:#3A211A;--hi-line:#C4664A;--grp:#211F1C}}
*{{box-sizing:border-box}}
body{{background:var(--bg);color:var(--ink);font-family:var(--sans);margin:0;
padding:0 20px 60px;font-size:16px;line-height:1.6;-webkit-font-smoothing:antialiased}}
.wrap{{max-width:1000px;margin:0 auto}}
header{{padding:52px 0 8px}}
h1{{font-family:var(--serif);font-size:clamp(30px,4.4vw,44px);line-height:1.06;
letter-spacing:-.02em;font-weight:700;margin:0 0 18px;text-wrap:balance}}
.intro{{font-family:var(--serif);font-size:clamp(16.5px,2vw,19px);line-height:1.55;
color:var(--ink2);max-width:64ch;margin:0 0 14px}}
.intro a{{color:inherit}}
.scope{{background:var(--card);border:1px solid var(--line);border-left:3px solid var(--hi-line);
padding:16px 18px;margin:24px 0 8px;max-width:72ch;font-size:14px;color:var(--ink2)}}
.scope b{{color:var(--ink)}}
.scroller{{overflow-x:auto;margin-top:30px}}
table{{border-collapse:collapse;width:100%;min-width:720px;background:var(--card);
border:1px solid var(--line);font-variant-numeric:tabular-nums}}
th,td{{padding:13px 14px;text-align:center;border-bottom:1px solid var(--line)}}
thead th{{font-family:var(--serif);font-size:15px;font-weight:600;padding-top:18px;
padding-bottom:16px;border-bottom:1px solid var(--line2)}}
thead th .m{{display:block}}
thead th .s{{display:block;font-family:var(--sans);font-size:10.5px;font-weight:400;
letter-spacing:.06em;text-transform:uppercase;color:var(--ink3);margin-top:3px}}
tbody th[scope=row]{{text-align:left;font-weight:600;font-size:13.5px;width:31%}}
.rl{{display:block}}
.rs{{display:block;font-size:11px;font-weight:400;color:var(--ink3);margin-top:2px;
font-family:var(--mono)}}
td{{font-family:var(--mono);font-size:14px;color:var(--ink2)}}
td.b{{font-weight:700;color:var(--ink)}}
td.na{{font-family:var(--sans);font-size:12px;font-style:italic;color:var(--ink3)}}
.hi{{background:var(--hi)}}
thead th.hi{{border-radius:3px 3px 0 0}}
tr.grp th{{background:var(--grp);text-align:left;padding:14px;border-bottom:1px solid var(--line2);
border-top:1px solid var(--line2)}}
.gn{{display:block;font-family:var(--serif);font-size:15.5px;font-weight:700}}
.gc{{display:block;font-size:11.5px;color:var(--ink3);font-weight:400;margin-top:2px}}
.dag{{color:var(--hi-line);font-weight:700}}
tr.setb th[scope=row]{{border-left:3px solid var(--line2)}}
tr.grp.setb th{{border-left:3px solid var(--line2)}}
.notes{{margin-top:22px;font-size:13.5px;color:var(--ink2);max-width:76ch}}
.notes p{{margin:0 0 10px}}
.notes b{{color:var(--ink)}}
h2.tt{{font-family:var(--serif);font-size:17px;font-weight:600;margin:30px 0 2px}}
p.tl{{font-size:13px;color:var(--ink2);margin:0 0 12px;max-width:70ch;line-height:1.6}}
footer{{margin-top:38px;padding-top:16px;border-top:1px solid var(--line);
font-family:var(--mono);font-size:11px;color:var(--ink3);line-height:1.75}}
@media(max-width:620px){{tbody th[scope=row]{{width:auto}}}}
</style>
<div class="wrap">
<header>
  <h1>Model evaluation &mdash; our test cases on our own GPUs</h1>
  <p class="intro">An evaluation of the models we could run on True&rsquo;s own hardware
  against the one we use in production today, across <b>two tracks</b>: labelling a call from
  its transcript, and producing that transcript from audio in the first place. Every figure below
  comes from <b>evaluation test cases we wrote</b> &mdash; no production traffic was involved.
  It covers accuracy, how many tokens each model uses, how fast it answers and how consistent
  it is.</p>
  <div class="scope"><b>What this is, and what it is not.</b> An initial benchmark I built to
  size up these models for our own use cases. <b>Every accuracy figure here is an upper
  bound</b> &mdash; the test cases are written, and real calls are messier. It ranks these
  models against each other; it does not predict production accuracy, and it is
  <b>not a migration verdict</b> until we evaluate against real labelled calls.<br><br>
  <b>Every item scored here is an evaluation test case</b> &mdash; a transcript we wrote
  in-house for this purpose, with the right answer filled in by hand. Not production cases, no
  customer data, no real transcripts, no IP.</div>
</header>

<h2 class="tt">Track 1 &mdash; labelling a call from its transcript</h2>
<p class="tl">What production does today, on written test cases. Four models, two sets.</p>
<div class="scroller">
  <table>
    <thead><tr><th></th>{head}</tr></thead>
    <tbody>{body}</tbody>
  </table>
</div>

<h2 class="tt">Track 2 &mdash; turning the audio into that transcript</h2>
<p class="tl">The step that does not exist in our stack yet. Production hands Gemini the audio
and gets labels back in one call; a text-only model needs this step first, and everything
above inherits its errors. Different models and different metrics from Track&nbsp;1, so it is
a separate table &mdash; only the production reference appears in both.</p>
<div class="scroller">
  <table>
    <thead><tr><th></th>{vhead}</tr></thead>
    <tbody>{vbody}</tbody>
  </table>
</div>

<div class="notes">
  <p><b>Do not read a higher accuracy figure as a win.</b> On almost every accuracy row the
  models gave the same answer on nearly every test case, and where they differed there were too
  few disagreements to call a winner at all &mdash; a 0.002 gap can rest on a single test
  case. That is
  why <b>the accuracy rows are not bolded</b>. The one difference that is statistically real:
  <b>Gemma 4 12B is measurably behind</b> the production model on all three accuracy rows of
  the coverage set. Everything else is indistinguishable or too close to judge. The full report
  shows the working.</p>
  <p><b>Higher is better</b> for the three accuracy rows; <b>lower is better</b> for tokens,
  response time and answer changes. Bold marks the best figure only on the rows where the gap
  is large enough to mean something &mdash; tokens and answer changes.</p>
  <p><span class="dag">&dagger;</span> <b>Response time is not a like-for-like comparison</b>
  and is not bolded for that reason. The production model was called over the public internet
  at concurrency&nbsp;8; ours ran at concurrency&nbsp;4 against a single shared box. Those rows
  measure deployments, not models.</p>
  <p><b>On the voice table, none of the three accuracy rows is bolded, and the reason is
  not caution.</b> The two arms write numbers differently: the reference spells them out
  (&ldquo;eighteen August&rdquo;), the production model matches it <i>because the prompt we
  wrote asks it to</i>, and the model on our GPU writes digits. That one difference pushes
  character error rate against our model and entity recovery in its favour, so all three
  figures measure formatting as much as hearing. Reading the digits back as spoken Thai
  closes about two fifths of the character-error gap. The full voice report shows the
  working.</p>
  <p><b>The two figures that are clean are the last two, and they are the ones to look at.</b>
  Both models produce a broken transcript on some calls, in opposite ways, and neither says so
  &mdash; every call returned a normal response. The production model silently drops whole
  passages on two calls; ours runs away into repetition on one, emitting seventeen times more
  text than the audio contains. That one call is 83% of its total character error, which is
  why the row above it excludes it. Re-running it reproduced byte-for-byte, so it is a
  property of the model on that audio, not luck.</p>
  <p><b>Two models have no result on the second set, for different reasons.</b> Gemma was
  attempted, and {esc(ABSENT['gemma'][1])}. Its accuracy on that set is therefore
  <b>unmeasured, not zero</b> &mdash; the outage is a fact about our box, not about Gemma.
  Qwen3.6 {esc(ABSENT['qwen36'][1])}, so it was never in that comparison.</p>
</div>

<footer>
  Coverage set {esc(a['pack'])} &middot; {sa['items']} test cases &middot; {sa['rows']} API
  calls per model. Messy-conversation set {esc(b['pack'])} &middot; {sb['items']} test cases
  &middot; {sb['rows']} API calls per model.<br>
  Both sets: same prompt ({esc(sa['prompt_sha'][:12])}&hellip;), same scoring code
  ({esc(sa['scoring_code_sha'][:12])}&hellip;), {sa['repeats']} repeats. Accuracy is scored on
  the first repeat; the answer-changes row is what the other two measure. Accuracy is weighted
  F1. Figures copied without recomputation from the two published metrics files, each
  independently re-derived from the raw per-call logs.
</footer>
</div>
"""


def main() -> int:
    a = json.loads(PACK_A.read_text(encoding="utf-8"))
    b = json.loads(PACK_B.read_text(encoding="utf-8"))
    voice = [(k, n, r, json.loads(path.read_text(encoding="utf-8")))
             for k, n, r, path in VOICE]
    frag = render(a, b, voice)
    cut = frag.index("</style>") + len("</style>")
    page = ('<!DOCTYPE html>\n<html lang="en">\n<head>\n<meta charset="utf-8">\n'
            '<meta name="viewport" content="width=device-width, initial-scale=1">\n'
            + frag[:cut] + "\n</head>\n<body>\n" + frag[cut:] + "\n</body>\n</html>\n")
    (DOCS / f"{OUT_STEM}.html").write_text(page, encoding="utf-8")
    (DOCS / f"{OUT_STEM}-fragment.html").write_text(frag, encoding="utf-8")
    print(f"wrote docs/reports/{OUT_STEM}.html, -fragment.html "
          f"(ascii-only: {page.isascii()})")
    print(f"  columns: {', '.join(DISPLAY[k][0] for k in COLUMNS)}")
    print(f"  rows:    {len(ROWS)} metrics x 2 sets, "
          f"plus {len(VOICE_ROWS)} voice metrics x {len(VOICE)} arms")
    return 0


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
