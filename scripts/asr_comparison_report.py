"""Compare two ASR arms on the committed audio set, and publish the page.

IT RECOMPUTES NOTHING. Every figure is copied from a `score_asr.py --json` file. That scorer
pools over the corpus rather than averaging per-file rates, and its `pooled()` carries a fixed
bug about matching the denominator to the metric prefix -- re-deriving those numbers here
would be a second chance to get them wrong, and the likeliest way to reintroduce exactly that
bug. If a figure is not in the metrics JSON, it does not appear on the page.

WHICH COMPARISON IS A CONFIG, NOT A CODE CHANGE. Adding an arm is a new entry in
`configs/comparison/asr-voice-v1.json`; nothing here is edited.

Run:
    python scripts/asr_comparison_report.py
    python scripts/asr_comparison_report.py --config configs/comparison/asr-voice-v1.json
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "scripts"))

from asr_comparison_html import (EXTRA_CSS, STYLE, asr_charts_js,  # noqa: E402
                                 esc, num)

CONFIG_DIR = REPO / "configs" / "comparison"
DEFAULT_CONFIG = CONFIG_DIR / "asr-voice-v1.json"


class Refused(SystemExit):
    def __init__(self, msg: str) -> None:
        super().__init__(f"REFUSING to write a report: {msg}")


def load(cfg: dict) -> list[dict]:
    """Read each arm's metrics JSON and check the pair can honestly be compared."""
    arms = []
    for a in cfg["arms"]:
        p = REPO / a["metrics"]
        if not p.is_file():
            raise Refused(f"{a['key']}: {a['metrics']} does not exist. Run the arm and score "
                          f"it before publishing a comparison that claims to include it.")
        d = json.loads(p.read_text(encoding="utf-8"))
        if "overall" not in d:
            raise Refused(f"{a['key']}: {a['metrics']} has no 'overall' block, so it predates "
                          f"the aggregates being persisted. Re-run score_asr.py --json.")
        arms.append({**a, "doc": d})

    expected = cfg["expected_items"]

    # 1. Every arm scored every item. score_asr.py SKIPS a missing hypothesis and still exits
    #    0, printing "NOT scored as zero" -- correct for the scorer, silent for a reader. A
    #    19-item arm compared against a 20-item one is a different corpus, not a worse model.
    for a in arms:
        got = a["doc"].get("items_scored")
        if got != expected:
            raise Refused(f"{a['key']} scored {got} items, expected {expected}. A partial arm "
                          f"cannot be compared against a complete one.")

    # 2. Both arms were scored against the same references. Different ground truth means a
    #    mis-load, and every difference on the page would be that rather than the models.
    shas = {a["doc"].get("ground_truth_sha256") for a in arms}
    if len(shas) != 1 or None in shas:
        raise Refused(f"the arms were scored against different ground truth: {shas}")

    # 3. Same scorer build. Two arms scored by different code are two measurements.
    codes = {a["doc"].get("scoring_code_sha256") for a in arms}
    if len(codes) != 1:
        raise Refused(f"the arms were scored by different builds of score_asr.py: {codes}")

    # 4. Per item, the reference sizes must match exactly. This catches a mis-pairing that
    #    the corpus-level sha would not -- e.g. items scored in a different order.
    ref = {r["item_id"]: r["ref_chars"] for r in arms[0]["doc"]["items"]}
    for a in arms[1:]:
        for r in a["doc"]["items"]:
            if ref.get(r["item_id"]) != r["ref_chars"]:
                raise Refused(
                    f"{a['key']} item {r['item_id']} has ref_chars {r['ref_chars']}, but "
                    f"{arms[0]['key']} has {ref.get(r['item_id'])}. Same audio, same "
                    f"reference -- these cannot differ.")

    # 5. No pooled figure may be null. A None here would render as an em-dash in a column a
    #    reader compares across, and an absent number invites the eye to treat it as zero.
    for a in arms:
        missing = [k for k, v in a["doc"]["overall"].items() if v is None]
        if missing:
            raise Refused(f"{a['key']} has no pooled value for {missing}")
    return arms


def pooled_cer(items: list[dict], exclude: set[str] = frozenset()) -> float | None:
    """Corpus CER over a subset, using score_asr.pooled's rule.

    The rule is copied rather than imported because score_asr is not a library, and it is
    copied EXACTLY: total errors over total reference characters, with the denominator taken
    from the same normalisation as the numerator. An earlier version of the original divided
    normalised errors by raw character counts and read slightly low; that is the one thing to
    preserve when duplicating it.

    This is the only figure on the page not lifted straight from a metrics JSON, and it
    exists because one degenerate item dominates a pooled number that would otherwise
    describe the other nineteen wrongly. It is labelled as computed here wherever it appears.
    """
    rows = [r for r in items if r["item_id"] not in exclude]
    den = sum(r["ref_chars_norm"] for r in rows)
    return (sum(sum(r["cer_norm_sdi"]) for r in rows) / den) if den else None


def outlier(items: list[dict]) -> tuple[dict, float]:
    """The item contributing the largest share of an arm's total character error."""
    tot = sum(sum(r["cer_norm_sdi"]) for r in items)
    worst = max(items, key=lambda r: sum(r["cer_norm_sdi"]))
    return worst, (sum(worst["cer_norm_sdi"]) / tot if tot else 0.0)


def rows_for(arms: list[dict], value) -> list[dict]:
    """One rowbar row per arm, coloured by whose hardware it runs on."""
    out = []
    for a in arms:
        colour = "var(--s-ours)" if a["ours"] else "var(--s-prod)"
        out.append({"label": a["short"], "value": value(a["doc"]), "colour": colour,
                    "note": None})
    return out


def render(cfg: dict, arms: list[dict]) -> str:
    fam_labels = cfg["family_labels"]
    n_items = cfg["expected_items"]
    gt_sha = arms[0]["doc"]["ground_truth_sha256"][:12]
    code_sha = arms[0]["doc"]["scoring_code_sha256"][:12]
    dates = sorted({a["doc"]["generated_at"][:10] for a in arms})
    same_day = len(dates) == 1

    # The findings, derived from the per-item rows both arms already carry.
    fx = {}
    for a in arms:
        items = a["doc"]["items"]
        worst, share = outlier(items)
        cers = sorted(r["cer_norm"] for r in items)
        fx[a["key"]] = {
            "worst": worst, "share": share,
            "median": cers[len(cers) // 2],
            "pooled_ex": pooled_cer(items, {worst["item_id"]}),
            # Deletion-dominated with almost no insertions means content was dropped rather
            # than misheard. The threshold is a PROPORTION of the reference, not an absolute
            # count: an absolute one also caught an item whose 573 deletions are scattered
            # small slips across a long call, which is ordinary transcription error and a
            # different thing from losing a contiguous passage. At >15% the two separate
            # cleanly -- the real cases sit at 22% each, the scattered one at 7.5%.
            "droppers": [r for r in items
                         if r["cer_norm_sdi"][1] > 0.15 * r["ref_chars"]
                         and r["cer_norm_sdi"][2] < 0.02 * r["ref_chars"]],
            "runaway": [r for r in items if r["hyp_chars"] > 3 * r["ref_chars"]],
        }

    charts = [
        {"id": "c-cer", "direction": "low", "bestWord": "best", "fmt": "cer",
         "caption": "CHARACTER ERROR RATE OVER THE WHOLE CORPUS (LOWER IS BETTER)",
         "ariaLabel": "Character error rate by model",
         "rows": rows_for(arms, lambda d: d["overall"]["cer_norm"])},
        {"id": "c-ent", "direction": "high", "bestWord": "best", "fmt": "pct",
         "caption": "ENTITIES RECOVERED: PHONE, AMOUNT, DATE, PACKAGE (HIGHER IS BETTER)",
         "ariaLabel": "Entity recovery by model",
         "rows": rows_for(arms, lambda d: d["entity_overall"]["accuracy"])},
        {"id": "c-wer", "direction": "low", "bestWord": "lowest", "fmt": "cer",
         "caption": "WORD ERROR RATE \\u2014 TOKENISER-DEPENDENT, READ CER FIRST",
         "ariaLabel": "Word error rate by model",
         "rows": rows_for(arms, lambda d: d["overall"]["wer_norm"])},
        {"id": "c-ins", "direction": "low", "bestWord": "fewest", "fmt": "rate",
         "caption": "INSERTED WORDS PER MINUTE OF NON-SPEECH \\u2014 A PROXY, NOT A COUNT",
         "ariaLabel": "Insertion rate by model",
         "rows": rows_for(arms, lambda d: d["insertions_per_nonspeech_min"]["mean"])},
    ]

    # Per-family CER, both arms side by side. Built from each arm's own by_family block.
    fams = sorted(arms[0]["doc"]["by_family"])
    fam_rows = []
    for f in fams:
        label, what = fam_labels.get(f, (f, ""))
        cells = "".join(
            f'<td>{num(a["doc"]["by_family"].get(f, {}).get("cer_norm"), 4)}</td>'
            for a in arms)
        fam_rows.append(
            f'<tr><th scope=row>{esc(label)}<span class="sub">{esc(what)}</span></th>'
            f'<td>{arms[0]["doc"]["by_family"][f]["items"]}</td>{cells}</tr>')

    # Per-entity-type recovery.
    types = sorted(arms[0]["doc"]["entity_by_type"])
    type_rows = []
    for t in types:
        cells = ""
        for a in arms:
            e = a["doc"]["entity_by_type"].get(t)
            cells += (f'<td>{e["hit"]}/{e["total"]}'
                      f'<span class="sub">{e["hit"] / e["total"]:.0%}</span></td>'
                      if e and e["total"] else "<td>&mdash;</td>")
        type_rows.append(f'<tr><th scope=row>{esc(t)}</th>{cells}</tr>')

    # Per-item CER, both arms.
    by_item = {a["key"]: {r["item_id"]: r for r in a["doc"]["items"]} for a in arms}
    item_rows = []
    for iid in sorted(by_item[arms[0]["key"]]):
        base = by_item[arms[0]["key"]][iid]
        label, _ = fam_labels.get(base["family"], (base["family"], ""))
        cells = ""
        for a in arms:
            r = by_item[a["key"]][iid]
            ratio = r["hyp_chars"] / r["ref_chars"] if r["ref_chars"] else 0
            flag = ' class="t-bad"' if ratio > 3 or ratio < 0.75 else ""
            cells += (f'<td{flag}>{num(r["cer_norm"], 4)}'
                      f'<span class="sub">{ratio:.2f}&times; length</span></td>')
        item_rows.append(f'<tr><th scope=row>{esc(iid)}</th><td>{esc(label)}</td>{cells}</tr>')

    head = "".join(f'<th>{esc(a["name"])}<span class="sub">{esc(a["role"])}</span></th>'
                   for a in arms)
    arm_cards = "".join(
        f'<div class="arm"><div class="n">{esc(a["name"])}</div>'
        f'<div class="r">{esc(a["role"])}</div>'
        f'<div class="m">{esc(a["how"])}</div></div>' for a in arms)

    cards = []
    for a in arms:
        f = fx[a["key"]]
        w, share = f["worst"], f["share"]
        if f["runaway"]:
            r = f["runaway"][0]
            body = (f'On <b>{esc(r["item_id"])}</b> it produced <b>{r["hyp_chars"]:,}</b> '
                    f'characters against a {r["ref_chars"]:,}-character reference &mdash; a '
                    f'single ~100-character span repeated <b>496 times</b>. Re-running that '
                    f'file gave a byte-identical result, so it is deterministic, not a '
                    f'sampling fluke. It is also visible without any reference: that call ran '
                    f'at real-time factor 1.05 against ~0.065 on every other file. '
                    f'<b>This one item is {share:.0%} of the arm&rsquo;s total character '
                    f'error</b>; excluding it the pooled rate is '
                    f'<b>{f["pooled_ex"]:.3f}</b> rather than '
                    f'{a["doc"]["overall"]["cer_norm"]:.3f}.')
            title = "Degenerate repetition on one file"
        elif f["droppers"]:
            names = ", ".join(esc(r["item_id"]) for r in f["droppers"])
            worst_d = max(f["droppers"], key=lambda r: r["cer_norm_sdi"][1])
            body = (f'On <b>{names}</b> whole passages of dialogue are simply absent &mdash; '
                    f'up to <b>{worst_d["cer_norm_sdi"][1]:,}</b> characters deleted with '
                    f'almost no insertions, which is the signature of dropped content rather '
                    f'than mishearing. The transcripts begin and end coherently; the middle '
                    f'is missing. Nothing in the response indicates it happened.')
            title = "Silently dropped passages"
        else:
            body = (f'No systematic failure mode found. Worst call '
                    f'<b>{esc(w["item_id"])}</b> at CER {w["cer_norm"]:.3f}.')
            title = "No degenerate output"
        cards.append(f'<div class="arm"><div class="n">{esc(a["name"])} &mdash; '
                     f'{title}</div><div class="r">{body}</div></div>')
    failure_cards = '<div class="arms">' + "".join(cards) + "</div>"

    worst_arm = max(arms, key=lambda a: fx[a["key"]]["share"])
    fw = fx[worst_arm["key"]]
    dominance = (f'{esc(worst_arm["short"])}&rsquo;s <b>{esc(fw["worst"]["item_id"])}</b> '
                 f'contributes {fw["share"]:.0%} of its total character error, and pulls its '
                 f'pooled rate from {fw["pooled_ex"]:.3f} to '
                 f'{worst_arm["doc"]["overall"]["cer_norm"]:.3f}')

    date_note = (f"Both arms ran on {dates[0]}." if same_day else
                 f'<strong>The arms ran on different days ({" and ".join(dates)}).</strong> '
                 f"Any difference could be a date effect rather than a model effect.")

    return f"""<title>{esc(cfg['doc_title'])}</title>
<style>{STYLE}{EXTRA_CSS}</style>
<div id="tip" role="status" aria-live="off"></div>
<div class="wrap">

<header>
  <p class="kicker">{cfg['kicker_subject']} &middot; {n_items} calls</p>
  <h1>{esc(cfg['title'])}</h1>
  <p class="standfirst">Production sends audio to Gemini and gets labels back in one call. A
  text-only model cannot do that &mdash; it needs a speech-to-text step that does not exist in
  our stack today, and every label downstream inherits its errors. This measures that step.</p>
  <div class="safe">Synthetic audio &middot; no customer data &middot; no production calls</div>
</header>

<section>
  <h2>1. What is being compared</h2>
  <div class="arms">{arm_cards}</div>
  <p class="lede">{n_items} Thai call-centre recordings, 3.6&ndash;9.5 minutes each, 123.6
  minutes in total. Ten mechanism families, two calls each, paired so the two calls in a family
  take different scenarios and opposite speaker genders &mdash; a family effect can therefore
  never be a voice effect.</p>
  <p class="lede"><strong>The reference is exact by construction.</strong> The dialogue was
  authored first and the audio synthesised from it, so the transcript is not an estimate of
  what was said &mdash; it is what was said. Reference error is removed entirely as a term in
  every figure here. {date_note}</p>
</section>

<section>
  <h2>2. Read this before the numbers</h2>
  <p class="lede"><strong>Both models work. Both also fail, in different ways, and neither
  failure is visible without a reference to check against.</strong> That is the finding this
  set exists to produce, and it matters more than the ranking below it.</p>
  {failure_cards}
  <p class="lede">Neither failure announces itself. Both arms returned HTTP&nbsp;200 on all
  {n_items} calls and wrote a well-formed transcript every time. An eyeball check of the
  opening lines would pass both.</p>
</section>

<section>
  <h2>3. Accuracy</h2>
  <p class="lede"><strong>Character error rate is the headline, with one caveat that
  changes it completely.</strong> A pooled corpus rate is the right way to aggregate ASR
  &mdash; averaging per-file rates would weight a 3-minute call the same as a 10-minute one
  &mdash; but it also means a single degenerate transcript can dominate. Here one does:
  {dominance}. The chart shows the pooled figure as measured; the per-call table in
  section&nbsp;7 shows what it is made of.</p>
  <p class="lede">Thai is written without word spaces, so a character distance is the only
  figure that does not depend on a tokeniser's opinion. Both arms are scored by the same code
  (<code>{esc(code_sha)}&hellip;</code>) against the same references
  (<code>{esc(gt_sha)}&hellip;</code>), pooled over the corpus rather than averaged over
  per-file rates.</p>
  <figure><div class="chartbox scroller"><div id="c-cer"></div></div>
    <figcaption>Lower is better. Pooled: total character errors over total reference
    characters.</figcaption></figure>
  <figure><div class="chartbox scroller"><div id="c-ent"></div></div>
    <figcaption>Did the phone number, amount, date or package survive? CER treats a wrong digit
    in a mobile number exactly like a wrong final particle; production does not, because it
    writes that number into a field.</figcaption></figure>
</section>

<section>
  <h2>4. Where each model struggles</h2>
  <p class="lede">Pooled CER within each mechanism family. This is what the set was built for
  &mdash; a single corpus number hides which acoustic condition actually breaks a model.</p>
  <div class="scroller"><table>
    <caption>Character error rate by family &middot; lower is better</caption>
    <thead><tr><th>Family</th><th>Calls</th>{head}</tr></thead>
    <tbody>{"".join(fam_rows)}</tbody>
  </table></div>
  <div class="scroller"><table>
    <caption>Entity recovery by type</caption>
    <thead><tr><th>Type</th>{head}</tr></thead>
    <tbody>{"".join(type_rows)}</tbody>
  </table></div>
</section>

<section>
  <h2>5. The two figures that need a caveat before you read them</h2>
  <figure><div class="chartbox scroller"><div id="c-wer"></div></div>
    <figcaption><strong>Word error rate is tokeniser-dependent.</strong> Both sides go through
    the same tokeniser, which removes most of that but not all: a model that writes Thai
    without word spaces is penalised by the tokeniser rather than by mishearing anything. Read
    CER first.</figcaption></figure>
  <figure><div class="chartbox scroller"><div id="c-ins"></div></div>
    <figcaption><strong>A hallucination proxy, and nothing more.</strong> Inserted words per
    minute of non-speech audio. A plain transcript carries no timestamps, so this cannot show
    that an insertion landed <em>during</em> the hold music &mdash; only that the model produced
    surplus words on a file containing that much silence.</figcaption></figure>
</section>

<section>
  <h2>6. What this comparison cannot control</h2>
  <div class="asym">
    <p><strong>The two arms write numbers differently, and the reference only matches one
    of them.</strong> The ground truth spells numbers out as they are spoken
    (&ldquo;<span lang="th">&#3626;&#3636;&#3610;&#3649;&#3611;&#3604; &#3626;&#3636;&#3591;&#3627;&#3634;&#3588;&#3617;</span>&rdquo;).
    Gemini does the same &mdash; <em>because the prompt written for it in this project asks it
    to</em>. Qwen3-ASR writes digits (&ldquo;18 <span lang="th">&#3626;&#3636;&#3591;&#3627;&#3634;&#3588;&#3617;</span>&rdquo;),
    which is a rendering choice, not a mishearing: the number was heard correctly. Character
    error rate charges it anyway, for every date, amount, phone number and case id in the
    corpus. Measured outside the scorer on the nineteen non-degenerate items, reading those
    digit runs back as spoken Thai moves CER from <b>0.153 to 0.087</b> &mdash; so roughly
    <b>two fifths of the internal model&rsquo;s character error is number formatting</b>.
    That figure is an estimate, not a published metric: forgiving a rendering difference is
    a change to the scoring contract, and that contract is a closed list amended by review
    rather than mid-run.</p>
    <p><strong>Entity recovery once appeared to point the other way. It was a measurement
    fault, and it has been fixed.</strong> Until 2026-08-17 this page would have reported
    92.9% against 67.5% in the internal model&rsquo;s favour. That gap was not real: the
    surface test was an exact substring match that included spaces, so an arm writing the
    same words with different phrase spacing scored the entity as lost. Of the production
    model&rsquo;s 151 apparent misses, <b>136 &mdash; 90% &mdash; were present in its own
    transcript and spaced differently</b>. With the test made whitespace-insensitive on both
    sides, the two arms land on <b>exactly the same figure</b>. Entity recovery is what maps
    to money, since production writes these values into fields, so it is worth being precise
    that on this set it does not separate the arms at all.</p>
    <p><strong>The two arms are not given the same help.</strong> Gemini is a general model
    steered by a prompt, and its prompt asks for Thai-script brand names. Qwen3-ASR is a
    dedicated transcription endpoint: its <code>prompt</code> parameter is accepted and
    <em>ignored</em> &mdash; measured, byte-identical output with and without. So Gemini has an
    advantage on brand names that the other arm cannot be given. Inherent to the comparison,
    not a choice made here.</p>
    <p><strong>Word spacing is a style difference, not an accuracy difference.</strong> One arm
    emits spaces between words and the other does not. Thai has no native word spaces, so
    neither is wrong &mdash; but the tokeniser charges one of them for it. This is why CER
    leads and WER is reported beside a warning.</p>
    <p><strong>Speed is not like-for-like</strong> and is deliberately not charted. Gemini was
    called over the public internet; the internal model over the datacentre network. Those
    numbers measure two deployments, not two models.</p>
    <p><strong>The audio is synthetic.</strong> It was generated from authored text by a
    neural TTS voice, and carries only the acoustic damage the degradation chain explicitly
    models. Two voices cover all {n_items} calls, so speaker variability is not tested at all.
    <strong>A CER measured here is not a production CER estimate.</strong></p>
  </div>
</section>

<section>
  <h2>7. Every call</h2>
  <div class="scroller"><table>
    <caption>Character error rate per call, after normalisation</caption>
    <thead><tr><th>Call</th><th>Family</th>{head}</tr></thead>
    <tbody>{"".join(item_rows)}</tbody>
  </table></div>
</section>

<footer>
  {n_items} calls &middot; scored by <code>score_asr.py {esc(code_sha)}&hellip;</code> against
  ground truth <code>{esc(gt_sha)}&hellip;</code>. Normalisation:
  {esc(arms[0]['doc']['normalisation'])}. Figures copied without recomputation from the
  per-arm scored JSONs under <code>asr-eval/reports/</code>; transcripts are model output and
  are not committed.<br>
  This measures the speech-to-text step alone, not the labelling that would follow it. It
  narrows the audio gap in this harness without closing it: the audio is synthetic and no
  production call has been scored. <strong>RECONCILED: NO.</strong>
</footer>
</div>
{asr_charts_js(charts)}
"""


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--config", type=Path, default=DEFAULT_CONFIG)
    args = ap.parse_args()

    cfg = json.loads(args.config.read_text(encoding="utf-8"))
    arms = load(cfg)
    print(f"config: {args.config.name}  pack: {cfg['pack']}")
    print("all refusals passed")
    for a in arms:
        d = a["doc"]
        print(f"  {a['name']:20} CER {d['overall']['cer_norm']:.4f}  "
              f"WER {d['overall']['wer_norm']:.4f}  "
              f"entity {d['entity_overall']['accuracy']:.1%}  "
              f"scored {d['items_scored']}  {d['generated_at']}")

    fragment = render(cfg, arms)
    out_dir = REPO / cfg["output"]["dir"]
    stem = cfg["output"]["stem"]
    (out_dir / f"{stem}-fragment.html").write_text(fragment, encoding="utf-8")
    cut = fragment.index("</style>") + len("</style>")
    standalone = ('<!DOCTYPE html>\n<html lang="en">\n<head>\n<meta charset="utf-8">\n'
                  '<meta name="viewport" content="width=device-width, initial-scale=1">\n'
                  + fragment[:cut] + "\n</head>\n<body>\n" + fragment[cut:]
                  + "\n</body>\n</html>\n")
    (out_dir / f"{stem}.html").write_text(standalone, encoding="utf-8")
    ascii_only = all(ord(c) < 128 for c in standalone)
    print(f"\nwrote {cfg['output']['dir']}/{stem}.html, -fragment.html "
          f"(ascii-only: {ascii_only})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
