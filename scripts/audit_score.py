"""Score reviewer labels against the corpus, and apply the preregistered decision rule.

    PYTHONPATH=src python scripts/audit_score.py --reviews alice.csv bob.csv

WHAT THIS ANSWERS. Not "were the models right" -- that is what the F1 table is for. This asks
whether the LABELS the models were scored against are what an independent reader, given only
the transcript and the written spec, would have written. Where a reader disagrees with the
corpus, the corpus is the thing that is wrong.

THE CONTROLS ARE THE MEASUREMENT, NOT THE DISPUTES. Reading agreement on disputed cases alone
tells you nothing, because you have no idea what agreement looks like when nothing is wrong.
Two reviewers reading clean calls against a clear spec do not agree 100% -- this repository's
own LLM judge disagreed with ITSELF on 18.1% of units at temperature 0
(`docs/llm-judge-direction.md:43`), and humans are not steadier than that. So the number that
matters is the GAP:

    control agreement    what agreement looks like when the corpus is not in dispute
    dispute agreement    what it looks like where the model and the corpus disagreed
    the gap between them is the finding

A dispute rate at or near the control rate means the corpus was right and the models were
wrong. A dispute rate far below it means the corpus is wrong where the models said it was.
A LOW CONTROL RATE means neither conclusion is available and the exercise has measured the
spec's clarity instead -- which is itself worth knowing, and is reported rather than hidden.

THE DECISION RULE IS PREREGISTERED. Written into the plan before any reviewer saw a case, so
it cannot be chosen afterwards:

  * reviewer agrees with the corpus  -> the label stands; a model that missed it was wrong
  * reviewer disagrees               -> the benchmark is wrong; relabel or drop, recompute
  * reviewers disagree with each other -> genuinely ambiguous; out of the scored set
  * >= 20% of audited disputes against the corpus -> the benchmark does not support a
    migration recommendation until it is repaired, whatever the F1 table says

MULTIPLE REVIEWERS. Aggregated by strict majority with ties recorded as `no_majority`, the
same pattern `judge._unit_aggregation` (`judge.py:1193`) uses -- a tie is a finding about the
case, not a number to round away.
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

from evalharness.labelspaces import RETENTION  # noqa: E402

PRODUCTS = ("Postpaid", "TOL", "TVS", "unknown")

# Preregistered. See the module docstring.
DISPUTE_THRESHOLD = 0.20


class Refused(SystemExit):
    def __init__(self, message: str) -> None:
        super().__init__(f"audit_score REFUSING: {message}")


def load_key(path: Path) -> dict[str, dict]:
    if not path.exists():
        raise Refused(
            f"no answer key at {path}. It is written by audit_packet.py into out/ (which is "
            "gitignored) so it cannot be sent alongside the packet by accident."
        )
    key = {}
    with path.open(encoding="utf-8", newline="") as handle:
        for row in csv.DictReader(handle):
            key[row["case_id"]] = {
                "group": row["group"],
                "call_id": row["call_id"],
                "item_id": row["item_id"],
                "product": set(row["expected_product"].split("|")),
                "call_result": set(row["expected_call_result"].split("|")),
            }
    if not key:
        raise Refused(f"{path} has a header but no rows")
    return key


def load_reviews(paths: list[Path], key: dict) -> dict[str, dict[str, dict]]:
    """reviewer -> case_id -> {product, call_result, evidence}."""
    reviews: dict[str, dict[str, dict]] = {}
    for path in paths:
        if not path.exists():
            raise Refused(f"no review file at {path}")
        name = path.stem
        rows: dict[str, dict] = {}
        with path.open(encoding="utf-8", newline="") as handle:
            for row in csv.DictReader(handle):
                case = (row.get("case_id") or "").strip()
                if not case:
                    continue
                if case not in key:
                    raise Refused(
                        f"{path.name} labels case {case!r}, which is not in the answer key. "
                        "Reviewer answers and key must come from the same packet -- case ids "
                        "are seed-scoped precisely so a mismatch cannot pass silently."
                    )
                product = (row.get("product") or "").strip()
                outcome = (row.get("call_result") or "").strip()
                if product and product not in PRODUCTS:
                    raise Refused(f"{path.name}: {case} has product {product!r}, not in "
                                  f"{list(PRODUCTS)}")
                if outcome and outcome not in RETENTION.call_result:
                    raise Refused(f"{path.name}: {case} has call_result {outcome!r}, not in "
                                  f"{list(RETENTION.call_result)}")
                rows[case] = {"product": product, "call_result": outcome,
                              "evidence": (row.get("evidence") or "").strip()}
        if not rows:
            raise Refused(f"{path.name} has no answered cases")
        reviews[name] = rows
    return reviews


def aggregate(reviews: dict[str, dict[str, dict]], case: str, field: str):
    """(consensus value, was there one, how many answered). Ties are `None`."""
    votes = [r[case][field] for r in reviews.values()
             if case in r and r[case][field]]
    if not votes:
        return None, False, 0
    counts = collections.Counter(votes)
    top, n = counts.most_common(1)[0]
    if list(counts.values()).count(n) > 1:
        return None, False, len(votes)          # tie -> no majority
    return top, True, len(votes)


def main() -> int:
    ap = argparse.ArgumentParser(prog="audit_score")
    ap.add_argument("--reviews", type=Path, nargs="+", required=True)
    ap.add_argument("--key", type=Path, default=REPO / "out" / "audit-answer-key.csv")
    ap.add_argument("--json", type=Path, default=None)
    args = ap.parse_args()

    key = load_key(args.key)
    reviews = load_reviews(args.reviews, key)

    per_group: dict[str, collections.Counter] = collections.defaultdict(collections.Counter)
    against: list[dict] = []
    ties: list[str] = []

    for case, expected in key.items():
        answered = [r for r in reviews.values() if case in r and r[case]["call_result"]]
        if not answered:
            per_group[expected["group"]]["unreviewed"] += 1
            continue

        outcome, decided_o, _n = aggregate(reviews, case, "call_result")
        product, decided_p, _m = aggregate(reviews, case, "product")
        if not decided_o or not decided_p:
            per_group[expected["group"]]["no_majority"] += 1
            ties.append(case)
            continue

        agrees = (outcome in expected["call_result"]
                  and product.lower() in {p.lower() for p in expected["product"]})
        per_group[expected["group"]]["agree" if agrees else "against"] += 1
        if not agrees:
            against.append({
                "case_id": case, "item_id": expected["item_id"],
                "group": expected["group"],
                "corpus_product": sorted(expected["product"]),
                "corpus_call_result": sorted(expected["call_result"]),
                "reviewer_product": product, "reviewer_call_result": outcome,
                "evidence": [r[case]["evidence"] for r in reviews.values()
                             if case in r and r[case]["evidence"]],
            })

    print("=" * 74)
    print("REVIEWER vs CORPUS")
    print(f"reviewers: {', '.join(sorted(reviews))}   cases in key: {len(key)}")
    print("=" * 74)
    print(f"\n  {'group':18}{'agree':>7}{'against':>9}{'no maj.':>9}{'unrev.':>8}"
          f"{'agreement':>11}")
    rates: dict[str, float] = {}
    for group in ("control", "product_mismatch", "outcome_error"):
        c = per_group.get(group, collections.Counter())
        decided = c["agree"] + c["against"]
        rate = c["agree"] / decided if decided else float("nan")
        rates[group] = rate
        shown = f"{rate:.1%}" if decided else "--"
        print(f"  {group:18}{c['agree']:>7}{c['against']:>9}{c['no_majority']:>9}"
              f"{c['unreviewed']:>8}{shown:>11}")

    control = rates.get("control", float("nan"))
    disputed_agree = sum(per_group[g]["agree"] for g in ("product_mismatch", "outcome_error"))
    disputed_against = sum(per_group[g]["against"]
                           for g in ("product_mismatch", "outcome_error"))
    disputed = disputed_agree + disputed_against
    dispute_rate = disputed_against / disputed if disputed else float("nan")

    print("\n" + "-" * 74)
    print("READING THIS")
    if control == control and control < 0.80:
        print(f"  STOP. Control agreement is {control:.1%}. Reviewers disagree with the")
        print("  corpus on cases nobody disputed, so nothing can be concluded about the")
        print("  disputed ones -- this has measured the spec's clarity, not the corpus's")
        print("  correctness. That is a real finding: the rules need work before the")
        print("  labels can be audited against them.")
    elif control == control:
        print(f"  Control agreement is {control:.1%} -- what agreement looks like when the")
        print("  corpus is NOT in dispute. Read the disputed rate against that, not")
        print("  against 100%: this repository's own judge disagreed with itself on 18.1%")
        print("  of units at temperature 0, and humans are not steadier.")
        print()
        print(f"  Disputed cases: {disputed_against} of {disputed} went AGAINST the corpus "
              f"({dispute_rate:.1%}).")
        if dispute_rate >= DISPUTE_THRESHOLD:
            print()
            print(f"  >= {DISPUTE_THRESHOLD:.0%} THRESHOLD CROSSED. Per the preregistered")
            print("  decision rule, the benchmark does not support a migration")
            print("  recommendation until these labels are repaired -- whatever the F1")
            print("  table says. Relabel or drop the calls below and recompute every")
            print("  published figure.")
        else:
            print()
            print(f"  Below the {DISPUTE_THRESHOLD:.0%} threshold. The corpus largely holds "
                  "where it was")
            print("  challenged; models that missed these calls were wrong.")

    if ties:
        print(f"\n  {len(ties)} case(s) had no majority -- genuinely ambiguous, and they come")
        print("  out of the scored set rather than being rounded to whichever side won.")
        # With two reviewers a "strict majority" is unanimity, so every disagreement is a
        # tie. That is arithmetically correct and practically useless on a hard task -- and
        # it is invisible from the numbers alone, so it is said here.
        if len(reviews) == 2 and len(ties) > 0.25 * len(key):
            print()
            print(f"  NOTE: with 2 reviewers a strict majority IS unanimity, so every")
            print(f"  disagreement lands here. {len(ties)} of {len(key)} cases went")
            print("  unresolved for that reason alone. A third reviewer would resolve most")
            print("  of them; without one, this packet can only speak to the cases the two")
            print("  reviewers happened to agree on, and the disputed-rate above is")
            print("  computed over a much smaller sample than the packet size suggests.")
        elif len(reviews) == 1:
            print()
            print("  NOTE: one reviewer, so there are no ties by construction and no way to")
            print("  separate 'the corpus is wrong' from 'this reviewer read it that way'.")
            print("  Record the result as single-shot.")

    if against:
        print("\n" + "-" * 74)
        print(f"CASES THE REVIEWERS LABELLED DIFFERENTLY  ({len(against)})")
        for row in sorted(against, key=lambda r: r["item_id"]):
            print(f"\n  {row['item_id']}  [{row['group']}]")
            print(f"    corpus   product={row['corpus_product']} "
                  f"call_result={row['corpus_call_result']}")
            print(f"    reviewer product={row['reviewer_product']!r} "
                  f"call_result={row['reviewer_call_result']!r}")
            for ev in row["evidence"][:2]:
                print(f"    because  {ev[:96]}")

    if args.json:
        args.json.parent.mkdir(parents=True, exist_ok=True)
        args.json.write_text(json.dumps({
            "reviewers": sorted(reviews),
            "cases": len(key),
            "by_group": {g: dict(c) for g, c in per_group.items()},
            "control_agreement": None if control != control else control,
            "disputed_against_rate": None if dispute_rate != dispute_rate else dispute_rate,
            "threshold": DISPUTE_THRESHOLD,
            "no_majority": ties,
            "against": against,
        }, indent=2, ensure_ascii=False), encoding="utf-8")
        print(f"\n  wrote {args.json}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
