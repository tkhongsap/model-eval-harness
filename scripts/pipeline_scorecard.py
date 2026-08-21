"""One table: the three end-to-end pipelines, every metric, side by side.

    PYTHONPATH=src python scripts/pipeline_scorecard.py \
        --run out/runs/20260820-e23-with-typhoon --pack asr-eval-v2

WHY ONE TABLE. The E23 numbers currently live in four places -- business F1 in the scorer,
CER and runaways in the ASR reports, latency and tokens in the raw run records, cost only in
the Gemini rows. Reading a migration decision off four artifacts is how a pack-A figure ends
up printed under a pooled heading, which has already happened once in this repository. So
this assembles all of them into a single column-per-pipeline view.

THE THREE COLUMNS are whole pipelines, not models:

    gemini-audio       .wav -> Gemini 2.5 Flash -> JSON          ONE call, no ASR stage
    typhoon-pipeline   .wav -> Typhoon Whisper large-v3 -> Qwen3.8-27B -> JSON
    qwen-pipeline      .wav -> Qwen3-ASR 1.7B    -> Qwen3.8-27B -> JSON

The two internal columns share a labeller and differ ONLY in the transcriber, which is what
makes the comparison between them a clean read on the ASR stage.

FOUR THINGS THIS REFUSES TO DO, each because the alternative reads as a number and is not one:

  * **No ASR row is filled in for Gemini.** It never produces a transcript -- audio goes in
    and JSON comes out of one call. A CER of 0, or a dash quietly formatted like a value,
    would both invite a comparison that does not exist.
  * **No cost is invented for the internal arms.** Gemini bills per call through OpenRouter
    and the figure is recorded per record. Token Factory is internal GPU with no per-call
    price, and printing $0.00 would say "free" where the truth is "not metered here".
  * **Latency is summed across stages for the pipelines.** A two-stage pipeline's wall clock
    is transcription PLUS labelling; reporting only the label call would flatter the internal
    arms against Gemini's single call.
  * **Nothing is averaged over replicates.** Business figures follow the preregistered
    replicate-1 rule; operational figures (latency, tokens) pool every call actually made,
    because that is what the machine really did.
"""

from __future__ import annotations

import argparse
import collections
import hashlib
import importlib.util
import json
import statistics
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent

# arm id -> (display name, transcriber, ASR report stem or None)
COLUMNS = [
    ("gemini-audio", "Gemini 2.5 Flash", "none - direct audio", None),
    ("typhoon-pipeline", "Typhoon + Qwen3.8", "Typhoon Whisper large-v3",
     "typhoon-whisper-large-v3"),
    ("qwen-pipeline", "Qwen ASR + Qwen3.8", "Qwen3-ASR 1.7B", "qwen3-asr-1.7b"),
]

NA = "--"


def pct(x, dp=1):
    return f"{x * 100:.{dp}f}%" if isinstance(x, (int, float)) else NA


def num(x, dp=3):
    return f"{x:.{dp}f}" if isinstance(x, (int, float)) else NA


def load_scorer():
    spec = importlib.util.spec_from_file_location(
        "e23", REPO / "scripts" / "experiment23_score.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def operational(run_dir: Path) -> dict:
    """Per-arm latency, tokens, cost and outcome counts over every call actually made."""
    rows = collections.defaultdict(list)
    for line in (run_dir / "results.jsonl").read_text(encoding="utf-8").splitlines():
        if line.strip():
            record = json.loads(line)
            rows[record["arm"]].append(record)

    out = {}
    for arm, records in rows.items():
        ok = [r for r in records if r.get("status") == "ok"]
        lat = sorted(r["latency_s"] for r in ok if r.get("latency_s") is not None)
        prompt = [(r.get("usage") or {}).get("prompt_tokens") for r in ok]
        compl = [(r.get("usage") or {}).get("completion_tokens") for r in ok]
        prompt = [v for v in prompt if v is not None]
        compl = [v for v in compl if v is not None]
        costs = [(r.get("usage") or {}).get("cost") for r in ok]
        costs = [c for c in costs if isinstance(c, (int, float))]
        # Audio tokens, split out of prompt_tokens, because leaving them merged makes the
        # token row compare two different things. The one-call audio arm's prompt CONTAINS
        # the recording; the pipeline arms' label prompt contains a transcript, and their
        # audio never becomes tokens at all -- it goes to an endpoint that bills GPU time
        # and reports no usage. "11,466 against 3,664" then reads as the pipeline using a
        # third of the tokens, when the truth is that its first stage is missing from the
        # comparison entirely.
        audio = [((r.get("usage") or {}).get("prompt_tokens_details") or {}).get(
            "audio_tokens") or 0 for r in ok]
        text_in = [p - a for p, a in zip(prompt, audio)] if len(audio) == len(prompt) else []
        statuses = collections.Counter(r.get("status", "unknown") for r in records)
        out[arm] = {
            "calls": len(records),
            "ok": len(ok),
            "statuses": statuses,
            "lat_p50": statistics.median(lat) if lat else None,
            "lat_p95": lat[int(len(lat) * 0.95) - 1] if lat else None,
            "lat_max": lat[-1] if lat else None,
            "lat_mean": statistics.mean(lat) if lat else None,
            "prompt_med": statistics.median(prompt) if prompt else None,
            "audio_med": statistics.median(audio) if any(audio) else None,
            "audio_total": sum(audio) if any(audio) else None,
            "text_in_med": statistics.median(text_in) if text_in else None,
            "compl_med": statistics.median(compl) if compl else None,
            "prompt_total": sum(prompt),
            "compl_total": sum(compl),
            # Only meaningful where the provider bills per call and reports it.
            "cost_total": sum(costs) if costs else None,
            "cost_calls": len(costs),
        }
    return out


def input_drift(run_dir: Path) -> dict[str, list[str]]:
    """Which of the run's recorded inputs no longer match the bytes on disk.

    WHY THE SCORECARD CHECKS THIS AND NOT ONLY THE RUNNER. The runner records the sha256 of
    every input before its first model call, and `experiment21_pipeline_delta --score`
    refuses outright when one has changed. This script never called that path, so it would
    happily print a full table over a corpus that had moved underneath the run.

    That is not hypothetical. On 2026-08-21 a `transcribe.py` orphaned by a stopped chain
    kept running for four hours against a wedged backend and overwrote 23 of E24's Typhoon
    transcripts -- hours AFTER the run had been scored. The runner's refusal caught it; this
    script had already published from the same directory and said nothing.

    It does NOT refuse, and the distinction matters. Business figures come from
    `results.jsonl`, the models' recorded answers, so drift cannot reach them. What drift
    reaches is anything derived from the inputs after the fact -- CER, the ASR-stage record,
    a re-read transcript. So this reports precisely, loudly, and lets the honest rows through
    rather than suppressing a valid table because a file changed beside it.
    """
    log_path = run_dir / "run.json"
    if not log_path.is_file():
        return {}
    log = json.loads(log_path.read_text(encoding="utf-8"))
    out: dict[str, list[str]] = {}
    for arm, per_item in (log.get("input_sha256") or {}).items():
        paths = (log.get("input_paths") or {}).get(arm) or {}
        moved = []
        for item, want in per_item.items():
            src = paths.get(item)
            if not src:
                continue
            f = Path(src)
            if not f.is_file():
                moved.append(item)
                continue
            if f.suffix == ".wav":
                got = hashlib.sha256(f.read_bytes()).hexdigest()
            else:
                got = hashlib.sha256(
                    f.read_text(encoding="utf-8").strip().encode("utf-8")).hexdigest()
            if got != want:
                moved.append(item)
        if moved:
            out[arm] = moved
    return out


def asr_stage(pack: Path, stem: str | None) -> dict:
    """CER, runaways and transcription wall clock for one transcriber."""
    if stem is None:
        return {}
    report = pack / "reports" / f"{stem}.json"
    runlog = pack / "hypotheses" / stem / "_run.json"
    out: dict = {}
    if report.exists():
        overall = json.loads(report.read_text(encoding="utf-8")).get("overall", {})
        out.update({
            "cer": overall.get("cer_norm"),
            "wer": overall.get("wer_norm"),
            "scoreable": overall.get("scoreable_items"),
            "runaway_items": overall.get("runaway_items"),
            "runaway_rate": overall.get("runaway_rate"),
        })
        entity = json.loads(report.read_text(encoding="utf-8")).get("entity_overall", {})
        out["entity"] = entity.get("accuracy")
    # Count the transcripts that exist, NOT `_run.json`'s `ok`.
    #
    # `_run.json` is overwritten by whichever invocation wrote last, and a RESUME run
    # records only the items it retried. Qwen's says `ok: 0, failed: 2` because the last
    # thing anyone ran against it was a two-item retry of ASR-082 and ASR-089 -- while 136
    # transcripts sit on disk from the earlier full pass. Reading that field printed
    # "0/138" for an arm that had transcribed 136 calls, which is a wrong number that looks
    # like a catastrophic one.
    out["on_disk"] = len(list((pack / "hypotheses" / stem).glob("ASR-*.txt")))
    if runlog.exists():
        log = json.loads(runlog.read_text(encoding="utf-8"))
        items = [i for i in log.get("items", []) if i.get("status") == "ok"]
        secs = sorted(i["seconds"] for i in items if i.get("seconds") is not None)
        rtfs = [i["rtf"] for i in items if i.get("rtf") is not None]
        out.update({
            "transcribed_ok": log.get("ok"),
            "transcribed_failed": log.get("failed"),
            "asr_sec_med": statistics.median(secs) if secs else None,
            "asr_sec_total": sum(secs) if secs else None,
            "rtf_med": statistics.median(rtfs) if rtfs else None,
            "chunk_seconds": log.get("chunk_seconds"),
        })
    return out


def main() -> int:
    ap = argparse.ArgumentParser(prog="pipeline_scorecard")
    ap.add_argument("--run", type=Path, required=True)
    ap.add_argument("--pack", type=Path, default=REPO / "asr-eval-v2")
    ap.add_argument("--policy", choices=("first", "modal"), default="first")
    ap.add_argument("--json", type=Path, default=None)
    ap.add_argument("--markdown", type=Path, default=None)
    args = ap.parse_args()

    e23 = load_scorer()
    truth, phones = e23.load_truth(args.pack)
    collapsed, unstable, _failures = e23.collapse(args.run, args.policy)
    ops = operational(args.run)

    # EVERY COLUMN IS SCORED ON THE SAME ITEMS, and that is not a formality here.
    #
    # The four original arms ran 136 items; the typhoon arm was added later by a resume and
    # ran 138, because Qwen3-ASR never produced transcripts for ASR-082 and ASR-089 and the
    # original run was therefore restricted to what every arm could answer. Reporting
    # typhoon's F1 over 138 beside the others' over 136 compares arms on different item
    # sets -- exactly the mistake `items()` refuses a mismatched manifest to prevent, and
    # the same class of error as a pack-A figure printed under a pooled heading.
    #
    # The effect measured 2026-08-20 is small (call_result 0.597 over 138 vs 0.600 over the
    # shared 136) but "small" is a fact discovered after restricting, not a reason to skip
    # it. The paired test already intersected; this makes section 1 agree with it.
    present = [arm for arm, *_ in COLUMNS if arm in collapsed]
    shared = set.intersection(*(set(collapsed[arm]) for arm in present)) if present else set()
    dropped = {arm: sorted(set(collapsed[arm]) - shared) for arm in present}

    business: dict = {}
    for arm, *_ in COLUMNS:
        if arm not in collapsed:
            continue
        subset = {k: v for k, v in collapsed[arm].items() if k in shared}
        gt, pred = e23.build_pair(truth, phones, subset)
        # The same three scorers section 1 of experiment23_score uses, called the same way,
        # so this table cannot drift from the report it summarises.
        entry = {
            "call_result": e23.score_call_result(gt, pred, e23.RETENTION.call_result),
            "reason": e23.score_reason(gt, pred, e23.RETENTION.reason),
            "product": e23.score_product(gt, pred, e23.RETENTION.product),
        }
        # Call-level accuracy per dimension, the grain production acts on.
        for dim in ("call_result", "reason", "product"):
            verdicts = e23.call_level_correct(gt, pred, dim)
            entry[f"{dim}_acc"] = (sum(verdicts.values()), len(verdicts))
        entry["_n"] = len(subset)
        business[arm] = entry

    asr = {arm: asr_stage(args.pack, stem) for arm, _n, _t, stem in COLUMNS}

    # ---- render -------------------------------------------------------------------
    heads = [name for _a, name, _t, _s in COLUMNS]
    w = 26
    lines: list[str] = []

    def row(label: str, values: list[str], indent: bool = False) -> None:
        left = ("  " + label) if indent else label
        lines.append(f"{left:<34}" + "".join(f"{v:>{w}}" for v in values))

    def rule(char: str = "-") -> None:
        lines.append(char * (34 + w * len(COLUMNS)))

    lines.append("")
    lines.append("END-TO-END PIPELINE SCORECARD".center(34 + w * len(COLUMNS)))
    lines.append(f"{len(shared)} calls scored in EVERY column  |  replicate policy "
                 f"'{args.policy}'".center(34 + w * len(COLUMNS)))
    lines.append("")
    row("", heads)
    rule("=")

    row("PIPELINE SHAPE", ["" for _ in COLUMNS])
    row("transcriber", [t for _a, _n, t, _s in COLUMNS], indent=True)
    row("labeller", ["(same model)", "Qwen3.8-27B-fp8", "Qwen3.8-27B-fp8"], indent=True)
    row("model calls per item", ["1", "2", "2"], indent=True)
    row("runtime", ["OpenRouter", "Token Factory", "Token Factory"], indent=True)
    row("calls scored (common set)", [
        str(business.get(a, {}).get("_n", 0)) for a, *_ in COLUMNS], indent=True)
    row("items this arm ran", [
        str(len(collapsed.get(a, {}))) for a, *_ in COLUMNS], indent=True)
    rule()

    row("BUSINESS OUTCOME  (primary)", ["" for _ in COLUMNS])
    for stat in ("f1", "precision", "recall"):
        for dim in ("call_result", "reason", "product"):
            vals = []
            for arm, *_ in COLUMNS:
                result = business.get(arm, {}).get(dim)
                # `.weighted("f1")`, not `.f1` -- ClassMetrics.f1 is PER CLASS, and reading
                # it here would silently report one class's score as the arm's.
                vals.append(num(result.weighted(stat)) if result is not None else NA)
            row(f"weighted {stat} - {dim}", vals, indent=True)
    for dim in ("call_result", "reason", "product"):
        vals = []
        for arm, *_ in COLUMNS:
            hit, tot = business.get(arm, {}).get(f"{dim}_acc", (None, None))
            vals.append(f"{hit}/{tot}  ({hit / tot * 100:.1f}%)" if tot else NA)
        row(f"call accuracy - {dim}", vals, indent=True)
    rule()

    row("TRANSCRIPTION STAGE", ["" for _ in COLUMNS])
    def asr_cell(arm: str, key: str, fmt) -> str:
        if not asr[arm]:
            return "no ASR stage"
        value = asr[arm].get(key)
        return fmt(value) if value is not None else "not recorded"

    row("CER (normalised)", [asr_cell(a, "cer", lambda v: num(v, 4)) for a, *_ in COLUMNS],
        indent=True)
    row("WER (normalised)", [asr_cell(a, "wer", lambda v: num(v, 4)) for a, *_ in COLUMNS],
        indent=True)
    row("entity accuracy", [asr_cell(a, "entity", pct) for a, *_ in COLUMNS], indent=True)
    row("calls transcribed", [
        asr_cell(a, "on_disk", lambda v: "%d/138" % v) for a, *_ in COLUMNS], indent=True)
    row("runaway failures", [
        ("no ASR stage" if not asr[a] else
         "%d  (%.1f%%)" % (asr[a]["runaway_items"], asr[a]["runaway_rate"] * 100)
         if asr[a].get("runaway_items") is not None else "not recorded")
        for a, *_ in COLUMNS], indent=True)
    row("scoreable after exclusion", [
        asr_cell(a, "scoreable", str) for a, *_ in COLUMNS], indent=True)
    rule()

    row("LATENCY  (seconds)", ["" for _ in COLUMNS])

    def asr_latency(arm: str) -> str:
        """Three states, three different words -- never one shared dash.

        Gemini has NO transcription stage. Qwen HAS one whose per-item timings were
        overwritten when a two-item resume rewrote `_run.json`. Printing the same "--" for
        both would say the two arms are alike in a way they are not.
        """
        if not asr[arm]:
            return "no ASR stage"
        value = asr[arm].get("asr_sec_med")
        return num(value, 1) if value is not None else "not recorded"

    row("ASR stage, median", [asr_latency(a) for a, *_ in COLUMNS], indent=True)
    row("label call, median", [num(ops.get(a, {}).get("lat_p50"), 1) for a, *_ in COLUMNS],
        indent=True)
    row("label call, p95", [num(ops.get(a, {}).get("lat_p95"), 1) for a, *_ in COLUMNS],
        indent=True)
    row("label call, max", [num(ops.get(a, {}).get("lat_max"), 1) for a, *_ in COLUMNS],
        indent=True)
    end_to_end = []
    for arm, *_ in COLUMNS:
        label = ops.get(arm, {}).get("lat_p50")
        if label is None:
            end_to_end.append(NA)
            continue
        if not asr[arm]:                       # single-call arm: label call IS the pipeline
            end_to_end.append(num(label, 1))
            continue
        stage = asr[arm].get("asr_sec_med")
        if stage is None:
            # Refuse to add zero for a stage that ran and simply was not timed. That would
            # report a two-stage pipeline as faster than it is, in the direction that
            # flatters it.
            end_to_end.append(">%.1f (ASR untimed)" % label)
        else:
            end_to_end.append(num(label + stage, 1))
    row("END TO END, median", end_to_end, indent=True)
    row("ASR real-time factor", [
        ("no ASR stage" if not asr[a]
         else num(asr[a].get("rtf_med"), 3) if asr[a].get("rtf_med") is not None
         else "not recorded") for a, *_ in COLUMNS], indent=True)
    rule()

    # TOKENS, BY STAGE. A single "input tokens" row across these two shapes compares the
    # audio arm's ONE call -- which carries the recording as tokens -- against the pipeline
    # arms' SECOND call, which carries text. The pipeline's first stage, which ingests the
    # same audio, is absent from that row because /v1/audio/transcriptions reports no usage.
    # Both shapes are handed identical audio; only one denominates it in tokens.
    row("TOKENS  (see note)", ["" for _ in COLUMNS])

    def tok(key, arm):
        value = ops.get(arm, {}).get(key)
        return f"{value:,.0f}" if value else None

    row("stage 1 in - audio", [
        (tok("audio_med", a) or NA) if a == "gemini-audio"
        else ("not token-metered" if asr.get(a, {}).get("chunk_seconds") else NA)
        for a, *_ in COLUMNS], indent=True)
    row("stage 2 in - text", [
        "no second call" if a == "gemini-audio" else (tok("text_in_med", a) or NA)
        for a, *_ in COLUMNS], indent=True)
    row("prompt+schema, in above", [
        tok("text_in_med", a) or NA if a == "gemini-audio" else "included above"
        for a, *_ in COLUMNS], indent=True)
    row("billed input, median", [
        tok("prompt_med", a) or NA for a, *_ in COLUMNS], indent=True)
    row("output, median", [
        tok("compl_med", a) or NA for a, *_ in COLUMNS], indent=True)
    row("billed input, total", [
        f"{ops.get(a, {}).get('prompt_total', 0):,}" for a, *_ in COLUMNS], indent=True)
    row("output, total", [
        f"{ops.get(a, {}).get('compl_total', 0):,}" for a, *_ in COLUMNS], indent=True)
    rule()

    row("RELIABILITY", ["" for _ in COLUMNS])
    row("label calls made", [str(ops.get(a, {}).get("calls", 0)) for a, *_ in COLUMNS],
        indent=True)
    row("parse failures", [
        f"{ops[a]['statuses'].get('parse_failed', 0)}/{ops[a]['calls']}"
        if a in ops else NA for a, *_ in COLUMNS], indent=True)
    row("unstable items (of 3 reps)", [
        f"{unstable.get(a, 0)}/{len(collapsed.get(a, {}))}" for a, *_ in COLUMNS],
        indent=True)
    rule()

    row("COST", ["" for _ in COLUMNS])
    cost_vals = []
    for arm, *_ in COLUMNS:
        total = ops.get(arm, {}).get("cost_total")
        cost_vals.append(f"${total:.4f}" if total is not None else "internal GPU")
    row("metered cost, all calls", cost_vals, indent=True)
    per_call = []
    for arm, *_ in COLUMNS:
        total = ops.get(arm, {}).get("cost_total")
        n = ops.get(arm, {}).get("cost_calls") or 0
        per_call.append(f"${total / n:.5f}" if total is not None and n else "not metered")
    row("metered cost, per call", per_call, indent=True)
    rule("=")

    # The cap is arithmetic, not a model property, and without it the F1 column reads as
    # though every arm is simply bad.
    # A class no arm predicts caps the reachable F1 -- but WHY it goes unpredicted decides
    # whether that cap is a model limitation or a corpus defect, and the first version of
    # this note asserted the former without checking.
    #
    # It is the latter, measured 2026-08-20. `retention_v9_16_body.txt:80` rules that a
    # client who "expresses indecision or asks for time to think" is a `save`. The corpus's
    # entire `unknown` pool (asr-eval/scripts/thai_corpus.py:634-643) is exactly those
    # indecision phrases, so a model FOLLOWING ITS SPEC must answer `save` on all of them --
    # and does, on 174 of 186 blocks. The same models emit `unknown` 33 times and `undefined`
    # 19 times on the text packs (run 20260814-132425Z-e17-gemini), so they are willing.
    #
    # Emission is therefore MEASURED here rather than assumed, and the wording distinguishes
    # "no arm predicted this" from "no arm can".
    import collections as _c

    gt_all, _ = e23.build_pair(truth, phones,
                               {k: v for k, v in collapsed[present[0]].items()
                                if k in shared}) if present else ([], [])
    support = _c.Counter(r.call_result for r in gt_all)
    emitted: _c.Counter = _c.Counter()
    for arm, *_ in COLUMNS:
        if arm not in collapsed:
            continue
        _gt, arm_pred = e23.build_pair(
            truth, phones, {k: v for k, v in collapsed[arm].items() if k in shared})
        emitted.update(r.call_result for r in arm_pred)

    unpredicted = [c for c in ("unknown", "undefined")
                   if support.get(c) and not emitted.get(c)]
    if unpredicted:
        blocked = sum(support[c] for c in unpredicted)
        total = sum(support.values())
        lines.append("")
        lines.append(f"  {' and '.join(unpredicted)} were never predicted by any arm here, "
                     f"and are {blocked} of")
        lines.append(f"  {total} ground-truth rows -- so the highest weighted F1 reachable "
                     f"on this run is")
        lines.append(f"  {(total - blocked) / total:.3f}, not 1.000.")
        lines.append("")
        lines.append("  That is a CORPUS limitation, not a model one, and it is a KNOWN "
                     "one rather than a")
        lines.append("  discovery. `unknown` used to sit here too: the corpus built those "
                     "calls from the")
        lines.append("  indecision phrases retention_v9_16_body.txt:80 explicitly rules to "
                     "be a `save`, so a")
        lines.append("  spec-obeying model could never label them right. That was fixed on "
                     "2026-08-20 and")
        lines.append("  `unknown` now scores 0.966.")
        lines.append("")
        lines.append("  `undefined` resisted the same treatment and the reason is worth "
                     "keeping. The spec")
        lines.append("  (body:82) requires the FOCUS of the call to be outside retention; "
                     "these calls are")
        lines.append("  68-86 turns of a real retention conversation with one closing line "
                     "saying otherwise,")
        lines.append("  and the labeller answers `save` on 7 of 8. It is right to. Fixing it "
                     "needs a scenario")
        lines.append("  whose BODY is out of scope -- a new generator branch, not another "
                     "sentence.")

    lines.append("")
    drift = input_drift(args.run)
    if drift:
        lines.append("")
        lines.append("!! INPUT DRIFT -- files this run READ have changed on disk since")
        for arm, items in sorted(drift.items()):
            shown = ", ".join(items[:6]) + (" ..." if len(items) > 6 else "")
            lines.append(f"   {arm}: {len(items)} item(s) -- {shown}")
        lines.append("   The BUSINESS figures above are unaffected: they are computed from")
        lines.append("   results.jsonl, the models' recorded answers, not from these files.")
        lines.append("   Anything derived from the inputs AFTER the run -- CER, the ASR-stage")
        lines.append("   record -- cannot be trusted for this run and is not reported.")
        lines.append("   Verify with: python scripts/experiment21_pipeline_delta.py --score "
                     f"{args.run}")
        lines.append("")

    lines.append("READING THIS TABLE")
    lines.append("  Gemini has no transcription stage -- audio goes in and JSON comes out of")
    lines.append("  ONE call -- so every ASR row is blank for it rather than zero.")
    lines.append("  Typhoon and Qwen share the same labeller and differ only in the")
    lines.append("  transcriber, so the gap between those two columns IS the ASR stage.")
    lines.append("  Cost is metered only on OpenRouter; the internal arms run on company GPU")
    lines.append("  with no per-call price, which is not the same as being free.")
    lines.append("  TOKENS ARE NOT ONE NUMBER HERE. Both shapes are handed the SAME audio.")
    lines.append("  The one-call arm's prompt carries that audio as tokens and is billed for")
    lines.append("  it; the pipeline arms send it to a transcription endpoint that reports no")
    lines.append("  usage at all and bills GPU time, then send only TEXT to the labeller. So")
    lines.append("  a single input-token row would compare one arm's whole job against the")
    lines.append("  other's second half. The rows are split by stage for that reason, and")
    lines.append("  'billed input' is what each runtime actually charges for, not what each")
    lines.append("  pipeline consumed.")
    lines.append("  Business figures follow the preregistered replicate-1 rule and are")
    lines.append("  restricted to the calls EVERY column answered; latency and tokens pool")
    lines.append("  every call actually made, which is why their denominators differ.")
    for arm, missing in dropped.items():
        if missing:
            lines.append(f"  {arm}: {len(missing)} item(s) held out of the business figures "
                         f"because no other arm ran them -- {', '.join(missing)}.")

    text = "\n".join(lines)
    print(text)

    if args.markdown:
        args.markdown.parent.mkdir(parents=True, exist_ok=True)
        args.markdown.write_text(text, encoding="utf-8", newline="\n")
    if args.json:
        args.json.parent.mkdir(parents=True, exist_ok=True)
        args.json.write_text(json.dumps({
            "run": args.run.name, "policy": args.policy,
            "operational": {k: {kk: (dict(vv) if isinstance(vv, collections.Counter) else vv)
                                for kk, vv in v.items()} for k, v in ops.items()},
            "asr": asr,
        }, indent=2, default=str), encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
