"""How much of the business outcome is recoverable without understanding the call?

    python leak_probe.py                    # measure, print, exit 0/1
    ASR_EVAL_ROOT=/path/to/pack python leak_probe.py

WHAT THIS GUARDS. The primary metric for the migration decision is end-to-end business
accuracy: does the pipeline emit the right save/churn/unknown/undefined? That number is only
worth having if arriving at it requires reading the call. If the generator that wrote the
corpus also planted a sentence that announces the answer, then every arm scores near 100%,
every arm agrees with every other, and the eval reports a property of the template file.

That is not hypothetical here. Measured on the twenty-call set as it stood on 2026-08-18:
`CUSTOMER_ACCEPT` (5 lines) and `CUSTOMER_DECLINE` (3 lines) were disjoint, fired on every
call, and a plain substring match over those eight sentences recovered the outcome on
**20 of 20 calls**. Experiment 21's headline -- "no arm ever disagreed about the outcome
across 351 label calls" -- is that leak, not a finding about the models.

HOW IT MEASURES. For each channel (a named pool of templates) it computes the *best possible*
accuracy of a classifier that sees only which of that pool's lines appear in the transcript:
group the calls by their signature, and give every call in a group the group's majority
outcome. That is an upper bound on what any string matcher could achieve, which is what makes
it a fair gate -- it cannot be beaten by a cleverer matcher.

The number to read is not the raw accuracy, it is **lift over the majority baseline**. A
channel that scores 53% when always-guess-churn also scores 53% has told you nothing.

TWO WAYS TO READ IT WRONG, both guarded:

  * **A signature seen once is not evidence.** A group of size 1 is always "predicted"
    perfectly, so a channel with 138 distinct signatures over 138 calls scores 100% while
    carrying no generalisable signal at all. Singleton groups are reported separately and
    the headline figure is computed with them excluded.
  * **Some leak is legitimate.** A customer who says "yes, keep my service" HAS resolved the
    call, and a corpus where nobody ever states their decision would be unrealistic. The
    gate is not zero lift; it is that the closing line alone must not be sufficient.
"""

from __future__ import annotations

import collections
import csv
import re
import sys
from pathlib import Path

import asr_common as C
import thai_corpus as T

# A channel is only a leak if a matcher could actually use it. Fragments shorter than this
# are function words that appear everywhere and would match every call.
MIN_FRAGMENT = 8

# The gate. Lift is (accuracy - baseline) / (1 - baseline): the fraction of the headroom
# above always-guess-majority that this channel alone closes. 1.0 means the channel fully
# determines the outcome; 0.0 means it is worthless as a predictor.
#
# 0.55 is chosen, not derived. The closing exchange of a real retention call does carry real
# information about the outcome, so demanding near-zero would mean writing dialogue no human
# would recognise. What it rules out is the state this corpus was in, where the channel
# closed 100% of the headroom and the label was a lookup.
MAX_LIFT = 0.55


def fragments(template: str) -> list[str]:
    """The literal parts of a template, with the {slots} removed.

    Matching on slot-free fragments is what makes this robust: two calls fill `{amount}`
    differently but share every surrounding word, and a matcher looking for the leak would
    do exactly this.
    """
    return [f.strip() for f in re.split(r"\{[^}]*\}", template) if len(f.strip()) >= MIN_FRAGMENT]


def signature(text: str, pool: list[str]) -> frozenset[int]:
    """Which templates from `pool` appear in `text`, as a set of pool indices."""
    hits = set()
    for i, template in enumerate(pool):
        parts = fragments(template)
        if parts and all(part in text for part in parts):
            hits.add(i)
    return frozenset(hits)


def best_accuracy(signatures: list[frozenset[int]], labels: list[str]):
    """Upper bound on any classifier that sees only the signature.

    Returns (accuracy_excluding_singletons, n_scored, n_singleton, n_groups).
    """
    groups: dict[frozenset[int], list[str]] = collections.defaultdict(list)
    for sig, label in zip(signatures, labels):
        groups[sig].append(label)
    scored = correct = singletons = 0
    for members in groups.values():
        if len(members) == 1:
            singletons += 1
            continue
        scored += len(members)
        correct += collections.Counter(members).most_common(1)[0][1]
    accuracy = correct / scored if scored else float("nan")
    return accuracy, scored, singletons, len(groups)


def load() -> tuple[list[str], list[str]]:
    """(transcripts, call_result labels), aligned, in item order."""
    gt_path = C.GROUND_TRUTH_DIR / "business.csv"
    if not gt_path.exists():
        raise SystemExit(
            f"no business labels at {gt_path}.\n"
            "This probe measures recovery of an AUTHORED label; without one it would have to "
            "derive the outcome from the text, which is the very thing under test. Run "
            "compose_dialogues.py first."
        )
    labels: dict[str, str] = {}
    with gt_path.open(encoding="utf-8", newline="") as handle:
        for row in csv.DictReader(handle):
            # call_id 7100+idx -> ASR-0NN, the id the transcript is filed under.
            labels[f"ASR-{int(row['call_id']) - 7099:03d}"] = row["call_result"]

    transcripts, outcomes = [], []
    for item_id in sorted(labels):
        path = C.GROUND_TRUTH_DIR / f"{item_id}.txt"
        if not path.exists():
            raise SystemExit(f"{item_id} has a label but no transcript at {path}")
        transcripts.append(path.read_text(encoding="utf-8"))
        outcomes.append(labels[item_id])
    return transcripts, outcomes


def main() -> int:
    transcripts, labels = load()
    n = len(labels)
    counts = collections.Counter(labels)
    baseline = counts.most_common(1)[0][1] / n

    # Every pool that could plausibly announce the outcome. CUSTOMER_CLOSE is the one that
    # was leaking; the others are here because a fix that only moves the leak is not a fix.
    channels: list[tuple[str, list[str]]] = [
        ("CUSTOMER_CLOSE (all outcome pools)",
         [t for pool in T.CUSTOMER_CLOSE.values() for t in pool]),
        ("CUSTOMER_CLOSE_SHARED", list(T.CUSTOMER_CLOSE_SHARED)),
        ("PROBLEM (scenario opener)",
         [t for pool in T.PROBLEM.values() for t in pool]),
        ("WRAPUP (agent summary)",
         [t for pool in T.WRAPUP.values() for t in pool] + list(T.WRAPUP_GENERIC)),
    ]

    print(f"outcome leak probe -- {n} calls from {C.GROUND_TRUTH_DIR}")
    print(f"label mix: " + "  ".join(f"{k}={v}" for k, v in counts.most_common()))
    print(f"always-guess-{counts.most_common(1)[0][0]} baseline: {baseline * 100:.1f}%")
    print()
    print(f"  {'channel':38} {'groups':>7} {'single':>7} {'scored':>7} {'best':>7} {'lift':>7}")

    worst = 0.0
    failures = []
    for name, pool in channels:
        sigs = [signature(text, pool) for text in transcripts]
        accuracy, scored, singletons, groups = best_accuracy(sigs, labels)
        if scored == 0:
            print(f"  {name:38} {groups:>7} {singletons:>7} {scored:>7} "
                  f"{'n/a':>7} {'n/a':>7}   every signature unique -- no evidence")
            continue
        lift = (accuracy - baseline) / (1 - baseline) if baseline < 1 else 0.0
        worst = max(worst, lift)
        flag = ""
        if lift > MAX_LIFT:
            flag = "   <-- LEAK"
            failures.append((name, lift))
        print(f"  {name:38} {groups:>7} {singletons:>7} {scored:>7} "
              f"{accuracy * 100:6.1f}% {lift:6.2f}{flag}")

    print()
    print(f"  lift = fraction of the headroom above baseline that the channel alone closes.")
    print(f"  gate: no channel above {MAX_LIFT:.2f}. worst observed: {worst:.2f}")
    if failures:
        print()
        for name, lift in failures:
            print(f"  REFUSED: '{name}' recovers the outcome at lift {lift:.2f}. Business "
                  f"accuracy measured on this corpus would substantially be measuring "
                  f"string matching.")
        return 1
    print("  PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
