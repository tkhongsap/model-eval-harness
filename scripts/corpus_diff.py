"""Compare two rendered corpora: what moved in the labels, and what moved in the audio.

WHY THIS EXISTS. `retention-e24.plan.json` claims two things a reader should be able to check
rather than take on trust:

  * that 30 of 138 product labels were CORRECTED and 51 merely RE-ROLLED, which is the
    difference between "the benchmark was wrong on 22% of calls" and "the benchmark was wrong
    on 59% of calls". Only one of those is true, and the plan says which.
  * that the audio changed exactly where the label changed and nowhere else, which is what
    rules out the regeneration having moved something nobody asked it to.

A number in a preregistration that no command re-derives is a number that decays into folklore
the first time someone edits the generator.

WHAT MAKES THE COMPARISON VALID. The two corpora must share a scenario plan -- otherwise every
difference is attributable to a reshuffle rather than to the fix -- so that is asserted first
and the script refuses if it does not hold.

Usage:
    python scripts/corpus_diff.py --before asr-eval-v2 --after asr-eval-v3
    python scripts/corpus_diff.py --before asr-eval-v2 --after asr-eval-v3 --json out/diff.json
"""

from __future__ import annotations

import argparse
import csv
import json
import sys
from collections import Counter
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]

# Scenarios whose dialogue names the product outright, and what it names. Kept here as the
# READER's copy: if this disagrees with business_labels.PRODUCT_BY_SCENARIO, one of them has
# been edited without the other and the disagreement is worth surfacing.
FORCED = {
    "net_slow": "tol",
    "coverage_issue": "postpaid",
    "sim_replace": "postpaid",
    "mnp": "postpaid",
    "device_promo": "postpaid",
}


class Refused(SystemExit):
    def __init__(self, msg: str) -> None:
        super().__init__(f"CORPUS-DIFF REFUSING: {msg}")


def load(pack: Path) -> tuple[list[dict], list[dict], dict[str, dict]]:
    business = pack / "ground-truth" / "business.csv"
    index = pack / "dialogues" / "index.json"
    manifest = pack / "manifest.json"
    for required in (business, index):
        if not required.is_file():
            raise Refused(f"{required} does not exist")
    with business.open(encoding="utf-8") as fh:
        rows = list(csv.DictReader(fh))
    idx = json.loads(index.read_text(encoding="utf-8"))
    man = {}
    if manifest.is_file():
        man = {r["item_id"]: r for r in json.loads(manifest.read_text(encoding="utf-8"))}
    return rows, idx, man


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="corpus_diff")
    ap.add_argument("--before", type=Path, required=True)
    ap.add_argument("--after", type=Path, required=True)
    ap.add_argument("--json", type=Path, default=None)
    args = ap.parse_args(argv if argv is not None else sys.argv[1:])

    before = args.before if args.before.is_absolute() else REPO / args.before
    after = args.after if args.after.is_absolute() else REPO / args.after
    rows_b, idx_b, man_b = load(before)
    rows_a, idx_a, man_a = load(after)

    if len(rows_b) != len(rows_a):
        raise Refused(f"{len(rows_b)} rows before, {len(rows_a)} after; not the same set")

    # business.csv and index.json are written in the same loop in plan order, so position is
    # the join. Checked rather than assumed -- index.json carries the product too.
    for label, rows, idx in (("before", rows_b, idx_b), ("after", rows_a, idx_a)):
        if not all(r["product"] == i["product"] for r, i in zip(rows, idx)):
            raise Refused(
                f"{label}: business.csv and index.json disagree row-for-row, so the join by "
                "position is invalid and every number below would be silently misattributed"
            )

    scen_b = [d["scenario"] for d in idx_b]
    if scen_b != [d["scenario"] for d in idx_a]:
        raise Refused(
            "the two corpora do not share a scenario plan. Every difference would then be "
            "attributable to a reshuffle rather than to the product fix, and the counts "
            "below would mean nothing."
        )

    corrected, rerolled = Counter(), Counter()
    pairs = Counter()
    unchanged = 0
    for row_b, row_a, scenario in zip(rows_b, rows_a, scen_b):
        if row_b["product"] == row_a["product"]:
            unchanged += 1
            continue
        if scenario in FORCED:
            corrected[scenario] += 1
            pairs[(row_b["product"], row_a["product"])] += 1
        else:
            rerolled[scenario] += 1

    n = len(rows_b)
    n_corr, n_roll = sum(corrected.values()), sum(rerolled.values())
    forced_total = sum(1 for s in scen_b if s in FORCED)

    print(f"{before.name}  ->  {after.name}     {n} calls, scenario plan identical\n")
    print(f"  product unchanged        {unchanged:4d}")
    print(f"  CORRECTED (contradicted) {n_corr:4d}   {n_corr / n:6.1%} of the set")
    print(f"  re-rolled (mix change)   {n_roll:4d}   {n_roll / n:6.1%} of the set")
    print(f"\n  of {forced_total} calls whose dialogue names the product, {n_corr} carried a "
          f"label it contradicts = {n_corr / forced_total:.1%}")

    print("\n  corrections by scenario:")
    for scenario in sorted(FORCED):
        total = sum(1 for s in scen_b if s == scenario)
        print(f"    {scenario:16s} {corrected[scenario]:3d}/{total:3d}  -> "
              f"{FORCED[scenario]:9s} {'#' * corrected[scenario]}")

    print("\n  what the corpus said, against what the call is about:")
    for (was, now), count in pairs.most_common():
        print(f"    {was:9s} -> {now:9s}  {count:3d}")

    audio = {}
    if man_b and man_a:
        table = Counter()
        for i, (row_b, row_a) in enumerate(zip(rows_b, rows_a)):
            item = f"ASR-{i + 1:03d}"
            if item not in man_b or item not in man_a:
                continue
            table[(row_b["product"] != row_a["product"],
                   man_b[item]["sha256"] != man_a[item]["sha256"])] += 1
        agree = table[(False, False)] + table[(True, True)]
        total = sum(table.values())
        audio = {
            "label_unchanged_audio_identical": table[(False, False)],
            "label_changed_audio_changed": table[(True, True)],
            "label_unchanged_audio_changed": table[(False, True)],
            "label_changed_audio_identical": table[(True, False)],
            "agreement": f"{agree}/{total}",
        }
        print(f"\n  audio provenance                 identical      changed")
        print(f"    label unchanged        {table[(False, False)]:14d} {table[(False, True)]:12d}")
        print(f"    label changed          {table[(True, False)]:14d} {table[(True, True)]:12d}")
        print(f"    agreement: {agree}/{total} = {agree / total:.1%}")
        if table[(False, True)] or table[(True, False)]:
            print("    ^ a non-zero off-diagonal means the regeneration moved audio the "
                  "label fix did not ask it to move")
        renamed = sum(1 for i in range(n)
                      if (f"ASR-{i + 1:03d}" in man_b and f"ASR-{i + 1:03d}" in man_a
                          and man_b[f"ASR-{i + 1:03d}"]["filename"]
                          != man_a[f"ASR-{i + 1:03d}"]["filename"]))
        audio["files_renamed"] = renamed
        print(f"\n    {renamed} files were RENAMED by a duration shift. Re-rendering in "
              f"place would have left {n + renamed} files for {n} calls.")

    mix_b = Counter(r["product"] for r in rows_b)
    mix_a = Counter(r["product"] for r in rows_a)
    print("\n  product mix       before -> after")
    for product in sorted(set(mix_b) | set(mix_a)):
        print(f"    {product:9s} {mix_b[product] / n:.3f} -> {mix_a[product] / n:.3f}")

    if args.json:
        args.json.parent.mkdir(parents=True, exist_ok=True)
        args.json.write_text(json.dumps({
            "before": before.name, "after": after.name, "calls": n,
            "product_unchanged": unchanged, "corrected": n_corr, "rerolled": n_roll,
            "forced_scenario_calls": forced_total,
            "corrections_by_scenario": dict(corrected),
            "substitutions": {f"{a}->{b}": c for (a, b), c in pairs.items()},
            "audio": audio,
            "mix_before": {k: v / n for k, v in mix_b.items()},
            "mix_after": {k: v / n for k, v in mix_a.items()},
        }, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"\nwrote {args.json}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
