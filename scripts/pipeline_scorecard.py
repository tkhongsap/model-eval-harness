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
            "compl_med": statistics.median(compl) if compl else None,
            "prompt_total": sum(prompt),
            "compl_total": sum(compl),
            # Only meaningful where the provider bills per call and reports it.
            "cost_total": sum(costs) if costs else None,
            "cost_calls": len(costs),
        }
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

    business: dict = {}
    for arm, *_ in COLUMNS:
        if arm not in collapsed:
            continue
        gt, pred = e23.build_pair(truth, phones, collapsed[arm])
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
    lines.append(f"138 synthetic Thai retention calls  |  replicate policy '{args.policy}'"
                 .center(34 + w * len(COLUMNS)))
    lines.append("")
    row("", heads)
    rule("=")

    row("PIPELINE SHAPE", ["" for _ in COLUMNS])
    row("transcriber", [t for _a, _n, t, _s in COLUMNS], indent=True)
    row("labeller", ["(same model)", "Qwen3.8-27B-fp8", "Qwen3.8-27B-fp8"], indent=True)
    row("model calls per item", ["1", "2", "2"], indent=True)
    row("runtime", ["OpenRouter", "Token Factory", "Token Factory"], indent=True)
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

    row("TOKENS  (label call)", ["" for _ in COLUMNS])
    row("input, median", [
        f"{ops.get(a, {}).get('prompt_med'):,.0f}" if ops.get(a, {}).get("prompt_med")
        else NA for a, *_ in COLUMNS], indent=True)
    row("output, median", [
        f"{ops.get(a, {}).get('compl_med'):,.0f}" if ops.get(a, {}).get("compl_med")
        else NA for a, *_ in COLUMNS], indent=True)
    row("input, total", [
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

    lines.append("")
    lines.append("READING THIS TABLE")
    lines.append("  Gemini has no transcription stage -- audio goes in and JSON comes out of")
    lines.append("  ONE call -- so every ASR row is blank for it rather than zero.")
    lines.append("  Typhoon and Qwen share the same labeller and differ only in the")
    lines.append("  transcriber, so the gap between those two columns IS the ASR stage.")
    lines.append("  Cost is metered only on OpenRouter; the internal arms run on company GPU")
    lines.append("  with no per-call price, which is not the same as being free.")
    lines.append("  Business figures follow the preregistered replicate-1 rule; latency and")
    lines.append("  tokens pool every call actually made.")

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
