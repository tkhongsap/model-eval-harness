"""Did correcting the product labels change the scores, and is the change the fix?

WHY A SEPARATE SCRIPT FROM THE SCORECARD. The scorecard reports each run against its own
corpus, which is right and which is also not enough to attribute a difference. E24 scores
higher than E23 on product. So would a run that changed nothing but the weather, if these
models were unstable enough -- and they are: the E23 scorecard already measured 30 of 138
Typhoon items as unstable across three replicates.

So this uses the two-cell design the data hands us. The corpus fix changed EXACTLY one field,
`product`, on 81 of 138 rows; call_result, reason and phone_number are byte-identical between
v2 and v3, verified field by field. And the audio changed on exactly those 81 rows and no
others, verified by per-item sha256. That gives three cells:

  CONTROL       57 calls  label unchanged AND audio byte-identical.
                          Nothing the fix did reaches these, so any movement is the
                          noise floor between two runs.

  CONTRADICTED  30 calls  a scenario whose dialogue NAMES the product, carrying a label that
                          contradicted it. This is the defect the blind audit found.

  RE-ROLLED     51 calls  product-neutral scenarios that drew a different value because the
                          free mix changed. Nothing was wrong with the old label and nothing
                          is wrong with the new one. Reported separately, because folding
                          these into the treated group would turn a 30-call defect into an
                          81-call one.

Read CONTROL first. A gain in CONTRADICTED that CONTROL does not show is the fix. A gain in
both is the two runs differing for reasons that have nothing to do with the corpus.

Usage:
    python scripts/corpus_fix_effect.py --before-run out/runs/<e23> --after-run out/runs/<e24>
"""

from __future__ import annotations

import argparse
import csv
import importlib.util
import json
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "src"))

# Scenarios whose dialogue names the product outright. Same list as the generator's forced
# set and as corpus_diff.py; a disagreement between the three is worth surfacing loudly.
FORCED = {"net_slow", "coverage_issue", "sim_replace", "mnp", "device_promo"}

# The scorer's product spelling, so this cannot drift from the scorecard's.
NORM = {"TOL": "tol", "TVS": "tvs", "POSTPAID": "postpaid", "UNKNOWN": "unknown"}


class Refused(SystemExit):
    def __init__(self, msg: str) -> None:
        super().__init__(f"EFFECT REFUSING: {msg}")


def load_scorer():
    spec = importlib.util.spec_from_file_location(
        "e23s", REPO / "scripts" / "experiment23_score.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def business(pack: Path) -> dict:
    with (pack / "ground-truth" / "business.csv").open(encoding="utf-8") as fh:
        return {f"ASR-{i + 1:03d}": r for i, r in enumerate(csv.DictReader(fh))}


def manifest(pack: Path) -> dict:
    return {r["item_id"]: r for r in json.loads(
        (pack / "manifest.json").read_text(encoding="utf-8"))}


def scenarios(pack: Path) -> dict:
    return {f"ASR-{i + 1:03d}": d["scenario"] for i, d in enumerate(
        json.loads((pack / "dialogues" / "index.json").read_text(encoding="utf-8")))}


def hit(fields, arm, item, truth) -> bool | None:
    """Did the arm name this call's ground-truth product?"""
    rec = (fields.get(arm) or {}).get(item)
    if not isinstance(rec, dict):
        return None
    got = {NORM.get(str(p).upper(), str(p).lower()) for p in (rec.get("products") or [])}
    return (truth[item]["product"] in got) if got else None


def rate(fields_a, fields_b, arm, items, gt_a, gt_b) -> tuple[float, float, float, int]:
    keys = [i for i in items
            if hit(fields_a, arm, i, gt_a) is not None
            and hit(fields_b, arm, i, gt_b) is not None]
    if not keys:
        return (float("nan"), float("nan"), float("nan"), 0)
    a = sum(1 for i in keys if hit(fields_a, arm, i, gt_a)) / len(keys)
    b = sum(1 for i in keys if hit(fields_b, arm, i, gt_b)) / len(keys)
    return (a, b, b - a, len(keys))


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="corpus_fix_effect")
    ap.add_argument("--before-run", type=Path, required=True)
    ap.add_argument("--after-run", type=Path, required=True)
    ap.add_argument("--before-pack", type=Path, default=REPO / "asr-eval-v2")
    ap.add_argument("--after-pack", type=Path, default=REPO / "asr-eval-v3")
    ap.add_argument("--json", type=Path, default=None)
    args = ap.parse_args(argv if argv is not None else sys.argv[1:])

    S = load_scorer()
    gt_a, gt_b = business(args.before_pack), business(args.after_pack)
    man_a, man_b = manifest(args.before_pack), manifest(args.after_pack)
    scen = scenarios(args.before_pack)
    if scen != scenarios(args.after_pack):
        raise Refused("the two packs do not share a scenario plan; no cell below would mean "
                      "anything")

    same_audio = {k for k in man_a if k in man_b
                  and man_a[k]["sha256"] == man_b[k]["sha256"]}
    moved = {k for k in gt_b if gt_a[k]["product"] != gt_b[k]["product"]}
    control = sorted((set(gt_b) - moved) & same_audio)
    contradicted = sorted(k for k in moved if scen[k] in FORCED)
    rerolled = sorted(k for k in moved if scen[k] not in FORCED)

    # The design only holds if audio moved exactly where the label did.
    if moved & same_audio or (set(gt_b) - moved) - same_audio:
        raise Refused(
            "audio and label did not move together. CONTROL would then contain calls the "
            "regeneration changed, and its delta would stop being a noise floor.")

    other = [f for f in ("call_result", "main", "secondary", "third")
             if any(gt_a[k][f] != gt_b[k][f] for k in gt_b)]
    if other:
        raise Refused(
            f"{other} also differ between the two corpora. This design attributes a product "
            "change to the product fix; with another field moving too it cannot.")

    fa, _, _ = S.collapse(args.before_run, "first")
    fb, _, _ = S.collapse(args.after_run, "first")
    arms = sorted(set(fa) & set(fb))

    print(f"{args.before_pack.name} -> {args.after_pack.name}   "
          f"product is the ONLY ground-truth field that moved")
    print(f"  CONTROL      {len(control):3d}  label unchanged, audio byte-identical")
    print(f"  CONTRADICTED {len(contradicted):3d}  dialogue names the product, label "
          f"disagreed")
    print(f"  RE-ROLLED    {len(rerolled):3d}  product-neutral scenario, different draw")
    print(f"\narms in both runs: {arms}")

    header = (f"\n{'arm':18s} {'CONTROL':>21s}   {'CONTRADICTED':>21s}   {'RE-ROLLED':>21s}")
    print(header)
    # ASCII only: this repository is worked on from a Thai-locale Windows console (cp874),
    # where a Greek delta raises UnicodeEncodeError and kills the report after the header.
    print(f"{'':18s} {'before':>6s} {'after':>6s} {'delta':>7s}   "
          f"{'before':>6s} {'after':>6s} {'delta':>7s}   "
          f"{'before':>6s} {'after':>6s} {'delta':>7s}")
    print("-" * len(header))
    out = {}
    for arm in arms:
        cells = {name: rate(fa, fb, arm, items, gt_a, gt_b)
                 for name, items in (("control", control),
                                     ("contradicted", contradicted),
                                     ("rerolled", rerolled))}
        out[arm] = {k: {"before": v[0], "after": v[1], "delta": v[2], "n": v[3]}
                    for k, v in cells.items()}
        c, x, r = cells["control"], cells["contradicted"], cells["rerolled"]
        print(f"{arm:18s} {c[0]:6.1%} {c[1]:6.1%} {c[2]:+7.1%}   "
              f"{x[0]:6.1%} {x[1]:6.1%} {x[2]:+7.1%}   "
              f"{r[0]:6.1%} {r[1]:6.1%} {r[2]:+7.1%}")

    print("\nHOW TO READ IT")
    print("  CONTROL delta is the noise floor: same bytes in, same key, two runs.")
    print("  CONTRADICTED delta above that floor is what the corrected labels bought.")
    print("  RE-ROLLED is neither a defect nor a fix; it is reported so the defect count")
    print("  stays 30 rather than being inflated to 81 by calls that were never wrong.")

    if args.json:
        args.json.parent.mkdir(parents=True, exist_ok=True)
        args.json.write_text(json.dumps({
            "before_run": args.before_run.name, "after_run": args.after_run.name,
            "cells": {"control": control, "contradicted": contradicted,
                      "rerolled": rerolled},
            "arms": out,
        }, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"\nwrote {args.json}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
