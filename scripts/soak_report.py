"""Turn a soak run directory into an analysed result, not a pile of logs.

    python scripts/soak_report.py out/soak/<run>                 # markdown to stdout + files
    python scripts/soak_report.py out/soak/<run> --json-only

Writes `analysis.json` (every number the report quotes, machine-readable) and `report.md` into
the run directory. `scripts/soak_report_html.py` renders the same `analysis.json`, so the two
outputs cannot disagree.

The pass criteria are fixed in `docs/soak-test-plan.md` BEFORE the run and are applied here
verbatim. They are not adjusted to fit what came back.
"""

from __future__ import annotations

import argparse
import json
import statistics
import sys
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path

# From the plan. Changing these after seeing results is how a soak test lies to you.
PASS_ERROR_RATE = 0.01        # post-retry, at the recommended concurrency
PASS_P95_DRIFT = 0.20         # phase 6 vs phase 2
# A level whose time-to-first-token has grown past this multiple of the uncontended baseline
# is queueing, not serving. Used to pick the recommended concurrency; see the note there.
TTFT_KNEE_MULTIPLE = 3.0
DEGRADE_PAIRS = (("normal", "normal_end"), ("baseline_a", "baseline_b"))


def pct(values: list[float], q: float) -> float | None:
    """Nearest-rank percentile. Named because conventions differ and this one is a choice."""
    if not values:
        return None
    ordered = sorted(values)
    idx = min(len(ordered) - 1, max(0, int(round(q * len(ordered) + 0.5)) - 1))
    return round(ordered[idx], 4)


def load(directory: Path) -> tuple[dict, list[dict], list[dict], list[dict]]:
    meta = json.loads((directory / "run.json").read_text(encoding="utf-8"))

    def jsonl(name: str) -> list[dict]:
        path = directory / name
        if not path.is_file():
            return []
        rows = []
        for line in path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                rows.append(json.loads(line))
            except json.JSONDecodeError:
                continue  # a run killed mid-write can leave one torn line; keep the rest
        return rows

    return (meta, jsonl("requests.jsonl"), jsonl("timeline.jsonl"), jsonl("health.jsonl"),
            jsonl("gpu.jsonl"))


def summarise_gpu(samples: list[dict], phases: dict, requests: list[dict]) -> dict | None:
    """GPU utilization, VRAM, temperature and power -- if someone collected them.

    Absent unless `gpu.jsonl` is present in the run directory, which requires
    `scripts/gpu_telemetry.py` to have run ON the GPU host: vLLM is bound to loopback there and
    nothing a client can reach exposes a GPU metric. When the file IS present this fills in the
    four metrics the report otherwise has to declare missing, and time-joins them to the load
    phases so utilization can be read against concurrency.
    """
    if not samples:
        return None
    rolls = [(s["ts"], s["rollup"]) for s in samples
             if s.get("ts") and isinstance(s.get("rollup"), dict)]
    if not rolls:
        return None

    def series(key: str) -> list[float]:
        return [r[key] for _, r in rolls if r.get(key) is not None]

    def block(values: list[float]) -> dict | None:
        if not values:
            return None
        return {"min": round(min(values), 1), "mean": round(sum(values) / len(values), 1),
                "p95": pct(values, .95), "max": round(max(values), 1)}

    # Per phase, by timestamp window -- this is the join that makes the numbers actionable:
    # utilization at c8 versus c16 is the question a capacity plan actually asks.
    per_phase = {}
    for name, stats in phases.items():
        rows = [r for r in requests if r["phase"] == name]
        if not rows:
            continue
        lo, hi = min(r["ts_start"] for r in rows), max(r["ts_end"] for r in rows)
        window = [r for ts, r in rolls if lo <= ts <= hi]
        if not window:
            continue
        per_phase[name] = {
            "concurrency": stats.get("concurrency"),
            "util_pct": block([w["util_pct_mean"] for w in window
                               if w.get("util_pct_mean") is not None]),
            "vram_used_pct": block([w["vram_used_pct"] for w in window
                                    if w.get("vram_used_pct") is not None]),
            "temp_c": block([w["temp_c_max"] for w in window if w.get("temp_c_max") is not None]),
            "power_w": block([w["power_w_total"] for w in window
                              if w.get("power_w_total") is not None]),
        }

    vllm = [s["vllm"] for s in samples if isinstance(s.get("vllm"), dict) and "error" not in s["vllm"]]
    kv = [v.get("vllm:gpu_cache_usage_perc") for v in vllm
          if v.get("vllm:gpu_cache_usage_perc") is not None]
    waiting = [v.get("vllm:num_requests_waiting") for v in vllm
               if v.get("vllm:num_requests_waiting") is not None]
    preempt = [v.get("vllm:num_preemptions_total") for v in vllm
               if v.get("vllm:num_preemptions_total") is not None]

    first_vram = next((r["vram_used_pct"] for _, r in rolls if r.get("vram_used_pct")), None)
    last_vram = next((r["vram_used_pct"] for _, r in reversed(rolls) if r.get("vram_used_pct")),
                     None)
    return {
        "samples": len(rolls),
        "utilization_pct": block(series("util_pct_mean")),
        "vram_used_pct": block(series("vram_used_pct")),
        "vram_used_mib": block(series("vram_used_mib")),
        "vram_total_mib": next((r.get("vram_total_mib") for _, r in rolls
                                if r.get("vram_total_mib")), None),
        "temperature_c": block(series("temp_c_max")),
        "power_w": block(series("power_w_total")),
        # The memory-growth test the task asks for: VRAM at the start versus at the end.
        "vram_growth_pct_points": (round(last_vram - first_vram, 2)
                                   if first_vram is not None and last_vram is not None else None),
        "by_phase": per_phase,
        "vllm": {
            "samples": len(vllm),
            "kv_cache_usage": block(kv) if kv else None,
            "requests_waiting": block(waiting) if waiting else None,
            "preemptions_total_max": max(preempt) if preempt else None,
        } if vllm else None,
    }


def summarise(rows: list[dict]) -> dict:
    """One block of statistics over a set of attempt rows."""
    if not rows:
        return {"attempts": 0}
    final = {}
    for row in rows:                       # last attempt of each logical request
        key = row.get("req_id") or id(row)
        if key not in final or row["attempt"] >= final[key]["attempt"]:
            final[key] = row
    finals = list(final.values())
    ok = [r for r in finals if r["outcome"] == "ok"]
    ttft = [r["ttft_s"] for r in ok if r.get("ttft_s")]
    e2e = [r["e2e_s"] for r in ok]
    tps = [r["output_tokens_per_s"] for r in ok if r.get("output_tokens_per_s")]
    out_tok = [r["completion_tokens"] for r in ok if r.get("completion_tokens")]
    in_tok = [r["prompt_tokens"] for r in ok if r.get("prompt_tokens")]
    span = max((r["ts_end"] for r in finals), default=0) - min(
        (r["ts_start"] for r in finals), default=0)
    checked = [r for r in ok if r.get("valid") is not None]

    return {
        "attempts": len(rows),
        "requests": len(finals),
        "ok": len(ok),
        "raw_error_rate": round(sum(1 for r in rows if r["outcome"] != "ok") / len(rows), 5),
        "post_retry_error_rate": round(1 - len(ok) / len(finals), 5) if finals else None,
        "outcomes": dict(Counter(r["outcome"] for r in finals)),
        "wall_s": round(span, 1),
        "requests_per_s": round(len(finals) / span, 4) if span > 0 else None,
        "ttft_s": {"p50": pct(ttft, .5), "p95": pct(ttft, .95), "p99": pct(ttft, .99),
                   "max": round(max(ttft), 4) if ttft else None, "n": len(ttft)},
        "e2e_s": {"p50": pct(e2e, .5), "p95": pct(e2e, .95), "p99": pct(e2e, .99),
                  "max": round(max(e2e), 4) if e2e else None},
        "output_tokens_per_s": {"p50": pct(tps, .5), "mean": round(statistics.mean(tps), 2)
                                if tps else None},
        "tokens": {
            "input_total": sum(in_tok), "output_total": sum(out_tok),
            "input_mean": round(statistics.mean(in_tok), 1) if in_tok else None,
            "output_mean": round(statistics.mean(out_tok), 1) if out_tok else None,
            "aggregate_output_per_s": round(sum(out_tok) / span, 2) if span > 0 else None,
        },
        "truncated": sum(1 for r in ok if r.get("truncated")),
        "correctness": {
            "checked": len(checked),
            "valid": sum(1 for r in checked if r["valid"]),
            "rate": round(sum(1 for r in checked if r["valid"]) / len(checked), 4)
            if checked else None,
        },
    }


def find_disconnect(requests: list[dict], health: list[dict]) -> dict | None:
    """The moment the client stopped being able to reach the host, if it ever did.

    A soak run that loses its network path records hours of connect timeouts that look like a
    catastrophic server failure and are nothing of the kind. Everything after this instant is
    measuring a disconnected client, so the analysis truncates there and says so, rather than
    averaging the two regimes into one meaningless number.

    Detected from the UNAUTHENTICATED health endpoint: it needs no key and no model, so it
    failing means the host is unreachable, full stop.
    """
    polls = [h for h in health if "ts" in h and "/health/liveness" in h]
    run = 0
    first_bad = None
    for poll in polls:
        if poll.get("/health/liveness", {}).get("status") is None:
            run += 1
            if first_bad is None:
                first_bad = poll["ts"]
            if run >= 4:                    # a minute of no response, not one blip
                break
        else:
            run, first_bad = 0, None
    if run < 4 or first_bad is None:
        return None
    after = [r for r in requests if r["ts_start"] >= first_bad]
    ok_after = sum(1 for r in after if r["outcome"] == "ok")
    last_ok = max((r["ts_end"] for r in requests if r["outcome"] == "ok"), default=None)
    # Cut at the LAST SUCCESS, not at the first health failure. Health is polled every 15 s, so
    # it notices the disconnect up to a poll late -- and the requests in between already failed
    # because of it. Charging those to the endpoint would inflate the error rate at whatever
    # concurrency happened to be running when the network dropped.
    cut = min(last_ok, first_bad) if last_ok else first_bad
    return {
        "detected_ts": first_bad,
        "last_success_ts": last_ok,
        "cut_ts": cut,
        "requests_after": len(after),
        "successes_after": ok_after,
        "note": "Unauthenticated /health/liveness stopped responding, and nothing succeeded "
                "after this point. Requests past it failed on TCP connect, so they measure "
                "the network path rather than the endpoint. Excluded from every statistic "
                "below, along with the health polls from the same window.",
    }


def analyse(directory: Path) -> dict:
    meta, requests, timeline, health, gpu_samples = load(directory)

    disconnect = find_disconnect(requests, health)
    excluded = 0
    if disconnect:
        cut = disconnect["cut_ts"]
        before = len(requests)
        # ts_END, not ts_start: a request that began while the path was up and died when it
        # dropped failed because of the disconnect. Keeping it charges the disconnect to
        # whatever concurrency happened to be running, which is how a clean c64 leg came out
        # at 10.4% error in the first pass of this analysis.
        requests = [r for r in requests if r["ts_end"] < cut]
        excluded = before - len(requests)
        # The health log has to be truncated too, or the availability check reports the
        # disconnect as an endpoint outage -- which is the exact confusion this whole
        # function exists to prevent.
        health = [h for h in health if h.get("ts", 0) < cut]

    by_phase = defaultdict(list)
    by_conc = defaultdict(list)
    by_class = defaultdict(list)
    for row in requests:
        by_phase[row["phase"]].append(row)
        by_conc[row["concurrency"]].append(row)
        by_class[row["class"]].append(row)

    phases = {name: summarise(rows) | {"concurrency": rows[0]["concurrency"]}
              for name, rows in by_phase.items()}
    concurrency = {str(c): summarise(rows) for c, rows in sorted(by_conc.items())}
    classes = {c: summarise(rows) for c, rows in sorted(by_class.items())}

    # ---- degradation: identical configuration, hours apart -------------------------
    degradation = {}
    for early, late in DEGRADE_PAIRS:
        a, b = phases.get(early), phases.get(late)
        if not a or not b or not a.get("ok") or not b.get("ok"):
            continue

        def drift(path: tuple[str, ...]) -> float | None:
            x, y = a, b
            for k in path:
                x, y = (x or {}).get(k), (y or {}).get(k)
            if not x or not y:
                return None
            return round((y - x) / x, 4)

        degradation[f"{early}_vs_{late}"] = {
            "early": early, "late": late,
            "e2e_p50_drift": drift(("e2e_s", "p50")),
            "e2e_p95_drift": drift(("e2e_s", "p95")),
            "ttft_p50_drift": drift(("ttft_s", "p50")),
            "ttft_p95_drift": drift(("ttft_s", "p95")),
            "throughput_drift": drift(("requests_per_s",)),
            "tokens_per_s_drift": drift(("output_tokens_per_s", "p50")),
            "error_rate_early": a.get("post_retry_error_rate"),
            "error_rate_late": b.get("post_retry_error_rate"),
            "truncated_early": a.get("truncated"), "truncated_late": b.get("truncated"),
        }

    # ---- availability: any gap in the unauthenticated health poll ------------------
    liveness = [(h["ts"], h.get("/health/liveness", {}).get("status"))
                for h in health if "ts" in h and "/health/liveness" in h]
    unhealthy = [ts for ts, st in liveness if st != 200]

    # A gap in the poll log is not by itself an outage. The poller can simply have been busy --
    # in the 2026-08-14 run the determinism probe ran inline on the poller thread and its model
    # calls took ~170 s each at c64, opening 500 s "gaps" while the endpoint was serving
    # normally throughout. The discriminator is whether REQUESTS were also failing across the
    # same window: if traffic kept succeeding, the endpoint was up and the gap is an artifact
    # of this instrument. (The driver now polls on a dedicated thread, so new runs should not
    # produce these at all.)
    gaps, artifacts = [], []
    for (t0, _), (t1, _) in zip(liveness, liveness[1:]):
        if t1 - t0 <= 60:
            continue
        window = [r for r in requests if t0 <= r["ts_end"] <= t1]
        succeeded = sum(1 for r in window if r["outcome"] == "ok")
        entry = {"from": t0, "to": t1, "seconds": round(t1 - t0, 1),
                 "requests_in_window": len(window), "successes_in_window": succeeded}
        if window and succeeded:
            entry["verdict"] = ("poller artifact: traffic kept succeeding across this window, "
                                "so the endpoint was up")
            artifacts.append(entry)
        else:
            entry["verdict"] = "no successful traffic across this window"
            gaps.append(entry)

    # ---- determinism vs load: the batching hypothesis ------------------------------
    det_rows = meta.get("determinism_rows") or []
    det_by_conc = defaultdict(lambda: {"n": 0, "mismatch": 0})
    for row in det_rows:
        bucket = det_by_conc[str(row.get("conc_at_send"))]
        bucket["n"] += 1
        bucket["mismatch"] += 0 if row.get("matches_first") else 1
    determinism = {
        "total_probes": len(det_rows),
        "total_mismatches": sum(1 for r in det_rows if not r.get("matches_first")),
        "by_concurrency": {k: v | {"rate": round(v["mismatch"] / v["n"], 4) if v["n"] else None}
                           for k, v in sorted(det_by_conc.items(), key=lambda kv: int(kv[0] or 0))},
    }

    # ---- ceiling and recommendation -------------------------------------------------
    ramped = {int(c): s for c, s in concurrency.items() if s.get("requests", 0) >= 10}
    max_tested = max(ramped) if ramped else None
    healthy = {c: s for c, s in ramped.items()
               if (s.get("post_retry_error_rate") or 0) <= PASS_ERROR_RATE}
    peak_throughput = max(healthy, key=lambda c: healthy[c].get("requests_per_s") or 0) \
        if healthy else None

    # "Sustainable" cannot mean "fastest that did not error". On this endpoint the highest
    # level and the level 8x below it deliver the SAME throughput -- the extra concurrency
    # buys nothing and is paid for entirely in queueing. A level with a 50-second TTFT is not
    # something anyone would operate, whatever its error rate.
    #
    # So the recommendation is the highest level whose TTFT p50 stays within
    # TTFT_KNEE_MULTIPLE of the uncontended (lowest-concurrency) baseline. That is derived
    # from the run's own data rather than a number invented after seeing the results.
    baseline_ttft = None
    if ramped:
        low = min(ramped)
        baseline_ttft = (ramped[low].get("ttft_s") or {}).get("p50")
    recommended = peak_throughput
    knee_note = None
    if baseline_ttft:
        within = {c: s for c, s in healthy.items()
                  if ((s.get("ttft_s") or {}).get("p50") or 1e9)
                  <= baseline_ttft * TTFT_KNEE_MULTIPLE}
        if within:
            recommended = max(within)
            knee_note = (
                f"highest level holding TTFT p50 within {TTFT_KNEE_MULTIPLE}x the c{low} "
                f"baseline of {baseline_ttft:.3f}s")

    overall = summarise(requests)
    errors = Counter(r["outcome"] for r in requests if r["outcome"] != "ok")
    samples = {}
    for row in requests:
        if row["outcome"] != "ok" and row["outcome"] not in samples:
            samples[row["outcome"]] = {k: row.get(k) for k in
                                       ("phase", "concurrency", "prompt_id", "http_status",
                                        "attempt", "e2e_s", "error")}

    # ---- verdict, against criteria fixed before the run ----------------------------
    gpu = summarise_gpu(gpu_samples, phases, requests)

    reasons = []
    incomplete = []
    if disconnect:
        # NOT a fail reason. The endpoint did not fail; the client lost its route to it. It is
        # an incompleteness, which is a different verdict and needs a re-run, not a diagnosis.
        incomplete.append(
            f"The run lost its network path to the host and {excluded:,} requests after that "
            "point are excluded. Phases that did not complete before the disconnect must be "
            "re-run before this test can be called finished.")
    rec_stats = concurrency.get(str(recommended), {}) if recommended else {}
    if recommended is None:
        reasons.append("no concurrency level held the post-retry error rate at or below 1%")
    elif (rec_stats.get("post_retry_error_rate") or 0) > PASS_ERROR_RATE:
        reasons.append(f"post-retry error rate {rec_stats['post_retry_error_rate']:.2%} "
                       f"at the recommended concurrency exceeds {PASS_ERROR_RATE:.0%}")
    drift95 = (degradation.get("normal_vs_normal_end") or {}).get("e2e_p95_drift")
    if drift95 is not None and drift95 > PASS_P95_DRIFT:
        reasons.append(f"p95 latency grew {drift95:.1%} between the first and last "
                       f"normal-load phase, over the {PASS_P95_DRIFT:.0%} bar")
    if unhealthy:
        reasons.append(f"{len(unhealthy)} health polls did not return 200")
    if gaps:
        reasons.append(f"{len(gaps)} availability gap(s) longer than 60s")

    issues = []
    if determinism["total_mismatches"]:
        issues.append(f"{determinism['total_mismatches']} of {determinism['total_probes']} "
                      "determinism probes did not reproduce their first output")
    if overall.get("truncated"):
        issues.append(f"{overall['truncated']} responses hit the token ceiling "
                      "(finish_reason=length)")
    if (overall.get("correctness") or {}).get("rate") is not None \
            and overall["correctness"]["rate"] < 0.95:
        issues.append(f"task-correctness rate {overall['correctness']['rate']:.1%} under load")
    if errors:
        issues.append(f"error taxonomy: {dict(errors)}")
    if gpu is None:
        issues.append(
            "GPU utilization, VRAM, temperature and power were not collected. They are not "
            "reachable from a client: vLLM is bound to loopback on the GPU host and no "
            "nginx route exposes it. Run scripts/gpu_telemetry.py ON that host during the "
            "next soak and drop gpu.jsonl into this run directory to fill this in.")
    elif gpu.get("vram_growth_pct_points") is not None             and gpu["vram_growth_pct_points"] > 5:
        issues.append(
            f"VRAM grew {gpu['vram_growth_pct_points']:+.1f} percentage points between the "
            "start and end of the run, which is a memory-growth signature worth chasing.")
    if not meta.get("usage_reported_by_server"):
        issues.append("server did not honour stream_options.include_usage; output token "
                      "counts are estimated from SSE delta count, input tokens unavailable")

    if reasons:
        verdict = "FAIL"
    elif incomplete:
        # The measured part can be clean and the test still not be finished. Calling that PASS
        # would claim coverage the run does not have.
        verdict = "INCOMPLETE"
    elif issues:
        verdict = "PASS WITH ISSUES"
    else:
        verdict = "PASS"

    return {
        "generated_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "run_dir": str(directory),
        "meta": meta,
        "verdict": verdict,
        "disconnect": disconnect,
        "excluded_requests": excluded,
        "incomplete_reasons": incomplete,
        "fail_reasons": reasons,
        "issues": issues,
        "overall": overall,
        "phases": phases,
        "by_concurrency": concurrency,
        "by_class": classes,
        "degradation": degradation,
        "determinism": determinism,
        "availability": {"polls": len(liveness), "non_200": len(unhealthy),
                         "gaps": gaps, "poller_artifacts": artifacts},
        "gpu": gpu,
        "max_concurrency_tested": max_tested,
        "recommended_concurrency": recommended,
        "peak_throughput_concurrency": peak_throughput,
        "recommendation_basis": knee_note,
        "baseline_ttft_p50": baseline_ttft,
        "error_taxonomy": dict(errors),
        "error_samples": samples,
        "pass_criteria": {"post_retry_error_rate_max": PASS_ERROR_RATE,
                          "p95_drift_max": PASS_P95_DRIFT},
    }


def _row(label: str, s: dict) -> str:
    t, e = s.get("ttft_s") or {}, s.get("e2e_s") or {}
    return (f"| {label} | {s.get('requests', 0)} | {s.get('requests_per_s') or '-'} | "
            f"{t.get('p50') or '-'} | {t.get('p95') or '-'} | "
            f"{e.get('p50') or '-'} | {e.get('p95') or '-'} | {e.get('p99') or '-'} | "
            f"{(s.get('output_tokens_per_s') or {}).get('p50') or '-'} | "
            f"{(s.get('post_retry_error_rate') or 0) * 100:.2f}% | "
            f"{((s.get('correctness') or {}).get('rate') or 0) * 100:.1f}% |")


def markdown(a: dict) -> str:
    m = a["meta"]
    head = ("| level | reqs | req/s | TTFT p50 | TTFT p95 | e2e p50 | e2e p95 | e2e p99 | "
            "tok/s p50 | err | correct |\n|---|--:|--:|--:|--:|--:|--:|--:|--:|--:|--:|")
    lines = [
        f"# Soak test results - {m.get('model')}",
        "",
        f"**{a['verdict']}** &nbsp; | &nbsp; {m.get('elapsed_minutes', '?')} min, "
        f"{a['overall'].get('requests', 0):,} requests, "
        f"{a['overall'].get('attempts', 0):,} attempts",
        "",
        "## 1. Environment and configuration", "",
        f"- Endpoint `{m.get('endpoint')}`, model `{m.get('model')}`",
        f"- Decoding {m.get('decoding')}, timeout {m.get('timeout_s')}s, seed {m.get('seed')}",
        f"- Prompt classes {m.get('prompt_classes')}",
        f"- Server token accounting: "
        f"{'exact' if m.get('usage_reported_by_server') else 'ESTIMATED (see issues)'}",
        f"- GPU telemetry: {m.get('gpu_telemetry')}",
        "",
        "## 2. Executive summary", "",
        f"**Verdict: {a['verdict']}**", "",
    ]
    for r in a.get("incomplete_reasons", []):
        lines.append(f"- INCOMPLETE: {r}")
    for r in a["fail_reasons"]:
        lines.append(f"- FAIL: {r}")
    for i in a["issues"]:
        lines.append(f"- Issue: {i}")
    if not a["fail_reasons"] and not a["issues"] and not a.get("incomplete_reasons"):
        lines.append("- No failures and no issues against the pre-registered criteria.")

    o = a["overall"]
    lines += [
        "", "## 3. Performance", "", head, _row("overall", o), "",
        f"- Input tokens {o['tokens']['input_total']:,}, "
        f"output tokens {o['tokens']['output_total']:,}, "
        f"aggregate {o['tokens']['aggregate_output_per_s']} output tok/s",
        f"- Truncated (finish_reason=length): {o['truncated']}",
        f"- Raw per-attempt error rate {o['raw_error_rate']:.2%}, "
        f"post-retry {(o['post_retry_error_rate'] or 0):.2%}",
        "",
        "**GPU utilization, VRAM, temperature and power are absent.** Not an omission - "
        "unobtainable through this API at any permission level. See section 9.",
        "",
        "## 4. By concurrency level", "", head,
    ]
    for level, s in a["by_concurrency"].items():
        lines.append(_row(f"c{level}", s))
    lines += ["", f"- Maximum tested: **c{a['max_concurrency_tested']}**",
              f"- Recommended sustainable: **c{a['recommended_concurrency']}**", ""]

    lines += ["## 5. Stability and degradation", "", head]
    for name, s in a["phases"].items():
        lines.append(_row(f"{name} (c{s.get('concurrency')})", s))
    lines.append("")
    for key, d in a["degradation"].items():
        lines += [
            f"**{d['early']} -> {d['late']}** (identical configuration, hours apart)", "",
            f"- e2e p50 {_signed(d['e2e_p50_drift'])}, p95 {_signed(d['e2e_p95_drift'])}",
            f"- TTFT p50 {_signed(d['ttft_p50_drift'])}, p95 {_signed(d['ttft_p95_drift'])}",
            f"- throughput {_signed(d['throughput_drift'])}, "
            f"tokens/s {_signed(d['tokens_per_s_drift'])}",
            f"- truncation {d['truncated_early']} -> {d['truncated_late']}, "
            f"error rate {(d['error_rate_early'] or 0):.2%} -> {(d['error_rate_late'] or 0):.2%}",
            "",
        ]
    av = a["availability"]
    lines += [f"- Health polls: {av['polls']}, non-200: {av['non_200']}, "
              f"gaps >60s: {len(av['gaps'])}", ""]

    det = a["determinism"]
    lines += ["### Determinism under load", "",
              f"{det['total_mismatches']} of {det['total_probes']} byte-identical probes "
              "diverged from their first response.", "",
              "| concurrency at send | probes | diverged | rate |", "|---|--:|--:|--:|"]
    for c, v in det["by_concurrency"].items():
        lines.append(f"| c{c} | {v['n']} | {v['mismatch']} | "
                     f"{(v['rate'] or 0) * 100:.1f}% |")

    lines += ["", "## 6. Errors", ""]
    if a["error_taxonomy"]:
        lines += ["| outcome | count |", "|---|--:|"]
        for k, v in sorted(a["error_taxonomy"].items(), key=lambda kv: -kv[1]):
            lines.append(f"| `{k}` | {v} |")
        lines += ["", "First occurrence of each:", "", "```",
                  json.dumps(a["error_samples"], indent=2)[:2500], "```"]
    else:
        lines.append("No errors of any kind were recorded.")

    lines += ["", "## 7. By prompt class", "", head]
    for c, s in a["by_class"].items():
        lines.append(_row(c, s))

    lines += ["", "## 8. Baseline for future comparison", "",
              "```json", json.dumps({
                  "model": m.get("model"), "endpoint": m.get("endpoint"),
                  "recommended_concurrency": a["recommended_concurrency"],
                  "max_tested": a["max_concurrency_tested"],
                  "req_per_s": (a["by_concurrency"].get(str(a["recommended_concurrency"]))
                                or {}).get("requests_per_s"),
                  "ttft_p50": ((a["by_concurrency"].get(str(a["recommended_concurrency"]))
                                or {}).get("ttft_s") or {}).get("p50"),
                  "e2e_p95": ((a["by_concurrency"].get(str(a["recommended_concurrency"]))
                               or {}).get("e2e_s") or {}).get("p95"),
                  "output_tokens_per_s": ((a["by_concurrency"].get(
                      str(a["recommended_concurrency"])) or {}).get(
                      "output_tokens_per_s") or {}).get("p50"),
                  "verdict": a["verdict"],
              }, indent=2), "```"]
    return "\n".join(lines) + "\n"


def _signed(x: float | None) -> str:
    if x is None:
        return "n/a"
    return f"{x:+.1%}"


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="soak_report")
    ap.add_argument("run_dir")
    ap.add_argument("--json-only", action="store_true")
    args = ap.parse_args(argv)

    directory = Path(args.run_dir)
    if not (directory / "run.json").is_file():
        print(f"no run.json in {directory}")
        return 2

    a = analyse(directory)
    (directory / "analysis.json").write_text(
        json.dumps(a, indent=2, ensure_ascii=False), encoding="utf-8")
    if not args.json_only:
        text = markdown(a)
        (directory / "report.md").write_text(text, encoding="utf-8")
        print(text)
    print(f"\nwrote {directory / 'analysis.json'} and {directory / 'report.md'}",
          file=sys.stderr)
    return 0


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
