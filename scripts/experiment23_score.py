"""Score Experiment 23: two pipelines, end to end, against authored business truth.

    PYTHONPATH=src python scripts/experiment23_score.py --run out/runs/<stamp>-e23 \\
        --pack asr-eval-v2

WHAT MAKES THIS DIFFERENT FROM E21. Experiment 21 ran the same six arms over the same audio
and could only report AGREEMENT -- whether two arms produced the same JSON -- because the
audio set had no business labels at all. Its own docstring says so: "no arm is scored as
right: a labeller that is reliably wrong agrees with itself 100%". Agreement cannot answer
the migration question, because two pipelines that are wrong in the same way agree perfectly.

E23's corpus carries `ground-truth/business.csv`, authored by the generator at the moment it
chose each outcome. So for the first time these arms can be scored for CORRECTNESS, and this
module does that.

IT REUSES THE PRODUCTION SCORER RATHER THAN REIMPLEMENTING IT. `evalharness.metrics` already
computes the three dimensions at production's own grain, with production's own denominators
and its reproduced NaN-phone defect. A second implementation here would drift from it, and
the two sets of numbers would look comparable while quietly not being. So the work in this
file is translation -- results.jsonl and business.csv into `Record` lists -- and everything
after that is the same code path the text packs go through.

THREE THINGS IT REFUSES TO DO:

  * **Report a mean over replicates.** Each item is collapsed with `modal()` first, exactly
    as E21 does. A model that answers differently on three identical requests has an
    instability problem, and averaging it away hides the finding rather than measuring it.
  * **Let CER near the decision.** The preregistration marks normalised CER DIAGNOSTIC ONLY.
    It explains WHY arms differ; the decision is business F1. This module does not compute
    CER at all, so it cannot be quoted from here by accident.
  * **Call a null result a tie.** `compare.paired_verdict` returns UNDERPOWERED when the
    discordant count is too low to test, and that verdict is printed as-is.
"""

from __future__ import annotations

import argparse
import collections
import csv
import json
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "src"))

from evalharness.compare import Disagreement, paired_verdict  # noqa: E402
from evalharness.intervals import clopper_pearson, wilson  # noqa: E402
from evalharness.labelspaces import RETENTION  # noqa: E402
from evalharness.metrics import (  # noqa: E402
    Record,
    score_call_result,
    score_product,
    score_reason,
)


def _extract_fields(response: dict) -> dict:
    """The runner's own field extraction, imported rather than copied.

    `scripts/` is not a package, so this loads the module by path. The alternative --
    reimplementing the extraction here -- is how two definitions of "the product key" come
    to disagree, which is the exact class of bug this project keeps finding.
    """
    import importlib.util

    global _E21
    try:
        return _E21.extract_fields(response)
    except NameError:
        spec = importlib.util.spec_from_file_location(
            "e21", Path(__file__).resolve().parent / "experiment21_pipeline_delta.py")
        _E21 = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(_E21)
        return _E21.extract_fields(response)

# item_id ASR-0NN maps to the call_id the composer stamped: 7100 + (NN - 1).
CALL_ID_BASE = 7100

DIMENSIONS = ("call_result", "reason", "product")
# `reason_exact` is a reporting-only view of `reason`: set equality rather than
# recall of the ground-truth reasons. Scored from the same rows, printed beside it,
# so the strict reading stays available and neither can be quoted as the other.
ACCURACY_VIEWS = ("call_result", "reason", "reason_exact", "product")


class Refused(SystemExit):
    def __init__(self, msg: str) -> None:
        super().__init__(f"E23 SCORING REFUSING: {msg}")


def item_to_call_id(item_id: str) -> str:
    return str(CALL_ID_BASE + int(item_id.split("-")[1]) - 1)


def load_truth(pack: Path) -> tuple[dict[str, list[Record]], dict[str, str]]:
    """(call_id -> its ground-truth rows, call_id -> phone)."""
    path = pack / "ground-truth" / "business.csv"
    if not path.exists():
        raise Refused(
            f"no authored ground truth at {path}. It is written by compose_dialogues.py "
            "and is what separates this experiment from E21, which could only measure "
            "agreement. Without it there is nothing to be correct against."
        )
    truth: dict[str, list[Record]] = collections.defaultdict(list)
    phones: dict[str, str] = {}
    with path.open(encoding="utf-8", newline="") as handle:
        for row in csv.DictReader(handle):
            call_id = row["call_id"]
            phones[call_id] = row["phone_number"]
            reasons = frozenset(
                r for r in (row["main"], row["secondary"], row["third"]) if r
            )
            truth[call_id].append(Record(
                call_id=call_id,
                phone=row["phone_number"],
                product=row["product"],
                call_result=row["call_result"],
                reasons=reasons,
            ))
    if not truth:
        raise Refused(f"{path} has a header but no rows")
    return dict(truth), phones


def modal(values: list[str]) -> tuple[str | None, bool]:
    """(most common value, was it unanimous). None when all replicates differ."""
    counts = collections.Counter(values)
    if not counts:
        return None, False
    top, n = counts.most_common(1)[0]
    if n == 1 and len(counts) > 1:
        return None, False
    return top, n == len(values)


def collapse(run_dir: Path, policy: str = "first") -> tuple[dict, dict, dict]:
    """(arm -> item -> fields, arm -> instability, arm -> failure counts).

    Replicates are collapsed BEFORE scoring, not averaged after. `unstable` counts items
    whose replicates did not agree, which is a reportable property of the arm rather than
    noise to be smoothed away.

    `policy` decides WHICH collapse, and the default is not a matter of taste.

      * ``first``  -- replicate 1 alone. This is what `experiments/retention-e23.plan.json`
                      preregistered, in its own words: "Every headline figure is computed
                      on replicate 1 alone. Replicates 2 and 3 feed ONLY the stability /
                      noise-floor metric. Preregistered because Experiment 2 showed a
                      metric crossing a decision band on a single draw from an arm that
                      flips; choosing the replicate after seeing three of them is choosing
                      the answer."
      * ``modal``  -- majority vote across all three replicates.

    This module shipped computing `modal` while the plan said `first`, and nobody noticed
    because both are defensible estimators. They are not interchangeable HERE: the arm
    disagrees with itself on 37 of 138 items and the published verdict cleared its band by
    exactly one call. A three-replicate vote is the better estimator in general and is
    still available; it is simply not the one that was preregistered, and swapping the
    analysis after seeing the data is the failure the preregistration exists to prevent.

    So `first` is the default, `modal` is opt-in, and the report prints both.
    """
    if policy not in {"first", "modal"}:
        raise Refused(f"unknown replicate policy {policy!r}; expected 'first' or 'modal'")
    path = run_dir / "results.jsonl"
    if not path.exists():
        raise Refused(f"no results at {path}")

    by_key: dict[tuple[str, str], list[dict]] = collections.defaultdict(list)
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.strip():
            rec = json.loads(line)
            by_key[(rec["arm"], rec["item_id"])].append(rec)
    # Sort by replicate index so `first` means replicate 1, not "whichever line the
    # concurrent writer happened to append first". With --jobs 8 the file order is
    # interleaved across arms and items, so this is load-bearing, not tidiness.
    for recs in by_key.values():
        recs.sort(key=lambda r: r.get("replicate", 0))

    collapsed: dict[str, dict[str, dict]] = collections.defaultdict(dict)
    unstable: dict[str, int] = collections.Counter()
    failures: dict[str, collections.Counter] = collections.defaultdict(
        collections.Counter
    )

    for (arm, item), recs in by_key.items():
        # Failure taxonomy first: an arm that never returned parseable JSON on an item has
        # no fields to collapse, and counting it as a wrong answer would confuse "got it
        # wrong" with "did not answer" -- two different problems with different fixes.
        statuses = [r.get("status", "unknown") for r in recs]
        for status in statuses:
            failures[arm][status] += 1

        # The runner stores the model's raw parsed JSON under `response`; the three
        # comparable dimensions are derived here, at scoring time, by the runner's own
        # `extract_fields`. Imported rather than reimplemented so there is exactly one
        # definition of what "the fields" means -- an earlier version of this module looked
        # for a `fields` key the runner has never written, found nothing, and silently
        # scored every arm 0.000 while reporting status=ok for all 411 records.
        for rec in recs:
            if rec.get("status") == "ok" and "fields" not in rec and rec.get("response"):
                rec["fields"] = _extract_fields(rec["response"])

        ok_all = [r for r in recs if r.get("status") == "ok" and r.get("fields")]
        if not ok_all:
            collapsed[arm][item] = {"__failed__": True, "statuses": statuses}
            continue

        # Instability is ALWAYS measured across every replicate, whatever the scoring
        # policy is. Measuring it over the single replicate being scored would report
        # every arm as perfectly stable by construction.
        keys_all = set().union(*(set(r["fields"]) for r in ok_all))
        for key in keys_all:
            vals = [json.dumps(r["fields"].get(key), ensure_ascii=False, sort_keys=True)
                    for r in ok_all]
            _, agreed = modal(vals)
            if not agreed:
                unstable[arm] += 1
                break

        ok = ok_all if policy == "modal" else ok_all[:1]
        keys = set().union(*(set(r["fields"]) for r in ok))
        fields: dict = {}
        for key in keys:
            vals = [json.dumps(r["fields"].get(key), ensure_ascii=False, sort_keys=True)
                    for r in ok]
            mode, _ = modal(vals)
            fields[key] = json.loads(mode) if mode is not None else None
        collapsed[arm][item] = fields

    return dict(collapsed), dict(unstable), {a: dict(c) for a, c in failures.items()}


def to_records(fields: dict, call_id: str, phone: str) -> list[Record]:
    """One prediction row per product the arm emitted, matching production's grain."""
    if fields.get("__failed__"):
        # An unparseable item still enters scoring, as a row that is wrong. Dropping it
        # would raise the arm's score for having failed, which is the single most
        # flattering bug a scorer can have.
        return [Record(call_id=call_id, phone=phone, product=None,
                       call_result=None, reasons=frozenset(), parse_ok=False)]
    out: list[Record] = []
    for product in fields.get("products") or []:
        key = product.lower()
        out.append(Record(
            call_id=call_id,
            phone=phone,
            product=key,
            call_result=(fields.get(f"{product}.outcome") or "").strip() or None,
            reasons=frozenset(fields.get(f"{product}.reasons") or []),
        ))
    if not out:
        out.append(Record(call_id=call_id, phone=phone, product=None,
                          call_result=None, reasons=frozenset()))
    return out


def build_pair(truth: dict, phones: dict, arm_fields: dict) -> tuple[list, list]:
    """(ground truth rows, prediction rows) over the items this arm actually ran."""
    gt: list[Record] = []
    pred: list[Record] = []
    for item, fields in sorted(arm_fields.items()):
        call_id = item_to_call_id(item)
        if call_id not in truth:
            raise Refused(
                f"{item} maps to call_id {call_id}, which has no ground-truth row. The "
                "pack and the run disagree about which corpus this is."
            )
        gt.extend(truth[call_id])
        pred.extend(to_records(fields, call_id, phones[call_id]))
    return gt, pred


def call_level_correct(gt: list[Record], pred: list[Record], dimension: str) -> dict:
    """call_id -> was this call entirely right on `dimension`.

    Call-level, not row-level: production acts on a call, and a call with one product row
    right and another wrong is not a correct call. This is the same grain
    `compare.comparison_clusters` uses, so the accuracy here and the paired test below are
    counting the same units.
    """
    def index(rows):
        out = collections.defaultdict(dict)
        for r in rows:
            out[r.call_id][r.product] = r
        return out

    gi, pi = index(gt), index(pred)
    verdict = {}
    for call_id, gt_products in gi.items():
        pred_products = pi.get(call_id, {})
        if dimension == "product":
            verdict[call_id] = set(gt_products) == set(pred_products)
            continue
        ok = set(gt_products) == set(pred_products)
        if ok:
            for product, g in gt_products.items():
                p = pred_products[product]
                if dimension == "call_result":
                    ok &= (g.call_result == p.call_result)
                elif dimension == "reason_exact":
                    ok &= (g.reasons == p.reasons)
                else:
                    # `reason` is scored as RECALL OF THE GROUND-TRUTH REASONS, not as set
                    # equality, because the two answer different questions and only one of
                    # them was asked.
                    #
                    # The schema offers main/secondary/third and the labeller fills all
                    # three on 95 of 112 calls; ground truth carries one. Exact set equality
                    # therefore scores 0/138 while the model is naming the right reason most
                    # of the time -- a number that says more about the schema's arity than
                    # about the model. `reason_exact` is kept alongside for anyone who wants
                    # the strict reading, and both are reported.
                    ok &= bool(g.reasons) and g.reasons <= p.reasons
        verdict[call_id] = bool(ok)
    return verdict


def main() -> int:
    ap = argparse.ArgumentParser(prog="experiment23_score")
    ap.add_argument("--run", type=Path, required=True)
    ap.add_argument("--pack", type=Path, default=REPO / "asr-eval-v2")
    ap.add_argument("--incumbent", default="gemini-audio")
    ap.add_argument("--candidate", default="qwen-pipeline")
    ap.add_argument("--json", type=Path, default=None)
    ap.add_argument("--allow-thin-coverage", action="store_true",
                    help="score arms that answered under 90%% of their items. Off by "
                         "default: a partial arm's F1 reads exactly like a model result.")
    ap.add_argument("--replicate-policy", choices=("first", "modal"), default="first",
                    help="first = replicate 1 alone, as preregistered (default); "
                         "modal = majority vote over all three replicates")
    args = ap.parse_args()

    truth, phones = load_truth(args.pack)
    collapsed, unstable, failures = collapse(args.run, args.replicate_policy)

    # A total shutout is never a real result. Four independent arms -- one of which reads
    # the PERFECT reference transcript -- cannot all score zero unless the join is broken.
    # This refusal exists because that exact thing happened: the scorer looked for a record
    # key the runner does not write, treated all 411 records as unparseable, and printed a
    # tidy table of 0.000s that looked like a finding.
    usable = sum(1 for arm in collapsed.values()
                 for fields in arm.values() if not fields.get("__failed__"))
    total_items = sum(len(arm) for arm in collapsed.values())
    if total_items and usable == 0:
        raise Refused(
            f"every one of {total_items} arm-items came back unusable, including the "
            "`ceiling` arm that reads the exact reference transcript. That is a broken "
            "join, not four models failing identically. Check that the runner's record "
            "shape still matches what collapse() reads."
        )

    # A TOTAL shutout is not the only shutout. An arm that answered a fraction of its calls
    # is scored on that fraction, and the F1 comes out looking like a model result.
    #
    # Measured 2026-08-20 on run 20260820-055342Z: a VPN drop mid-run produced 297
    # "unreachable host" errors, and `format-control` completed 130 of 414 calls. It scored
    # 0.358 against the 0.663 it had scored a day earlier -- a 0.3 collapse that reads
    # exactly like a broken arm and was in fact a broken network. `ceiling` and
    # `qwen-pipeline` lost 14.5% each; `gemini-audio`, which goes out through OpenRouter
    # rather than the VPN, lost 1.2% and looked fine beside them.
    #
    # Section 3 printed the failure counts, and that was not enough: the F1 table above it
    # still showed tidy numbers, and a reader comparing arms would have believed them. So
    # this refuses instead. The floor is deliberately low -- it is not a quality bar, it is
    # a "did the run actually happen" bar.
    MIN_COVERAGE = 0.90
    # Denominator is the TRUTH set, not what happens to be in the run file.
    #
    # Counting against the run's own rows makes the gate blind in exactly the case it is
    # for: an item whose every replicate failed leaves no row at all, so it vanishes from
    #  and the surviving items look like 100% coverage. Worse, the standard
    # recovery -- strip the failed rows and --resume -- deletes the evidence on purpose.
    expected = len(truth)
    thin = {}
    for arm, items in collapsed.items():
        answered = sum(1 for fields in items.values() if not fields.get("__failed__"))
        share = answered / expected if expected else 1.0
        if share < MIN_COVERAGE:
            thin[arm] = (answered, expected, share)
    if thin and not args.allow_thin_coverage:
        lines = "\n".join(
            f"    {arm}: {a} of {n} items answered ({s:.1%})"
            for arm, (a, n, s) in sorted(thin.items(), key=lambda kv: kv[1][2]))
        raise Refused(
            f"{len(thin)} arm(s) answered less than {MIN_COVERAGE:.0%} of their items:\n"
            f"{lines}\n"
            "  Scoring an arm on the calls that happened to succeed reports a transport "
            "failure as a model result, and the F1 looks entirely plausible while it does "
            "so. Re-run the missing calls (--resume drops straight back in) or pass "
            "--allow-thin-coverage if a partial arm is genuinely what you want to read."
        )

    print("=" * 78)
    print(f"EXPERIMENT 23 -- end to end, scored against authored truth")
    print(f"run  {args.run.name}")
    print(f"pack {args.pack.name}   {len(truth)} calls with business labels")
    print("=" * 78)

    scored: dict[str, dict] = {}
    for arm in sorted(collapsed):
        gt, pred = build_pair(truth, phones, collapsed[arm])
        scored[arm] = {
            "call_result": score_call_result(gt, pred, RETENTION.call_result),
            "reason": score_reason(gt, pred, RETENTION.reason),
            "product": score_product(gt, pred, RETENTION.product),
            "_correct": {d: call_level_correct(gt, pred, d) for d in ACCURACY_VIEWS},
            "_items": len(collapsed[arm]),
        }

    # ---- 1. the primary metric ----------------------------------------------------
    print("\n1. BUSINESS F1 (PRIMARY) and accuracy, with intervals at alpha 1/64 per side")
    print(f"\n  {'arm':18} {'dimension':12} {'F1':>7} {'prec':>7} {'recall':>7} "
          f"{'accuracy':>20}")
    payload_arms = {}
    for arm in sorted(scored):
        for dim in DIMENSIONS:
            result = scored[arm][dim]
            correct = scored[arm]["_correct"][dim]
            hits, n = sum(correct.values()), len(correct)
            interval = wilson(hits, n) if n else None
            acc = (f"{hits}/{n} " + f"[{interval.low:.3f},{interval.high:.3f}]"
                   if interval else "--")
            print(f"  {arm:18} {dim:12} {result.weighted('f1'):>7.3f} "
                  f"{result.weighted('precision'):>7.3f} "
                  f"{result.weighted('recall'):>7.3f} {acc:>20}")
            payload_arms.setdefault(arm, {})[dim] = {
                "f1": result.weighted("f1"),
                "precision": result.weighted("precision"),
                "recall": result.weighted("recall"),
                "accuracy_correct": hits,
                "accuracy_n": n,
                "accuracy": hits / n if n else None,
                "ci_wilson": [interval.low, interval.high] if interval else None,
                "ci_clopper_pearson": (
                    [clopper_pearson(hits, n).low, clopper_pearson(hits, n).high]
                    if n else None
                ),
                "classes": [
                    {"label": c.label, "support": c.weight, "tp": c.tp, "fp": c.fp,
                     "fn": c.fn, "precision": c.precision, "recall": c.recall,
                     "f1": c.f1}
                    for c in result.classes if c.weight
                ],
            }
        print()

    # ---- 2. is the difference real ------------------------------------------------
    inc, cand = args.incumbent, args.candidate
    paired = {}
    if inc in scored and cand in scored:
        print(f"2. IS THE DIFFERENCE REAL?  {cand} vs {inc}, paired at the call grain")
        print(f"\n  {'dimension':12} {'both':>5} {'neither':>8} {'inc':>5} {'cand':>5} "
              f"{'d':>4} {'net':>5} {'band':>7}  verdict")
        for dim in DIMENSIONS:
            a = scored[inc]["_correct"][dim]
            b = scored[cand]["_correct"][dim]
            shared = sorted(set(a) & set(b))
            both = sum(1 for k in shared if a[k] and b[k])
            neither = sum(1 for k in shared if not a[k] and not b[k])
            only_i = sum(1 for k in shared if a[k] and not b[k])
            only_c = sum(1 for k in shared if b[k] and not a[k])
            table = Disagreement(dimension=dim, both_right=both, both_wrong=neither,
                                 incumbent_only_right=only_i, candidate_only_right=only_c)
            verdict = paired_verdict(table)
            band = "--" if verdict.band is None else f"+/-{verdict.band}"
            print(f"  {dim:12} {both:>5} {neither:>8} {only_i:>5} {only_c:>5} "
                  f"{only_i + only_c:>4} {table.net:>5} {band:>7}  {verdict.verdict}")
            paired[dim] = {
                "both_right": both, "both_wrong": neither,
                "incumbent_only": only_i, "candidate_only": only_c,
                "discordant": only_i + only_c, "net": table.net,
                "band": verdict.band, "verdict": verdict.verdict,
            }
        print("\n  UNDERPOWERED means the arms disagreed on too few calls to test, NOT that")
        print("  they are the same. The two are different findings and are kept apart.")

    # ---- 3. stability and failures ------------------------------------------------
    print("\n3. STABILITY AND FAILURES")
    print(f"\n  {'arm':18} {'items':>6} {'unstable':>9}   outcome counts")
    for arm in sorted(scored):
        counts = ", ".join(f"{k}={v}" for k, v in sorted(failures.get(arm, {}).items()))
        print(f"  {arm:18} {scored[arm]['_items']:>6} {unstable.get(arm, 0):>9}   {counts}")
    print("\n  unstable = items whose replicates did not agree on every field. Measured")
    print("  over ALL three replicates whatever the scoring policy is -- measuring it over")
    print("  the one replicate being scored would report every arm as perfectly stable by")
    print("  construction. Never averaged: a model that answers three ways to one question")
    print("  has a problem the mean would hide.")
    if args.replicate_policy == "first":
        print("\n  scored on REPLICATE 1 ALONE, as preregistered. Re-run with")
        print("  --replicate-policy modal for the three-replicate majority vote.")
    else:
        print("\n  scored on the MODAL vote over three replicates. This is NOT what")
        print("  experiments/retention-e23.plan.json preregistered -- that says replicate 1")
        print("  alone. Both are reported; the preregistered one is the headline.")

    if args.json:
        args.json.write_text(json.dumps({
            "run": args.run.name, "pack": args.pack.name,
            # Stamped so a published figure can never be traced back to the wrong collapse.
            "replicate_policy": args.replicate_policy,
            "calls_with_truth": len(truth),
            "alpha_per_side": 1 / 64,
            "arms": payload_arms,
            "paired": {"incumbent": inc, "candidate": cand, "dimensions": paired},
            "unstable_items": unstable,
            "failure_counts": failures,
        }, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"\nwrote {args.json}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
