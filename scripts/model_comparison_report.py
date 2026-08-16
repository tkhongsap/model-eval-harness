"""Generate the model-comparison report from recorded runs. No figure is ever typed by hand.

    python scripts/model_comparison_report.py
    python scripts/model_comparison_report.py --config configs/comparison/retention-challenge-v1.json

**Which comparison is a config, not a code change.** `configs/comparison/<pack>.json` names
the pack, the arms, their run directories, the comparison reports, the expected shape and the
output paths. Adding a pack or a model is a new config file; nothing here is edited. The
`retention-v3.json` config reproduces the values this script used to hardcode, which is the
regression test that the parameterisation changed no figure.

It reads those run directories and the harness's own comparison reports, recomputes every
token and latency figure from the raw per-call logs, and emits `<stem>-metrics.json`,
`<stem>.html` (standalone) and `<stem>-fragment.html` (for publishing) into the configured
directory. The JSON and the HTML come from one computation, so they cannot disagree.

**Every claim in the prose is derived, never asserted.** The bottom line, the "no retries"
line, the tokenizer note and the incumbent's stability note are all computed from the data in
front of them, because each is a measurement -- a page that prints "no retries" unconditionally
is wrong the first time a gateway hiccups, and one that names a winner unconditionally is
fabrication on the next pack.

**Refusals.** This exits non-zero rather than emitting a report that looks fine and is wrong.
Each exists because the corresponding mistake is easy to make and invisible afterwards:

  1. The runs must share testset/workload/scorer/prompt/repeats/items. The comparability claim
     the page makes becomes a thing the code checks, not a thing someone remembered.
  2. The incumbent's F1 must be byte-identical across every comparison report it appears in. It
     is the same recorded outputs scored once per comparison; if they disagree, the scoring path
     is not deterministic and nothing downstream is trustworthy.
  3. No `ok` row may carry a null `prompt_tokens` or `completion_tokens`. `sum()` over a list
     containing None either raises or silently drops -- and a token total that quietly excluded
     rows would read as a real measurement.
  4. Every run must have exactly `expected_rows` rows.
  5. The config's incumbent must exist, keys must be unique, and every candidate must have a
     comparison report -- otherwise it would render as blank cells rather than as an error.

**Percentiles are nearest-rank: index = ceil(q * n), 1-based.** Written out because an earlier
helper in this repo used `round(q*n + 0.5)`, which is off by one whenever `q*n` is an exact
integer -- exactly the p50-with-even-n case. That produced a Gemini p50 of 2.062 s where the
correct value is 2.047 s. `scripts/soak_report.py` still carries the old form.
"""

from __future__ import annotations

import argparse
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

# The run list, the arm names and the expected shape all used to be module constants pinned
# to `retention_v3`. They are a property of WHICH COMPARISON is being rendered, not of the
# renderer, so they now live in `configs/comparison/<pack>.json` and the script takes
# `--config`. The v3 config reproduces the previously hardcoded values exactly, which is the
# regression test: regenerating it must yield a byte-identical report.
CONFIG_DIR = REPO / "configs" / "comparison"
DEFAULT_CONFIG = CONFIG_DIR / "retention-v3.json"

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


def load_config(path: Path) -> dict:
    cfg = json.loads(path.read_text(encoding="utf-8"))
    keys = [m["key"] for m in cfg["models"]]
    if cfg["incumbent"] not in keys:
        raise Refused(f"incumbent {cfg['incumbent']!r} is not among the models {keys}")
    if len(set(keys)) != len(keys):
        raise Refused(f"duplicate model keys in {path.name}: {keys}")
    # Every non-incumbent needs a comparison report; without one it has no F1, no paired
    # verdict and no stability, and would render as blank cells rather than as an error.
    missing = [k for k in keys if k != cfg["incumbent"] and k not in cfg["vs_incumbent"]]
    if missing:
        raise Refused(f"no vs-incumbent report configured for {missing}")
    return cfg


def read_run(key: str, directory: str, expected_rows: int) -> dict:
    path = RUNS_DIR / directory
    meta = json.loads((path / "run.json").read_text(encoding="utf-8"))
    rows = [json.loads(l) for l in (path / "run.jsonl").read_text(encoding="utf-8").splitlines()
            if l.strip()]
    if len(rows) != expected_rows:
        raise Refused(f"{directory} has {len(rows)} rows, expected {expected_rows}")

    ok = [r for r in rows if r["outcome"] == "ok"]

    # REFUSAL: an arm that did not actually answer must never reach a report. `flatten.to_rows`
    # emits a ground-truth-shaped skeleton for a payload it could not read, which keeps the
    # denominators equal -- and has the consequence that an arm answering NOTHING still scores
    # a perfect 1.000 on product, because the skeleton carries the ground truth's own product
    # names. `20260815-223809Z-e20-chal-gemma` is the live example: 150/150 transport errors,
    # zero correct clusters on every dimension, and w-F1 1.000 on product in its compare report.
    # Without this gate, re-adding a dead arm to a config publishes a table in which a model
    # that never responded outscores every model that did.
    failed = len(rows) - len(ok)
    if failed:
        counts: dict[str, int] = {}
        for r in rows:
            counts[r["outcome"]] = counts.get(r["outcome"], 0) + 1
        raise Refused(
            f"{directory} has {failed} of {len(rows)} rows that are not 'ok' ({counts}). An arm "
            "that did not answer still scores 1.000 on product, because to_rows emits a "
            "ground-truth-shaped skeleton for an unreadable payload. Fix the run, or leave the "
            "arm out of the config.")

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


def collect(cfg: dict) -> dict:
    spec = {m["key"]: m for m in cfg["models"]}
    order = [m["key"] for m in cfg["models"]]
    arm_of = {m["key"]: m["arm"] for m in cfg["models"]}
    inc = cfg["incumbent"]
    models = {k: read_run(k, spec[k]["run"], cfg["expected_rows"]) for k in order}

    # Refusal 1: the comparability claim, enforced.
    for field in SHARED_FIELDS:
        seen = {k: m["shared"][field] for k, m in models.items()}
        if len(set(map(str, seen.values()))) != 1:
            raise Refused(f"runs disagree on {field}: {seen}")

    compares = {k: parse_compare(REPORTS / f) for k, f in cfg["vs_incumbent"].items()}
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

    # Refusal 2: the incumbent is the same recorded outputs scored once per comparison. If
    # those disagree, the scoring path is not deterministic.
    gem_arm = arm_of[inc]
    gem_f1 = {k: {d: c["f1"][gem_arm][d]["f1"] for d in DIMS} for k, c in compares.items()}
    if len({json.dumps(v, sort_keys=True) for v in gem_f1.values()}) != 1:
        raise Refused(f"the incumbent's F1 differs between comparison reports: {gem_f1}")

    for key, comp in compares.items():
        arm = arm_of[key]
        models[key]["f1"] = {d: comp["f1"][arm][d]["f1"] for d in DIMS}
        models[key]["scoring_detail"] = comp["f1"][arm]
        models[key]["paired_vs_gemini"] = comp["paired"]
        models[key]["n_flip"] = comp["n_flip"].get(arm)
        models[key]["instability"] = comp["instability"].get(arm)
    # Refusal 2 proved every comparison agrees on the incumbent, so any one of them carries
    # its figures.
    any_comp = compares[next(k for k in order if k != inc)]
    models[inc]["f1"] = {d: any_comp["f1"][gem_arm][d]["f1"] for d in DIMS}
    models[inc]["scoring_detail"] = any_comp["f1"][gem_arm]
    models[inc]["paired_vs_gemini"] = None
    models[inc]["n_flip"] = any_comp["n_flip"].get(gem_arm)
    models[inc]["instability"] = any_comp["instability"].get(gem_arm)

    out = {
        "generated_from": "raw run.jsonl + harness comparison reports; no hand-entered figures",
        "percentile_method": "nearest rank, index = ceil(q * n), 1-based",
        "shared_contract": models[inc]["shared"],
        "models": {k: models[k] for k in order},
    }
    h2h = cfg.get("head_to_head")
    if h2h:
        out[h2h["key"]] = parse_compare(REPORTS / h2h["report"])["paired"]
    out["order"] = order
    out["labels"] = {m["key"]: {"name": m["name"], "short": m["short"], "ours": m["ours"]}
                     for m in cfg["models"]}
    out["pack"] = cfg["pack"]
    out["metrics_stem"] = cfg["output"]["stem"]
    out["incumbent"] = inc

    # The "what the evaluation set is" table used to be hardcoded HTML listing retention_v3's
    # nine families. Rendered over another pack it stated another pack's composition as fact --
    # caught on the challenge report, where it claimed 30 Thai-linguistic and 12 long-context
    # items that pack does not contain. COUNTS are recomputed from the testset every time so
    # they cannot drift; only the human-readable descriptions come from the config.
    labels = cfg.get("family_labels", {})
    counts: dict[str, int] = {}
    if cfg.get("testset"):
        for line in (REPO / cfg["testset"]).read_text(encoding="utf-8").splitlines():
            if line.strip():
                fam = json.loads(line).get("family", "?")
                counts[fam] = counts.get(fam, 0) + 1
        total = sum(counts.values())
        if total != out["shared_contract"]["items"]:
            raise Refused(
                f"{cfg['testset']} has {total} items but the runs recorded "
                f"{out['shared_contract']['items']}; the report would describe a different pack "
                "from the one that was scored")
    out["dataset"] = {
        "families": [
            {"name": labels.get(f, [f, ""])[0], "count": n, "what": labels.get(f, [f, ""])[1]}
            for f, n in sorted(counts.items(), key=lambda kv: (-kv[1], kv[0]))
        ],
    }
    out["head_to_head_key"] = h2h["key"] if h2h else None
    out["copy"] = {"title": cfg.get("title"), "doc_title": cfg.get("doc_title"),
                   "kicker_subject": cfg.get("kicker_subject"), "note": cfg.get("note")}
    return out


def main() -> int:
    ap = argparse.ArgumentParser(prog="model_comparison_report")
    ap.add_argument("--config", default=str(DEFAULT_CONFIG),
                    help="comparison set under configs/comparison/ (default: retention-v3)")
    args = ap.parse_args()

    cfg = load_config(Path(args.config))
    data = collect(cfg)

    out_dir = REPO / cfg["output"]["dir"]
    stem = cfg["output"]["stem"]
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / f"{stem}-metrics.json").write_text(
        json.dumps(data, indent=2), encoding="utf-8")

    from model_comparison_html import render  # noqa: PLC0415 - split for readability
    fragment = render(data)
    (out_dir / f"{stem}-fragment.html").write_text(fragment, encoding="utf-8")
    cut = fragment.index("</style>") + len("</style>")
    standalone = ('<!DOCTYPE html>\n<html lang="en">\n<head>\n<meta charset="utf-8">\n'
                  '<meta name="viewport" content="width=device-width, initial-scale=1">\n'
                  + fragment[:cut] + "\n</head>\n<body>\n" + fragment[cut:]
                  + "\n</body>\n</html>\n")
    (out_dir / f"{stem}.html").write_text(standalone, encoding="utf-8")

    print(f"config: {Path(args.config).name}  pack: {cfg['pack']}")
    print("all refusals passed")
    print(f"  shared contract: {data['shared_contract']['scoring_code_sha'][:16]} / "
          f"{data['shared_contract']['workload_sha'][:16]}")
    for key in data["order"]:
        m = data["models"][key]
        print(f"  {data['labels'][key]['name']:18} F1 {m['f1']}  "
              f"in/call {m['input_tokens_per_call']:>7}  out/call {m['output_tokens_per_call']:>6}"
              f"  p50 {m['latency_s']['p50']:>7}s")
    rel = out_dir.relative_to(REPO).as_posix()
    print(f"\nwrote {rel}/{stem}-metrics.json, .html, -fragment.html "
          f"(ascii-only: {standalone.isascii()})")
    return 0


if __name__ == "__main__":  # pragma: no cover
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    sys.exit(main())
