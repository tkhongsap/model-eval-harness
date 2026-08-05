"""Two passes of the same four runs, compared cell by cell.

An eval that is run once reports a number. An eval that is run twice reports whether the
number is a property of the models or a property of the afternoon. This script is the
second question, and it is deliberately unable to answer the first: it never picks a
pass, never averages them, and prints DISAGREE as a result rather than as an error.

What it compares, per arm:

  * The 22 scored rows x 3 dimensions grid, on REPLICATE 1 of each pass -- the same
    replicate the report's aggregate metrics use, and for the same reason
    (`metrics.outer_join` keys predictions by (call_id, phone, product), so pooling
    replicates silently keeps the last one). Two grids: the LABEL the arm emitted, and
    the VERDICT the scorer reached. Labels are the stricter test: two passes can reach
    the same verdict from two different wrong answers.
  * The mechanism verdicts, which read every replicate, so a within-pass flip that
    replicate 1 happens to miss still shows up here.
  * The headline net numbers, per prompt.

At row grain the `product` column can only disagree by a row going missing entirely,
because product is part of the merge key -- an arm that changes its mind about the
product moves the row rather than changing a value inside it. The call-grain product
comparison is printed separately for that reason, and it is the one that can see a
product change.

Usage:

    python scripts/consistency.py pass1.json pass2.json

where each JSON file is {"incumbent-base": "<run dir>", ...} for the four slots.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "src"))

from evalgen.cli import load_run, replicate_records  # noqa: E402
from evalgen.console import configure_stdout  # noqa: E402
from evalgen.report import mechanism_table, n_flip  # noqa: E402
from evalgen.testsets import load_testset  # noqa: E402
from evalharness.adapters.retention import load_csv  # noqa: E402
from evalharness.compare import _correct, disagreement  # noqa: E402

SLOTS = ("incumbent-base", "candidate-base", "incumbent-e1", "candidate-e1")
DIMENSIONS = ("call_result", "reason", "product")
PROMPTS = (("v9_16_base", "incumbent-base", "candidate-base"),
           ("v9_16_e1", "incumbent-e1", "candidate-e1"))


def load(directory: Path, gt, items):
    loaded = load_run(directory)
    per_replicate = replicate_records(loaded.result, items)
    return {
        "dir": directory,
        "meta": loaded.meta,
        "rep1": {r.key: r for r in per_replicate[0]},
        "per_replicate": per_replicate,
        "mechanisms": mechanism_table(gt, per_replicate, list(items)),
        "n_flip": n_flip(per_replicate),
    }


def label(rec, dimension: str) -> str:
    if rec is None:
        return "(no row)"
    if dimension == "call_result":
        return rec.call_result or "(empty)"
    if dimension == "reason":
        return ", ".join(sorted(rec.reasons)) or "(empty)"
    return rec.product or "(empty)"


def grid(arm, gt) -> tuple[dict, dict]:
    labels, verdicts = {}, {}
    for g in gt:
        rec = arm["rep1"].get(g.key)
        for dimension in DIMENSIONS:
            labels[(g.key, dimension)] = label(rec, dimension)
            verdicts[(g.key, dimension)] = _correct(g, rec, dimension)
    return labels, verdicts


def call_grain_products(arm) -> dict:
    out: dict = {}
    for key, rec in arm["rep1"].items():
        out.setdefault((key[0], key[1]), set())
        if rec.product is not None:
            out[(key[0], key[1])].add(rec.product)
    return {k: frozenset(v) for k, v in out.items()}


def main(argv: list[str]) -> int:
    configure_stdout()
    pass1 = {k: Path(v) for k, v in json.loads(Path(argv[0]).read_text("utf-8")).items()}
    pass2 = {k: Path(v) for k, v in json.loads(Path(argv[1]).read_text("utf-8")).items()}

    meta = json.loads((pass1["incumbent-base"] / "run.json").read_text("utf-8"))
    testset = load_testset(Path(meta["testset_path"]))
    gt = load_csv(Path(meta["gt_path"]))
    items = testset.items

    a = {slot: load(pass1[slot], gt, items) for slot in SLOTS}
    b = {slot: load(pass2[slot], gt, items) for slot in SLOTS}

    print("=" * 78)
    print("CONSISTENCY: PASS 1 vs PASS 2, same settings, same pinned backends")
    print("=" * 78)
    print(f"scored rows {len(gt)}  x  {len(DIMENSIONS)} dimensions "
          f"= {len(gt) * len(DIMENSIONS)} cells per arm, on replicate 1")
    print()

    verdict_lines = []
    all_identical = True

    for slot in SLOTS:
        arm_a, arm_b = a[slot], b[slot]
        la, va = grid(arm_a, gt)
        lb, vb = grid(arm_b, gt)
        total = len(la)
        label_same = sum(1 for k in la if la[k] == lb[k])
        verdict_same = sum(1 for k in va if va[k] == vb[k])
        differing = [k for k in la if la[k] != lb[k]]

        mech_a = [(m.mechanism, m.verdict) for m in arm_a["mechanisms"]]
        mech_b = [(m.mechanism, m.verdict) for m in arm_b["mechanisms"]]
        mech_same = mech_a == mech_b

        # The verdict letter is not the whole verdict. A group already FAIL because of
        # one item stays FAIL when a second item joins it, so comparing only the letters
        # reports "identical" for a mechanism whose membership changed. MEASURED: that is
        # exactly what candidate-e1 does between these two passes.
        fail_a = {m.mechanism: set(m.failing_items) for m in arm_a["mechanisms"]}
        fail_b = {m.mechanism: set(m.failing_items) for m in arm_b["mechanisms"]}
        members_same = fail_a == fail_b
        item_moves = sorted(
            {i for name in fail_a for i in fail_a[name] ^ fail_b.get(name, set())}
        )

        prod_a, prod_b = call_grain_products(arm_a), call_grain_products(arm_b)
        prod_keys = set(prod_a) | set(prod_b)
        prod_same = sum(1 for k in prod_keys if prod_a.get(k) == prod_b.get(k))

        if label_same != total or not mech_same or not members_same:
            all_identical = False

        print("-" * 78)
        print(f"{slot}   {arm_a['meta']['model_requested']}  prompt={arm_a['meta']['prompt_id']}")
        print(f"  pass 1  {arm_a['dir'].name}")
        print(f"  pass 2  {arm_b['dir'].name}")
        print(f"  LABELS agreeing    {label_same}/{total} "
              f"({label_same / total:.1%})")
        print(f"  VERDICTS agreeing  {verdict_same}/{total} "
              f"({verdict_same / total:.1%})")
        print(f"  product, call grain {prod_same}/{len(prod_keys)} calls identical")
        print(f"  outcome counts     pass1={arm_a['meta']['outcome_counts']}  "
              f"pass2={arm_b['meta']['outcome_counts']}")
        print(f"  provider served    pass1={arm_a['meta'].get('observed_providers')}  "
              f"pass2={arm_b['meta'].get('observed_providers')}")
        print(f"  N_flip within pass pass1={arm_a['n_flip']}  pass2={arm_b['n_flip']}")
        print(f"  MECHANISM VERDICTS {'IDENTICAL' if mech_same else 'DIFFER'}")
        if not mech_same:
            for (name, v1), (_, v2) in zip(mech_a, mech_b):
                flag = "" if v1 == v2 else "   <-- CHANGED"
                print(f"      {name:<18} pass1={v1:<6} pass2={v2:<6}{flag}")
        print(f"  FAILING ITEM SETS  {'IDENTICAL' if members_same else 'DIFFER'}"
              + ("" if members_same else f"  (moved: {', '.join(item_moves)})"))
        if not members_same:
            for name in fail_a:
                if fail_a[name] != fail_b.get(name, set()):
                    print(f"      {name:<18} pass1={sorted(fail_a[name]) or '[]'}  "
                          f"pass2={sorted(fail_b.get(name, set())) or '[]'}")
            print("      A group already FAIL stays FAIL when another item joins it, so "
                  "the verdict letter can")
            print("      report IDENTICAL while the set of broken items changed. This "
                  "line is the one to read.")
        if differing:
            print(f"  CELLS THAT MOVED ({len(differing)}):")
            for key, dimension in sorted(differing, key=lambda k: (k[0][0], k[1])):
                g = next(r for r in gt if r.key == key)
                print(f"      call {key[0]} product={key[2]!r}  {dimension}")
                print(f"        ground truth  {label(g, dimension)}")
                print(f"        pass 1        {la[(key, dimension)]}")
                print(f"        pass 2        {lb[(key, dimension)]}")
        verdict_lines.append(
            f"{slot}: labels {label_same}/{total}, verdicts {verdict_same}/{total}, "
            f"mechanism letters {'identical' if mech_same else 'DIFFER'}, "
            f"failing items {'identical' if members_same else 'DIFFER (' + ', '.join(item_moves) + ')'}"
        )

    print()
    print("-" * 78)
    print("HEADLINE NET NUMBERS (candidate-only-right minus incumbent-only-right)")
    print("-" * 78)
    print(f"  {'prompt':<12} {'dimension':<12} {'pass 1':>28} {'pass 2':>28}")
    headline_same = True
    for prompt, inc, cand in PROMPTS:
        for dimension in DIMENSIONS:
            d1 = disagreement(gt, a[inc]["per_replicate"][0], a[cand]["per_replicate"][0],
                              dimension)
            d2 = disagreement(gt, b[inc]["per_replicate"][0], b[cand]["per_replicate"][0],
                              dimension)
            f1 = (f"br={d1.both_right} bw={d1.both_wrong} i={d1.incumbent_only_right} "
                  f"c={d1.candidate_only_right} net="
                  f"{d1.candidate_only_right - d1.incumbent_only_right:+d}")
            f2 = (f"br={d2.both_right} bw={d2.both_wrong} i={d2.incumbent_only_right} "
                  f"c={d2.candidate_only_right} net="
                  f"{d2.candidate_only_right - d2.incumbent_only_right:+d}")
            same = f1 == f2
            headline_same &= same
            print(f"  {prompt:<12} {dimension:<12} {f1:>28} {f2:>28}"
                  + ("" if same else "   <-- CHANGED"))

    print()
    print("=" * 78)
    print("VERDICT")
    print("=" * 78)
    for line in verdict_lines:
        print(f"  {line}")
    print(f"  headline net numbers {'IDENTICAL' if headline_same else 'CHANGED'}")
    print(f"  overall: {'REPRODUCED' if all_identical and headline_same else 'DID NOT REPRODUCE EXACTLY'}")
    print()
    print("  A disagreement here is the result, not a defect to be resolved by picking")
    print("  the better pass. The spread between passes is the error bar this pack has;")
    print("  it does not have another one.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
