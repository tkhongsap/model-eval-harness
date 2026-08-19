"""How reliably does E23's AHEAD verdict hold, given it clears its band by nothing?

    PYTHONPATH=src python scripts/e23_fragility.py --run out/runs/<stamp>-e21

WHY THIS EXISTS. E23's headline is `qwen-pipeline` AHEAD of `gemini-audio` on `call_result`.
Under the preregistered replicate rule that is net **+14** against a band of **+/-14**; under
the modal rule it is **+15** against **+/-15**. Both clear. Neither clears by anything.

A verdict that clears by zero margin is not wrong, but "AHEAD" and "AHEAD by the smallest
representable amount" are read identically off a table and are not the same claim. This
quantifies the difference three ways, none of which needs a single new model call:

  1. **Leave-one-out.** Drop each call in turn and re-run the exact paired test. The output
     is the number of individual calls whose removal alone flips the verdict. If that number
     is small and non-zero, the honest sentence is "the result depends on N specific calls".

  2. **Cluster bootstrap.** Resample the 136 CALLS with replacement -- calls, not rows,
     because a call contributes several product rows and resampling rows would treat
     correlated rows as independent evidence and shrink the interval that matters. Reports
     the fraction of resamples that still come out AHEAD.

  3. **The discordant-pair view.** The sign test only ever sees the d calls where the arms
     disagreed; both/neither are irrelevant to it. Reporting d alongside the net makes the
     real sample size visible -- 36 discordant calls, not 136.

WHAT THIS IS NOT. It is not a second significance test and its numbers do not replace the
preregistered verdict. `exact_band` remains the decision rule. This says how load-bearing
that decision is, which the band alone cannot.
"""

from __future__ import annotations

import argparse
import json
import random
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "src"))

from evalharness.compare import exact_band  # noqa: E402

DIMENSIONS = ("call_result", "reason", "product")
# Fixed so a rerun reproduces the same figure. A fragility number that moves between runs
# invites re-rolling until it reads well.
SEED = 20260820
RESAMPLES = 20000


def verdict_of(net: int, discordant: int, alpha: float = 1 / 64) -> str:
    band = exact_band(discordant, alpha_per_side=alpha)
    if band is None:
        return "UNDERPOWERED"
    if net > band:
        return "AHEAD"
    if net == band:
        # exact_band returns the smallest net whose tail is within alpha, so net == band
        # is inside the rejection region. Named separately because it is the whole point
        # of this script: it clears, and it clears by nothing.
        return "AHEAD"
    if -net >= band:
        return "BEHIND"
    return "INDISTINGUISHABLE"


def per_call(pairs: list[tuple[bool, bool]]) -> tuple[int, int, str, int]:
    """(net, discordant, verdict, band) for a list of (incumbent_ok, candidate_ok)."""
    inc_only = sum(1 for i, c in pairs if i and not c)
    cand_only = sum(1 for i, c in pairs if c and not i)
    discordant = inc_only + cand_only
    net = cand_only - inc_only
    band = exact_band(discordant, alpha_per_side=1 / 64)
    return net, discordant, verdict_of(net, discordant), (band if band is not None else -1)


def analyse(pairs: list[tuple[bool, bool]], label: str) -> dict:
    net, discordant, verdict, band = per_call(pairs)
    n = len(pairs)

    # 1. Leave-one-out.
    flippers = []
    for i in range(n):
        reduced = pairs[:i] + pairs[i + 1:]
        if per_call(reduced)[2] != verdict:
            flippers.append(i)

    # 2. Cluster bootstrap over calls.
    rng = random.Random(SEED)
    same = 0
    for _ in range(RESAMPLES):
        sample = [pairs[rng.randrange(n)] for _ in range(n)]
        if per_call(sample)[2] == verdict:
            same += 1

    result = {
        "dimension": label, "n_calls": n, "net": net, "discordant": discordant,
        "band": band, "verdict": verdict,
        "margin": net - band if band >= 0 else None,
        "calls_that_flip_it_alone": len(flippers),
        "bootstrap_agreement": same / RESAMPLES,
    }
    print(f"\n  {label}")
    print(f"    net {net:+d} against band +/-{band}  ->  {verdict}"
          f"   (margin {net - band:+d})" if band >= 0 else
          f"    net {net:+d}, d={discordant} -> {verdict}")
    print(f"    discordant calls (the real sample size): {discordant} of {n}")
    print(f"    single calls whose removal alone flips the verdict: {len(flippers)}")
    print(f"    cluster bootstrap ({RESAMPLES:,} resamples of calls, not rows): "
          f"{same / RESAMPLES:.1%} still {verdict}")
    return result


def main() -> int:
    ap = argparse.ArgumentParser(prog="e23_fragility")
    ap.add_argument("--run", type=Path, required=True)
    ap.add_argument("--pack", type=Path, default=REPO / "asr-eval-v2")
    ap.add_argument("--incumbent", default="gemini-audio")
    ap.add_argument("--candidate", default="qwen-pipeline")
    ap.add_argument("--policy", choices=("first", "modal"), default="first")
    ap.add_argument("--json", type=Path, default=None)
    args = ap.parse_args()

    # Reuse the scorer's own loading and per-call correctness, so this cannot drift from
    # the number it is characterising.
    import importlib.util
    spec = importlib.util.spec_from_file_location(
        "e23score", REPO / "scripts" / "experiment23_score.py")
    e23 = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(e23)

    truth, phones = e23.load_truth(args.pack)
    collapsed, _unstable, _failures = e23.collapse(args.run, args.policy)

    for arm in (args.incumbent, args.candidate):
        if arm not in collapsed:
            raise SystemExit(f"arm {arm!r} not in run; present: {sorted(collapsed)}")

    print(f"E23 fragility -- {args.candidate} vs {args.incumbent}, "
          f"replicate policy '{args.policy}'")
    print(f"seed {SEED}, {RESAMPLES:,} resamples")

    # Same construction the scorer's own section 2 uses, so this cannot drift from the
    # number it characterises.
    inc_gt, inc_pred = e23.build_pair(truth, phones, collapsed[args.incumbent])
    cand_gt, cand_pred = e23.build_pair(truth, phones, collapsed[args.candidate])

    out = []
    for dimension in DIMENSIONS:
        inc = e23.call_level_correct(inc_gt, inc_pred, dimension)
        cand = e23.call_level_correct(cand_gt, cand_pred, dimension)
        shared = sorted(set(inc) & set(cand))
        pairs = [(inc[c], cand[c]) for c in shared]
        out.append(analyse(pairs, dimension))

    print("\n  Read the leave-one-out number as the honest caveat: if it is non-zero, the")
    print("  verdict depends on that many specific calls and should be reported as such.")
    print("  The bootstrap is a cluster bootstrap over CALLS -- resampling rows would treat")
    print("  correlated product rows from one call as independent evidence.")

    if args.json:
        args.json.write_text(json.dumps({
            "run": args.run.name, "policy": args.policy, "seed": SEED,
            "resamples": RESAMPLES, "incumbent": args.incumbent,
            "candidate": args.candidate, "dimensions": out,
        }, indent=2), encoding="utf-8")
        print(f"\n  wrote {args.json}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
