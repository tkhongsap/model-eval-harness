"""Pool the two hand-labelled packs, and grade every metric against production's own bands.

    PYTHONPATH=src python scripts/pooled_bands.py

WHY THIS EXISTS. Two questions were being answered badly.

  1. "Is the candidate different from the incumbent?" Each pack was tested on its own, and
     each returned UNDERPOWERED on two of three dimensions -- not because the models are
     close, but because 138 items produce too few DISCORDANT items to test at all. The packs
     are disjoint samples measured by the same instrument, so their discordant counts pool.
     Pooling is the only way to buy power here that does not involve generating new data.

  2. "Is it good enough?" Every published number so far was a weighted F1. Precision and
     recall were computed and thrown away at the report boundary, and F1 hides the fact that
     every model on this task has excellent recall and mediocre precision.

IT RECOMPUTES NO MODEL OUTPUT. Both inputs are published metrics files, themselves derived
from the raw per-call logs. This reads them, checks they may legitimately be combined, and
applies two things neither file applies to itself.

THE BANDS ARE PRODUCTION'S, READ FROM PRODUCTION'S CONFIG AT RUNTIME. They are not retyped
here. If that file moves or changes shape this refuses to run rather than fall back to a
remembered copy -- a threshold nobody can trace to its source is worse than no threshold.

FOUR REFUSALS, because pooling is exactly where a silent mistake becomes a wrong decision:

  1. IT REFUSES TO POOL PACKS THAT WERE NOT MEASURED THE SAME WAY. Same prompt, same scoring
     code, same repeat count, or it stops. Pooling across a prompt change would average two
     different experiments.

  2. IT REFUSES TO POOL A PACK WITH ITSELF. If the two testset hashes match, the "second"
     pack is the same items again, and adding the counts would double-count every item and
     halve the apparent noise. This is the failure that would be hardest to spot downstream.

  3. IT REFUSES TO POOL A MODEL THAT IS NOT IN BOTH PACKS. Two models were only ever run on
     pack A. Reporting their pack-A figure in a column headed "pooled" would be a lie of
     formatting, so they are listed separately with the reason.

  4. IT NEVER TURNS A BAND INTO A WINNER. A band is production's published threshold applied
     to a point estimate. It is not a significance test, and two models sitting in different
     bands can be statistically indistinguishable -- which is exactly what happens here. The
     paired verdict is printed beside the bands so the two can never be read apart.
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "src"))

from evalharness.compare import Disagreement, paired_verdict  # noqa: E402

DOCS = REPO / "docs" / "reports"
PACK_A = DOCS / "model-comparison-metrics.json"
PACK_B = DOCS / "model-comparison-challenge-metrics.json"
PROD_CONFIG = (REPO / "production-reference" / "sentiment-voice-analysis-develop"
               / "config" / "sentiment_qa" / "qa_pipeline_fact_check.yml")

DIMS = ("call_result", "reason", "product")
METRICS = ("precision", "recall", "f1")
# Graded separately: production bands accuracy on its own scale (80/85/90).
ACCURACY_METRIC = "accuracy"
INCUMBENT = "gemini"
# The contract fields that must agree before two packs may be combined. Deliberately does
# NOT include testset_sha or items: those are REQUIRED to differ (refusal 2).
POOLABLE = ("prompt_sha", "scoring_code_sha", "repeats")

DISPLAY = {
    "gemini": "Gemini 2.5 Flash",
    "qwen38": "Qwen3.8 27B",
    "qwen36": "Qwen3.6 27B",
    "gemma": "Gemma 4 12B",
}


class Refused(Exception):
    """A precondition for combining or grading these numbers does not hold."""


def _show(path: Path) -> str:
    """Repo-relative when it can be, absolute when it cannot.

    A refusal message must never be the thing that raises: `relative_to` throws on any
    path outside the repository, which is exactly the case a "config not found" message
    is being built for.
    """
    try:
        return str(path.relative_to(REPO)).replace("\\", "/")
    except ValueError:
        return str(path)


def read_bands(path: Path) -> dict[str, tuple[float, float, float]]:
    """Parse production's metric_thresholds block. Strict: any surprise is a refusal.

    A targeted parser rather than a YAML dependency: requirements.txt pins what the
    production scorer computes, so it is not a file to add a library to for one table.
    """
    if not path.exists():
        raise Refused(
            f"production config not found at {_show(path)}. The bands are read "
            "from production's own file at runtime and are deliberately not duplicated "
            "here, so there is nothing to fall back to."
        )
    text = path.read_text(encoding="utf-8")
    block = re.search(r"^  metric_thresholds:\n((?:^    .*\n|^[ \t]*\n)+)", text, re.M)
    if not block:
        raise Refused(
            f"no 'metric_thresholds:' block in {_show(path)}. Production's "
            "threshold vocabulary has moved; find it rather than assuming the old values."
        )
    bands: dict[str, tuple[float, float, float]] = {}
    for metric, body in re.findall(
        r"^    (\w+):[ \t]*\n((?:^      \w+:[ \t]*\d+[ \t]*\n)+)", block.group(1), re.M
    ):
        levels = {
            name: float(value)
            for name, value in re.findall(r"^      (\w+):[ \t]*(\d+)[ \t]*$", body, re.M)
        }
        missing = {"acceptable", "good", "excellent"} - set(levels)
        if missing:
            raise Refused(f"metric_thresholds.{metric}: missing {sorted(missing)}")
        triple = (levels["acceptable"], levels["good"], levels["excellent"])
        if not triple[0] <= triple[1] <= triple[2]:
            raise Refused(
                f"metric_thresholds.{metric}: bands are not ascending: {triple}. Grading "
                "against a non-monotone scale would put a better number in a worse band."
            )
        bands[metric] = triple
    if "f1_score" in bands:
        bands["f1"] = bands.pop("f1_score")
    for metric in (*METRICS, ACCURACY_METRIC):
        if metric not in bands:
            raise Refused(
                f"metric_thresholds has no band for {metric!r}: found {sorted(bands)}"
            )
    return bands


def grade(value: float, band: tuple[float, float, float]) -> str:
    """Production's own label for a point estimate. Percent, matching the config's units."""
    acceptable, good, excellent = band
    pct = value * 100
    if pct >= excellent:
        return "excellent"
    if pct >= good:
        return "good"
    if pct >= acceptable:
        return "acceptable"
    return "BELOW"


def load_packs() -> tuple[dict, dict]:
    packs = []
    for path in (PACK_A, PACK_B):
        if not path.exists():
            raise Refused(f"missing {_show(path)}")
        packs.append(json.loads(path.read_text(encoding="utf-8")))
    a, b = packs

    ca, cb = a["shared_contract"], b["shared_contract"]
    differing = [k for k in POOLABLE if ca.get(k) != cb.get(k)]
    if differing:
        raise Refused(
            f"the two packs do not share {differing}, so they were not measured by the same "
            "instrument and their discordant counts cannot be added. Pooling across a "
            "prompt or scorer change averages two different experiments."
        )
    if ca.get("testset_sha") == cb.get("testset_sha"):
        raise Refused(
            "both packs have the same testset_sha, so the 'second' pack is the same items "
            "again. Adding these counts would double-count every item and halve the "
            "apparent noise, which is the one pooling error that looks like more evidence."
        )
    return a, b


def main(out_path: Path | None = None) -> int:
    """Render the comparison. `out_path` overrides where the JSON lands.

    The parameter exists so tests do not write into the real tree, and that is not tidiness.
    `scripts/doc_claims.py` verifies published figures in `docs/` against
    `docs/reports/pooled-bands.json`. When the test suite regenerated that file as a side
    effect, a drift between a document and its source could be erased by a `pytest` run
    before any human saw it -- the checker would be comparing the document against numbers
    the test suite had just refreshed. A verification gate whose evidence its own test suite
    rewrites is not a gate.
    """
    try:
        bands = read_bands(PROD_CONFIG)
        pack_a, pack_b = load_packs()
    except Refused as exc:
        print(f"REFUSED: {exc}", file=sys.stderr)
        return 1

    n_a = pack_a["shared_contract"]["items"]
    n_b = pack_b["shared_contract"]["items"]
    models_a, models_b = pack_a["models"], pack_b["models"]
    both = [m for m in models_a if m in models_b]
    only_a = [m for m in models_a if m not in models_b]

    out: list[str] = []
    w = out.append

    w("=" * 100)
    w(f"POOLED COMPARISON  ---  {n_a} + {n_b} = {n_a + n_b} hand-labelled items")
    w("=" * 100)
    w("")
    w(f"  bands read from {_show(PROD_CONFIG)}")
    for metric in METRICS:
        acceptable, good, excellent = bands[metric]
        w(f"    {metric:<10} acceptable >= {acceptable:g}   good >= {good:g}   "
          f"excellent >= {excellent:g}")
    w("")
    w(f"  pooled contract  prompt {pack_a['shared_contract']['prompt_sha'][:12]}  "
      f"scorer {pack_a['shared_contract']['scoring_code_sha'][:12]}  "
      f"repeats {pack_a['shared_contract']['repeats']}")
    w("")

    w("-" * 100)
    w("1. IS IT GOOD ENOUGH?   every metric against production's own bands")
    w("-" * 100)
    w("")
    w(f"  {'model':<18} {'dimension':<12} "
      + "  ".join(f"{m:>9} {'band':<11}" for m in METRICS))
    for key in list(models_a):
        for dim in DIMS:
            detail = models_a[key].get("scoring_detail", {}).get(dim)
            if detail is None:
                w(f"  {DISPLAY.get(key, key):<18} {dim:<12} NOT SCORED")
                continue
            cells = "  ".join(
                f"{detail[m] * 100:>8.1f}% {grade(detail[m], bands[m]):<11}" for m in METRICS
            )
            w(f"  {DISPLAY.get(key, key):<18} {dim:<12} {cells}")
    w("")
    w(f"  (pack A only, n={n_a}. Pack B carries the same shape for the two models run on it.)")
    w("")

    w("-" * 100)
    w("2. BUSINESS ACCURACY   call-level exact correctness vs hand-computed ground truth")
    w("-" * 100)
    w("")
    w("  The primary decision metric. Read straight off the paired 2x2, whose four cells are")
    w("  call-level outcomes and must sum to the item count, so accuracy is (both_right +")
    w("  that arm's only_right) / total. No new scoring, and nothing here can drift from the")
    w("  paired table below because both are the same four numbers.")
    w("")
    w(f"  {'dimension':<12} {'pack':<14} {'n':>4} {'Gemini':>16} {'candidate':>16}")

    accuracy_rows = []
    for key in both:
        if key == INCUMBENT:
            continue
        for dim in DIMS:
            a = models_a[key]["paired_vs_gemini"][dim]
            b = models_b[key]["paired_vs_gemini"][dim]
            for label, t, n in ((f"pack A", a, n_a), (f"pack B", b, n_b),
                                ("POOLED", None, n_a + n_b)):
                if t is None:
                    inc = (a["both_right"] + a["incumbent_only"]
                           + b["both_right"] + b["incumbent_only"])
                    cand = (a["both_right"] + a["candidate_only"]
                            + b["both_right"] + b["candidate_only"])
                else:
                    inc = t["both_right"] + t["incumbent_only"]
                    cand = t["both_right"] + t["candidate_only"]
                w(f"  {dim:<12} {label:<14} {n:>4} "
                  f"{inc:>7}/{n} {100*inc/n:>6.1f}% {cand:>7}/{n} {100*cand/n:>6.1f}%")
                if label == "POOLED":
                    accuracy_rows.append({
                        "model": key, "dimension": dim, "n": n,
                        "incumbent_correct": inc, "candidate_correct": cand,
                        "incumbent_accuracy": round(inc / n, 4),
                        "candidate_accuracy": round(cand / n, 4),
                        "incumbent_band": grade(inc / n, bands["accuracy"]),
                        "candidate_band": grade(cand / n, bands["accuracy"]),
                    })
            w("")

    w("  Banded against production's ACCURACY thresholds, not F1's, where they differ.")
    w("")

    w("-" * 100)
    w("3. IS IT DIFFERENT?   paired sign test, pooled across both packs")
    w("-" * 100)
    w("")
    w("  d = items where exactly one of the pair was right. Everything else carries no")
    w("  information about which is better, so d -- not n -- is the sample size that counts.")
    w("")
    w(f"  {'model':<18} {'dimension':<12} {'d':>4} {'net':>5} {'band':>7}   verdict")

    pooled_rows = []
    for key in both:
        if key == INCUMBENT:
            continue
        for dim in DIMS:
            a = models_a[key]["paired_vs_gemini"][dim]
            b = models_b[key]["paired_vs_gemini"][dim]
            table = Disagreement(
                dimension=dim,
                both_right=a["both_right"] + b["both_right"],
                both_wrong=a["both_wrong"] + b["both_wrong"],
                incumbent_only_right=a["incumbent_only"] + b["incumbent_only"],
                candidate_only_right=a["candidate_only"] + b["candidate_only"],
            )
            verdict = paired_verdict(table)
            discordant = table.incumbent_only_right + table.candidate_only_right
            band = "--" if verdict.band is None else f"+/-{verdict.band}"
            w(f"  {DISPLAY.get(key, key):<18} {dim:<12} {discordant:>4} {table.net:>5} "
              f"{band:>7}   {verdict.verdict}")
            pooled_rows.append({
                "model": key, "dimension": dim, "discordant": discordant, "net": table.net,
                "band": verdict.band, "verdict": verdict.verdict,
                "pack_a": {"discordant": a["discordant"], "net": a["net"],
                           "verdict": a["verdict"]},
                "pack_b": {"discordant": b["discordant"], "net": b["net"],
                           "verdict": b["verdict"]},
            })
    w("")
    for key in only_a:
        if key == INCUMBENT:
            continue
        w(f"  {DISPLAY.get(key, key):<18} not pooled -- ran on pack A only. Its pack-A verdict")
        w(f"  {'':<18} stands on {n_a} items and is not comparable to a pooled row.")
    w("")
    w("  A band is a threshold on a point estimate; the verdict is a test. They answer")
    w("  different questions and a model can be in a higher band while the test says the")
    w("  difference is not resolvable. Both are printed so neither can be quoted alone.")
    w("")

    print("\n".join(out))

    payload = {
        "generated_from": "docs/reports/*-metrics.json; recomputes no model output",
        "items": {"pack_a": n_a, "pack_b": n_b, "pooled": n_a + n_b},
        "bands": {m: dict(zip(("acceptable", "good", "excellent"), bands[m]))
                  for m in (*METRICS, ACCURACY_METRIC)},
        "bands_source": _show(PROD_CONFIG),
        "pooled_contract": {k: pack_a["shared_contract"][k] for k in POOLABLE},
        "graded": {
            key: {
                dim: {
                    m: {"value": models_a[key]["scoring_detail"][dim][m],
                        "band": grade(models_a[key]["scoring_detail"][dim][m], bands[m])}
                    for m in METRICS
                }
                for dim in DIMS if dim in models_a[key].get("scoring_detail", {})
            }
            for key in models_a
        },
        "business_accuracy": accuracy_rows,
        "pooled_paired": pooled_rows,
        "not_pooled": {k: f"ran on pack A only (n={n_a})" for k in only_a if k != INCUMBENT},
    }
    out_path = out_path if out_path is not None else DOCS / "pooled-bands.json"
    out_path.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n",
                        encoding="utf-8")
    print(f"wrote {_show(out_path)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
