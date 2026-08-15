"""Generate the model-comparison report from recorded runs. No figure is ever typed by hand.

    python scripts/model_comparison_report.py

Reads the four `retention_v3` run directories and the harness's own comparison reports,
recomputes every token and latency figure from the raw per-call logs, and emits:

    docs/reports/model-comparison-metrics.json   every number, machine-readable
    docs/reports/model-comparison.html           the standalone page
    docs/reports/model-comparison-fragment.html  the same page as an Artifact fragment

The JSON and the HTML come from one computation, so they cannot disagree, and re-running this
after a fifth model is evaluated regenerates a correct report with no editing.

**Four refusals.** This exits non-zero rather than emitting a report that looks fine and is
wrong. Each exists because the corresponding mistake is easy to make and invisible afterwards:

  1. The runs must share testset/workload/scorer/prompt/repeats/items. The comparability claim
     the page makes becomes a thing the code checks, not a thing someone remembered.
  2. Gemini's F1 must be byte-identical across the three comparison reports it appears in. It is
     the same 414 recorded outputs scored three times; if the three disagree, the scoring path
     is not deterministic and nothing downstream is trustworthy.
  3. No `ok` row may carry a null `prompt_tokens` or `completion_tokens`. `sum()` over a list
     containing None either raises or silently drops -- and a token total that quietly excluded
     rows would read as a real measurement.
  4. Every run must have the full 414 rows.

**Percentiles are nearest-rank: index = ceil(q * n), 1-based.** Written out because an earlier
helper in this repo used `round(q*n + 0.5)`, which is off by one whenever `q*n` is an exact
integer -- exactly the p50-with-even-n case. That produced a Gemini p50 of 2.062 s where the
correct value is 2.047 s. `scripts/soak_report.py` still carries the old form.
"""

from __future__ import annotations

import json
import math
import re
import statistics
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
RUNS_DIR = REPO / "out" / "runs"
REPORTS = REPO / "out" / "reports"
DOCS = REPO / "docs" / "reports"

# (key, display name, short label, run directory, runs on our GPU?)
MODELS = [
    ("gemini", "Gemini 2.5 Flash", "Gemini", "20260814-132425Z-e17-gemini", False),
    ("qwen38", "Qwen3.8 27B", "Qwen3.8", "20260815-124600Z-e18-tf-qwen38", True),
    ("qwen36", "Qwen3.6 27B", "Qwen3.6", "20260814-134803Z-e17-tf-qwen", True),
    ("gemma", "Gemma 4 12B", "Gemma 4", "20260814-132642Z-e17-tf-gemma", True),
]
ARM_OF = {"gemini": "e17-gemini", "qwen38": "e18-tf-qwen38",
          "qwen36": "e17-tf-qwen", "gemma": "e17-tf-gemma"}

# candidate key -> comparison report against Gemini
VS_GEMINI = {
    "qwen38": "compare-e18-qwen38-vs-gemini.txt",
    "qwen36": "compare-e17-qwen.txt",
    "gemma": "compare-e17-gemma.txt",
}
DIMS = ["call_result", "reason", "product"]
DIM_LABEL = {"call_result": "Call outcome", "reason": "Reason", "product": "Product"}

# Verbatim from the run manifests; the report quotes these rather than restating them.
SHARED_FIELDS = ("testset_sha", "workload_sha", "scoring_code_sha", "prompt_sha",
                 "repeats", "items", "rows")

F1_ROW = re.compile(
    r"^  (call_result|reason|product)\s+(\S+)\s+(\d+)\s+(\d+)\s+(\d+)\s+"
    r"([\d.]+)\s+([\d.]+)\s+([\d.]+)\s*$", re.M)
# `net` is printed with an explicit sign when positive (e.g. "+1"), so the sign class has to
# admit '+' as well as '-'. Allowing only '-' silently matched nothing for the arms that came
# out ahead, which surfaced as a KeyError rather than a wrong number -- the loud failure was
# luck, so the parse result is asserted non-empty below regardless.
PAIRED_ROW = re.compile(
    r"^  (call_result|reason|product)\s+(\d+)\s+(\d+)\s+(\d+)\s+(\d+)\s+(\d+)\s+"
    r"([+-]?\d+)\s+(\S+)\s+(AHEAD|BEHIND|INDISTINGUISHABLE|UNDERPOWERED)\s*$", re.M)
NFLIP = re.compile(r"^  (\S+)\s+N_flip = (\d+)", re.M)
INSTAB = re.compile(
    r"^  (\S+)\s+observable\s+(\d+)\s+raw-unstable\s+(\d+)\s+scored-unstable\s+(\d+)", re.M)


class Refused(SystemExit):
    def __init__(self, why: str) -> None:
        super().__init__(f"REFUSING to write a report: {why}")


def pct(values: list[float], q: float) -> float:
    """Nearest rank: the ceil(q*n)-th smallest value, 1-based. See the module docstring."""
    ordered = sorted(values)
    idx = min(len(ordered), max(1, math.ceil(q * len(ordered))))
    return ordered[idx - 1]


def read_run(key: str, directory: str) -> dict:
    path = RUNS_DIR / directory
    meta = json.loads((path / "run.json").read_text(encoding="utf-8"))
    rows = [json.loads(l) for l in (path / "run.jsonl").read_text(encoding="utf-8").splitlines()
            if l.strip()]
    if len(rows) != 414:
        raise Refused(f"{directory} has {len(rows)} rows, expected 414")

    ok = [r for r in rows if r["outcome"] == "ok"]
    missing_in = sum(1 for r in ok if r.get("prompt_tokens") is None)
    missing_out = sum(1 for r in ok if r.get("completion_tokens") is None)
    if missing_in or missing_out:
        raise Refused(
            f"{directory} has {missing_in} ok rows with no prompt_tokens and {missing_out} with "
            "no completion_tokens; a token total computed over these would silently omit them")

    in_tok = [r["prompt_tokens"] for r in ok]
    out_tok = [r["completion_tokens"] for r in ok]
    lat = [r["latency_s"] for r in ok if r.get("latency_s") is not None]

    return {
        "run_id": meta["run_id"],
        "model_requested": meta["model_requested"],
        "observed_models": meta["observed_models"],
        "backend": meta["backend"],
        "concurrency": meta["concurrency"],
        "rows": len(rows),
        "ok": len(ok),
        "input_tokens_total": sum(in_tok),
        "input_tokens_per_call": round(statistics.mean(in_tok), 1),
        "output_tokens_total": sum(out_tok),
        "output_tokens_per_call": round(statistics.mean(out_tok), 1),
        # 3dp: latency_s sits on a 1 ms grid (host clock resolution), so more is invented.
        "latency_s": {"p50": round(pct(lat, .50), 3), "p90": round(pct(lat, .90), 3),
                      "p95": round(pct(lat, .95), 3), "p99": round(pct(lat, .99), 3),
                      "min": round(min(lat), 3), "max": round(max(lat), 3),
                      "mean": round(statistics.mean(lat), 3)},
        "attempts_total": sum(r.get("attempt_count") or 0 for r in rows),
        "rows_retried": sum(1 for r in rows if (r.get("attempt_count") or 0) > 1),
        "truncated": sum(1 for r in rows if r.get("finish_reason") == "length"),
        "wall_time_s": round(meta["wall_time_s"], 1),
        "throughput_calls_per_s": round(meta["throughput_calls_per_s"], 3),
        # None, not 0: the self-hosted endpoint reports no cost at all. Rendering that as
        # "$0.00" beside Gemini's real charge would be a false comparison.
        "cost_usd": (meta["total_cost_usd_lower_bound"]
                     if meta["backend"] == "openrouter" else None),
        "reasoning_effort": meta["decoding"].get("reasoning_effort"),
        "shared": {f: meta.get(f) for f in SHARED_FIELDS},
    }


def parse_compare(path: Path) -> dict:
    text = path.read_text(encoding="utf-8")
    f1: dict[str, dict[str, float]] = {}
    for dim, arm, denom, joined, pf, prec, rec, w in F1_ROW.findall(text):
        f1.setdefault(arm, {})[dim] = {"denominator": int(denom), "joined": int(joined),
                                       "parse_failures": int(pf), "precision": float(prec),
                                       "recall": float(rec), "f1": float(w)}
    paired = {}
    for dim, br, bw, io, co, d, net, band, verdict in PAIRED_ROW.findall(text):
        paired[dim] = {"both_right": int(br), "both_wrong": int(bw), "incumbent_only": int(io),
                       "candidate_only": int(co), "discordant": int(d), "net": int(net),
                       "band": band, "verdict": verdict}
    nflip = {a: int(n) for a, n in NFLIP.findall(text)}
    instab = {a: {"observable": int(o), "raw_unstable": int(r), "scored_unstable": int(s)}
              for a, o, r, s in INSTAB.findall(text)}
    return {"f1": f1, "paired": paired, "n_flip": nflip, "instability": instab}


def collect() -> dict:
    models = {k: read_run(k, d) for k, _, _, d, _ in MODELS}

    # Refusal 1: the comparability claim, enforced.
    for field in SHARED_FIELDS:
        seen = {k: m["shared"][field] for k, m in models.items()}
        if len(set(map(str, seen.values()))) != 1:
            raise Refused(f"runs disagree on {field}: {seen}")

    compares = {k: parse_compare(REPORTS / f) for k, f in VS_GEMINI.items()}
    missing = [k for k, c in compares.items() if not c["f1"]]
    if missing:
        raise Refused(f"could not parse an F1 table from the reports for {missing}")
    # A regex that silently matches nothing is the failure mode here, so assert the shape
    # rather than trusting that a parse "worked".
    for key, comp in compares.items():
        absent = [d for d in DIMS if d not in comp["paired"]]
        if absent:
            raise Refused(f"no paired verdict parsed for {key} on {absent} "
                          f"(got {sorted(comp['paired'])})")
        if not comp["n_flip"] or not comp["instability"]:
            raise Refused(f"no N_flip or instability parsed for {key}")

    # Refusal 2: Gemini is the same recorded outputs scored three times. If the three
    # disagree, the scoring path is not deterministic.
    gem_arm = ARM_OF["gemini"]
    gem_f1 = {k: {d: c["f1"][gem_arm][d]["f1"] for d in DIMS} for k, c in compares.items()}
    if len({json.dumps(v, sort_keys=True) for v in gem_f1.values()}) != 1:
        raise Refused(f"Gemini's F1 differs between comparison reports: {gem_f1}")

    for key, comp in compares.items():
        arm = ARM_OF[key]
        models[key]["f1"] = {d: comp["f1"][arm][d]["f1"] for d in DIMS}
        models[key]["scoring_detail"] = comp["f1"][arm]
        models[key]["paired_vs_gemini"] = comp["paired"]
        models[key]["n_flip"] = comp["n_flip"].get(arm)
        models[key]["instability"] = comp["instability"].get(arm)
    any_comp = compares["qwen38"]
    models["gemini"]["f1"] = {d: any_comp["f1"][gem_arm][d]["f1"] for d in DIMS}
    models["gemini"]["scoring_detail"] = any_comp["f1"][gem_arm]
    models["gemini"]["paired_vs_gemini"] = None
    models["gemini"]["n_flip"] = any_comp["n_flip"].get(gem_arm)
    models["gemini"]["instability"] = any_comp["instability"].get(gem_arm)

    head = parse_compare(REPORTS / "compare-qwen36-vs-qwen38.txt")
    return {
        "generated_from": "raw run.jsonl + harness comparison reports; no hand-entered figures",
        "percentile_method": "nearest rank, index = ceil(q * n), 1-based",
        "shared_contract": models["gemini"]["shared"],
        "models": {k: models[k] for k, *_ in MODELS},
        "qwen36_vs_qwen38": head["paired"],
        "order": [k for k, *_ in MODELS],
        "labels": {k: {"name": n, "short": s, "ours": o} for k, n, s, _, o in MODELS},
    }


def main() -> int:
    data = collect()
    DOCS.mkdir(exist_ok=True)
    (DOCS / "model-comparison-metrics.json").write_text(
        json.dumps(data, indent=2), encoding="utf-8")

    from model_comparison_html import render  # noqa: PLC0415 - split for readability
    fragment = render(data)
    (DOCS / "model-comparison-fragment.html").write_text(fragment, encoding="utf-8")
    cut = fragment.index("</style>") + len("</style>")
    standalone = ('<!DOCTYPE html>\n<html lang="en">\n<head>\n<meta charset="utf-8">\n'
                  '<meta name="viewport" content="width=device-width, initial-scale=1">\n'
                  + fragment[:cut] + "\n</head>\n<body>\n" + fragment[cut:]
                  + "\n</body>\n</html>\n")
    (DOCS / "model-comparison.html").write_text(standalone, encoding="utf-8")

    print("all four refusals passed")
    print(f"  shared contract: {data['shared_contract']['scoring_code_sha'][:16]} / "
          f"{data['shared_contract']['workload_sha'][:16]}")
    for key in data["order"]:
        m = data["models"][key]
        print(f"  {data['labels'][key]['name']:18} F1 {m['f1']}  "
              f"in/call {m['input_tokens_per_call']:>7}  out/call {m['output_tokens_per_call']:>6}"
              f"  p50 {m['latency_s']['p50']:>7}s")
    print(f"\nwrote docs/reports/model-comparison-metrics.json, .html, -fragment.html "
          f"(ascii-only: {standalone.isascii()})")
    return 0


if __name__ == "__main__":  # pragma: no cover
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    sys.exit(main())
