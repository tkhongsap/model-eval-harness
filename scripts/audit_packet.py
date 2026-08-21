"""Build a BLIND re-labelling packet so humans can audit the ground truth.

    PYTHONPATH=src python scripts/audit_packet.py --run out/runs/<stamp>-e21
    PYTHONPATH=src python scripts/audit_packet.py --run ... --controls 30 --seed 20260821

WHY THIS EXISTS. Every published figure in this repository is scored against labels a
generator wrote. Twice now those labels have turned out to contradict the written spec they
are scored against -- `unknown` built from the exact phrases the spec rules to be `save`
(found 2026-08-20, worth ~0.13 weighted F1 on every arm), and `undefined` fired on calls that
were plainly in scope. Both were found by reading, by accident, and late.

A benchmark nobody has audited is a benchmark whose failures look like model failures. This
packet is the audit: independent readers label a sample of calls using ONLY the transcript and
the written spec, and their labels are compared to the corpus's. Where a reader disagrees with
the corpus, the corpus is the thing that is wrong.

WHAT MAKES IT BLIND, AND WHY EACH PIECE MATTERS

The packet shows the reviewer the Thai transcript and the spec. It does NOT show:

  * the expected label -- the obvious one, and the reason `judge.py` cannot be reused as-is:
    `judge.py:570` prints `Reference label under review: {gt_label}` straight into its prompt.
  * the EVIDENCE SPANS. `case_explorer` renders each label beside the verbatim phrase that
    justifies it. Showing the reader the sentence the labeller keyed on is showing them the
    answer with extra steps.
  * `mechanism`, `why_it_matters`, `expected_failure` -- the corpus's own notes on what each
    call is supposed to demonstrate.
  * every model's answer, and which group the case came from. A reviewer who can tell a
    disagreement case from a control will read them differently.

CONTROLS ARE NOT PADDING. Roughly a third of the packet is cases every arm got right. Without
them the exercise tells you where the corpus is wrong and CANNOT tell you how often it is
right -- and "we audited the failures and found failures" is not a finding. With them, the
reviewer-versus-corpus agreement rate on cases nobody disputed is the base rate the disputed
cases are read against.

SHUFFLED WITH A RECORDED SEED. Group order would otherwise leak the grouping, and an
unrecorded shuffle cannot be reproduced when someone asks why case 14 was case 14.

WHAT THIS IS NOT. It is diagnostic. Nothing here changes a score. `judge.py` and `severity.py`
are AST-tested never to enter the verdict path and this file keeps to the same rule: it reads
a run, it writes a packet, and the scoring of reviewer answers lives in `audit_score.py`.
"""

from __future__ import annotations

import argparse
import collections
import csv
import hashlib
import html
import importlib.util
import json
import random
import re
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "src"))

from evalharness.labelspaces import RETENTION  # noqa: E402

VOCAB = REPO / "tests" / "fixtures" / "testsets" / "VOCABULARIES.md"

# The two spec sections a reviewer needs, by their headings in VOCABULARIES.md. `reason` (§1)
# is deliberately excluded: it is 1,000 lines, it is not what this packet asks about, and a
# reviewer who has to skim it is a reviewer who stops reading the part that matters.
SPEC_SECTIONS = (
    "# 2. `retention_outcome` (scored as `call_result`) — 4 values",
    "# 3. `product` — 4 keys",
)

GROUPS = ("product_mismatch", "outcome_error", "control")


class Refused(SystemExit):
    def __init__(self, message: str) -> None:
        super().__init__(f"audit_packet REFUSING: {message}")


def load_scorer():
    spec = importlib.util.spec_from_file_location(
        "e23", REPO / "scripts" / "experiment23_score.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def case_id(call_id: str, seed: int) -> str:
    """Stable, opaque, and seed-scoped.

    Opaque so the id cannot be decoded back to `ASR-076` and looked up; seed-scoped so two
    packets built from different samples cannot collide on an id and be silently merged.
    """
    digest = hashlib.sha256(f"{seed}:{call_id}".encode()).hexdigest()
    return f"ac_{digest[:10]}"


def spec_text() -> str:
    """The written spec, sliced to the two sections under audit."""
    if not VOCAB.exists():
        raise Refused(f"no spec at {VOCAB}; it is what the reviewer labels against")
    lines = VOCAB.read_text(encoding="utf-8").splitlines()
    starts = []
    for heading in SPEC_SECTIONS:
        try:
            starts.append(lines.index(heading))
        except ValueError:
            raise Refused(
                f"VOCABULARIES.md no longer contains the heading {heading!r}. The packet "
                "slices the spec by heading; if the document was restructured, update "
                "SPEC_SECTIONS rather than shipping a packet with no rules in it."
            )
    out: list[str] = []
    for start in starts:
        end = next((i for i in range(start + 1, len(lines))
                    if lines[i].startswith("# ") and i != start), len(lines))
        out.extend(lines[start:end])
    return "\n".join(out)


def transcript_turns(pack: Path, item_id: str) -> list[tuple[str, str]]:
    """(speaker, text) per turn, from the pack's own dialogue json."""
    path = pack / "dialogues" / f"{item_id}.json"
    if not path.exists():
        raise Refused(f"no dialogue at {path}")
    data = json.loads(path.read_text(encoding="utf-8"))
    return [(t["speaker"], t["text"]) for t in data["turns"]]


def classify(gt: dict, pred: dict) -> str:
    """Which group a call belongs to, from the ceiling arm's answer."""
    if set(gt) != set(pred):
        return "product_mismatch"
    if any(gt[k] != pred[k] for k in gt):
        return "outcome_error"
    return "control"


def build_cases(run: Path, pack: Path, arm: str, controls: int, seed: int) -> list[dict]:
    e23 = load_scorer()
    truth, phones = e23.load_truth(pack)
    collapsed, _unstable, _failures = e23.collapse(run, "first")
    if arm not in collapsed:
        raise Refused(f"arm {arm!r} not in {run}; present: {sorted(collapsed)}")

    gt_rows, pred_rows = e23.build_pair(truth, phones, collapsed[arm])
    gt: dict[str, dict[str, str]] = collections.defaultdict(dict)
    pred: dict[str, dict[str, str]] = collections.defaultdict(dict)
    for r in gt_rows:
        gt[r.call_id][r.product.lower()] = r.call_result
    for r in pred_rows:
        pred[r.call_id][r.product.lower()] = r.call_result

    by_group: dict[str, list[str]] = collections.defaultdict(list)
    for call_id in sorted(gt):
        by_group[classify(gt[call_id], pred.get(call_id, {}))].append(call_id)

    rng = random.Random(seed)
    chosen = list(by_group["product_mismatch"]) + list(by_group["outcome_error"])
    pool = sorted(by_group["control"])
    if len(pool) < controls:
        raise Refused(
            f"asked for {controls} controls but only {len(pool)} calls were answered "
            "correctly by every arm. A packet that is nearly all disagreements cannot "
            "measure a base rate."
        )
    chosen += rng.sample(pool, controls)

    cases = []
    for call_id in chosen:
        item_id = f"ASR-{int(call_id) - 7099:03d}"
        cases.append({
            "case_id": case_id(call_id, seed),
            "call_id": call_id,
            "item_id": item_id,
            "group": classify(gt[call_id], pred.get(call_id, {})),
            "turns": transcript_turns(pack, item_id),
            # Kept for the answer key ONLY. `render()` must never read these, and
            # tests/test_audit_packet_is_blind.py asserts they do not reach the HTML.
            "_expected_product": sorted(gt[call_id]),
            "_expected_outcome": sorted(set(gt[call_id].values())),
        })
    rng.shuffle(cases)
    return cases


PRODUCT_CHOICES = ("Postpaid", "TOL", "TVS", "unknown")


def render(cases: list[dict], spec: str, seed: int, run_name: str) -> str:
    """The reviewer-facing page. Reads only blind fields -- never the `_expected_*` keys."""
    esc = html.escape
    parts: list[str] = []
    for n, case in enumerate(cases, 1):
        turns = "\n".join(
            f'<div class="turn {esc(sp)}"><span class="who">{esc(sp)}</span>'
            f'<span class="said">{esc(tx)}</span></div>'
            for sp, tx in case["turns"])
        outcome_opts = "\n".join(
            f'<label><input type="radio" name="o_{case["case_id"]}" value="{esc(v)}"> '
            f'<code>{esc(v)}</code></label>' for v in RETENTION.call_result)
        product_opts = "\n".join(
            f'<label><input type="radio" name="p_{case["case_id"]}" value="{esc(v)}"> '
            f'<code>{esc(v)}</code></label>' for v in PRODUCT_CHOICES)
        parts.append(f"""
<section class="case" id="{esc(case['case_id'])}">
  <h3><span class="num">Case {n} of {len(cases)}</span>
      <code class="cid">{esc(case['case_id'])}</code></h3>
  <div class="transcript">{turns}</div>
  <div class="answers">
    <div class="q"><b>Which service is this call about?</b>
      <div class="opts">{product_opts}</div></div>
    <div class="q"><b>What is the retention outcome?</b>
      <div class="opts">{outcome_opts}</div></div>
    <div class="q"><b>Which line decided it?</b>
      <input type="text" class="ev" name="e_{esc(case['case_id'])}"
             placeholder="paste the sentence you keyed on"></div>
  </div>
</section>""")

    return f"""<title>Retention Label Audit</title>
<style>
:root{{--ground:#F5F3EF;--raised:#FFF;--sunken:#EDEAE4;--ink:#1A1D21;--ink-mid:#565C64;
--ink-faint:#8A9099;--rule:#DCD8D0;--accent:#B5651D;--agent:#3B6EA8;
--serif:"Charter","Iowan Old Style",Cambria,Georgia,serif;
--sans:system-ui,-apple-system,"Segoe UI",Roboto,Arial,sans-serif;
--thai:"Noto Sans Thai","Leelawadee UI","Tahoma",sans-serif;
--mono:ui-monospace,"Cascadia Mono",Consolas,monospace}}
@media (prefers-color-scheme:dark){{:root:not([data-theme="light"]){{--ground:#14161A;
--raised:#1C1F24;--sunken:#191C21;--ink:#E8E6E1;--ink-mid:#A2A8B0;--ink-faint:#767C85;
--rule:#2C3037;--accent:#D8933F;--agent:#6BA3DC}}}}
:root[data-theme="dark"]{{--ground:#14161A;--raised:#1C1F24;--sunken:#191C21;--ink:#E8E6E1;
--ink-mid:#A2A8B0;--ink-faint:#767C85;--rule:#2C3037;--accent:#D8933F;--agent:#6BA3DC}}
*{{box-sizing:border-box}}
body{{margin:0;background:var(--ground);color:var(--ink);font-family:var(--serif);
font-size:17px;line-height:1.6}}
.wrap{{max-width:56rem;margin:0 auto;padding:2.5rem 1.5rem 6rem}}
h1,h2,h3{{font-family:var(--sans);line-height:1.25;margin:0;text-wrap:balance}}
h1{{font-size:2.1rem;font-weight:660;letter-spacing:-.02em}}
h2{{font-size:1.2rem;font-weight:640;margin:2.5rem 0 .6rem;padding-top:1.2rem;
border-top:1px solid var(--rule)}}
h3{{font-size:.95rem;font-weight:640;display:flex;gap:.7rem;align-items:baseline}}
code{{font-family:var(--mono);font-size:.87em;background:var(--sunken);padding:.08em .34em;
border-radius:3px}}
.lede{{color:var(--ink-mid);font-size:1.1rem;max-width:52ch}}
.rules{{background:var(--raised);border:1px solid var(--rule);border-left:3px solid
var(--accent);border-radius:5px;padding:1.1rem 1.4rem;margin:1.5rem 0;font-size:.96rem}}
.rules p{{margin:.45rem 0}} .rules p:first-child{{margin-top:0}}
.rules p:last-child{{margin-bottom:0}}
details.spec{{background:var(--raised);border:1px solid var(--rule);border-radius:5px;
padding:.9rem 1.2rem;margin:1.4rem 0}}
details.spec summary{{font-family:var(--sans);font-weight:640;cursor:pointer;font-size:.95rem}}
details.spec pre{{white-space:pre-wrap;font-family:var(--mono);font-size:.78rem;
line-height:1.55;max-height:34rem;overflow:auto;background:var(--sunken);padding:.9rem;
border-radius:4px;margin-top:.9rem}}
.case{{background:var(--raised);border:1px solid var(--rule);border-radius:6px;
padding:1.2rem 1.4rem;margin:1.6rem 0}}
.num{{color:var(--ink-faint);font-weight:600}}
.cid{{margin-left:auto;font-size:.75rem;color:var(--ink-faint);background:none;padding:0}}
.transcript{{margin:1rem 0;max-height:26rem;overflow:auto;background:var(--sunken);
border-radius:4px;padding:.9rem 1rem;font-family:var(--thai);font-size:.94rem;
line-height:1.95}}
.turn{{margin:.3rem 0;display:flex;gap:.6rem}}
.who{{font-family:var(--sans);font-size:.66rem;font-weight:700;letter-spacing:.06em;
text-transform:uppercase;color:var(--ink-faint);min-width:4.6rem;padding-top:.35rem}}
.turn.agent .who{{color:var(--agent)}}
.answers{{display:grid;gap:.9rem;padding-top:.5rem;border-top:1px solid var(--rule)}}
.q{{font-family:var(--sans);font-size:.88rem}}
.opts{{display:flex;flex-wrap:wrap;gap:.9rem;margin-top:.4rem}}
.opts label{{display:flex;align-items:center;gap:.32rem;cursor:pointer}}
.ev{{width:100%;margin-top:.4rem;padding:.45rem .6rem;font-family:var(--thai);
font-size:.9rem;background:var(--sunken);color:var(--ink);border:1px solid var(--rule);
border-radius:4px}}
.bar{{position:sticky;bottom:0;background:var(--raised);border-top:1px solid var(--rule);
padding:.9rem 1.5rem;display:flex;gap:1rem;align-items:center;font-family:var(--sans);
font-size:.88rem;margin-top:2rem}}
button{{font-family:var(--sans);font-size:.9rem;font-weight:640;padding:.5rem 1rem;
border-radius:5px;border:1px solid var(--accent);background:var(--accent);color:#fff;
cursor:pointer}}
#out{{width:100%;height:11rem;margin-top:1rem;font-family:var(--mono);font-size:.75rem;
background:var(--sunken);color:var(--ink);border:1px solid var(--rule);border-radius:4px;
padding:.6rem;display:none}}
footer{{margin-top:2.5rem;padding-top:1.2rem;border-top:1px solid var(--rule);
font-family:var(--sans);font-size:.78rem;color:var(--ink-faint)}}
</style>
<div class="wrap">
<h1>Retention label audit</h1>
<p class="lede">{len(cases)} calls. For each one: read the transcript, apply the written
rules, and record the label you would give it.</p>

<div class="rules">
<p><b>Use only the transcript and the rules below.</b> Not what seems reasonable, not what a
customer probably meant &mdash; what the written rules say. Where your judgement and the rules
disagree, follow the rules and say so in the evidence box.</p>
<p><b>You are not checking anyone&rsquo;s work.</b> There is no expected answer shown, and the
cases are shuffled. Some are calls every model labelled correctly; some are not. You cannot
tell which from looking, and that is deliberate &mdash; it is the only way your agreement rate
means anything.</p>
<p><b>If a call genuinely does not fit any label, say so in the evidence box</b> rather than
forcing the nearest one. A call the rules cannot label is a finding about the rules.</p>
</div>

<details class="spec">
<summary>The written rules &mdash; <code>VOCABULARIES.md</code> &sect;2 and &sect;3</summary>
<pre>{esc(spec)}</pre>
</details>

<h2>The calls</h2>
{"".join(parts)}

<div class="bar">
  <button onclick="collect()">Copy my answers</button>
  <span id="status" class="num">nothing copied yet</span>
</div>
<textarea id="out" readonly></textarea>

<footer>
Packet built from run <code>{esc(run_name)}</code> &middot; shuffle seed
<code>{seed}</code> &middot; expected labels are not present in this file
</footer>
</div>
<script>
function collect(){{
  const ids=[...document.querySelectorAll('section.case')].map(s=>s.id);
  const rows=[['case_id','product','call_result','evidence']];
  let done=0;
  for(const id of ids){{
    const p=document.querySelector(`input[name="p_${{id}}"]:checked`);
    const o=document.querySelector(`input[name="o_${{id}}"]:checked`);
    const e=document.querySelector(`input[name="e_${{id}}"]`);
    if(p&&o)done++;
    rows.push([id,p?p.value:'',o?o.value:'',(e&&e.value||'').replace(/"/g,'""')]);
  }}
  const csv=rows.map(r=>r.map(c=>`"${{c}}"`).join(',')).join('\\n');
  const out=document.getElementById('out');
  out.style.display='block';out.value=csv;out.select();
  navigator.clipboard&&navigator.clipboard.writeText(csv);
  document.getElementById('status').textContent=
    `${{done}} of ${{ids.length}} answered — CSV copied, paste it into a file and send it back`;
}}
</script>"""


def main() -> int:
    ap = argparse.ArgumentParser(prog="audit_packet")
    ap.add_argument("--run", type=Path, required=True)
    ap.add_argument("--pack", type=Path, default=REPO / "asr-eval-v2")
    ap.add_argument("--arm", default="ceiling",
                    help="the arm whose answers define the groups. Default `ceiling` -- it "
                         "reads the perfect transcript, so its disagreements are labelling "
                         "disputes rather than mishearing.")
    ap.add_argument("--controls", type=int, default=30)
    ap.add_argument("--seed", type=int, default=20260821)
    ap.add_argument("--out", type=Path,
                    default=REPO / "docs" / "reports" / "audit-packet.html")
    ap.add_argument("--key", type=Path,
                    default=REPO / "out" / "audit-answer-key.csv",
                    help="the expected labels, written OUTSIDE docs/ because out/ is "
                         "gitignored and this must never ship beside the packet")
    args = ap.parse_args()

    cases = build_cases(args.run, args.pack, args.arm, args.controls, args.seed)
    counts = collections.Counter(c["group"] for c in cases)
    page = render(cases, spec_text(), args.seed, args.run.name)

    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(page, encoding="utf-8", newline="\n")

    args.key.parent.mkdir(parents=True, exist_ok=True)
    with args.key.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow(["case_id", "call_id", "item_id", "group",
                         "expected_product", "expected_call_result"])
        for case in sorted(cases, key=lambda c: c["case_id"]):
            writer.writerow([case["case_id"], case["call_id"], case["item_id"],
                             case["group"], "|".join(case["_expected_product"]),
                             "|".join(case["_expected_outcome"])])

    print(f"packet  {args.out}   {len(cases)} cases")
    for group in GROUPS:
        print(f"          {group:18} {counts.get(group, 0)}")
    print(f"key     {args.key}   (gitignored -- do not send this with the packet)")
    print(f"seed    {args.seed}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
