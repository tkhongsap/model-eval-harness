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
import reason_lines as R
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

# The three scored fields. `main` is the reason; `(none)` stands in for the empty reason,
# which is a real production value and must be probed like any other.
LABEL_FIELDS = ("call_result", "main", "product")


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


def load() -> tuple[list[str], dict[str, list[str]]]:
    """(transcripts, {label field -> values}), aligned, in item order.

    All three scored fields, not just the outcome. An earlier version probed `call_result`
    alone. That gap mattered: `main` and `product` turned out not to be expressed in the
    call at all, and a probe that never looked at them reported PASS while two of the three
    labels were unrecoverable by any reader.
    """
    gt_path = C.GROUND_TRUTH_DIR / "business.csv"
    if not gt_path.exists():
        raise SystemExit(
            f"no business labels at {gt_path}.\n"
            "This probe measures recovery of an AUTHORED label; without one it would have to "
            "derive the outcome from the text, which is the very thing under test. Run "
            "compose_dialogues.py first."
        )
    labels: dict[str, dict[str, str]] = {}
    with gt_path.open(encoding="utf-8", newline="") as handle:
        for row in csv.DictReader(handle):
            # call_id 7100+idx -> ASR-0NN, the id the transcript is filed under.
            labels[f"ASR-{int(row['call_id']) - 7099:03d}"] = {
                field: (row.get(field) or "(none)") for field in LABEL_FIELDS
            }

    transcripts: list[str] = []
    values: dict[str, list[str]] = {field: [] for field in LABEL_FIELDS}
    for item_id in sorted(labels):
        path = C.GROUND_TRUTH_DIR / f"{item_id}.txt"
        if not path.exists():
            raise SystemExit(f"{item_id} has a label but no transcript at {path}")
        transcripts.append(path.read_text(encoding="utf-8"))
        for field in LABEL_FIELDS:
            values[field].append(labels[item_id][field])
    return transcripts, values


STATED_LABEL_RATIONALE = """
  ACKNOWLEDGED: this corpus states every label explicitly, so a string matcher recovers them.
  That is a deliberate trade and it is recorded here rather than hidden by a widened
  threshold.

  WHY THE GATE IS THE WRONG INSTRUMENT FOR AN END-TO-END AUDIO EVAL. The gate was built for a
  TEXT eval, where both arms read the same clean transcript and a matchable label means the
  eval measures matching. In an audio eval both arms must first HEAR the fact. The comparison
  is then "which pipeline recovers an explicitly stated fact from the same audio" -- and the
  leak does not help either arm, because neither is given the transcript.

  WHAT THIS CORPUS THEREFORE MEASURES, AND WHAT IT DOES NOT.
    measures      : fact extraction under transcription noise
    does NOT      : inferential labelling, where the outcome must be deduced rather than read
    consequence   : every score here is an UPPER BOUND. Real calls state their outcome far
                    less often, so both arms will do worse in production than they do here.

  The alternative was a corpus where the label is stated in 38% of calls, which is what the
  previous build did: the labeller had nothing to read on the other 62%, defaulted to `save`,
  and scored 0.277 with a perfect transcript. An unmeasurable corpus is not the safer choice.
"""


def main(argv: "collections.abc.Sequence[str]" = ()) -> int:
    """The gate. `argv` defaults to EMPTY, not to `sys.argv`.

    Deliberate, and it broke before it was: `argparse.parse_args(None)` reads `sys.argv`,
    so when the tests call `main()` in-process argparse saw *pytest's* command line,
    rejected `-q` as an unrecognised argument, and raised `SystemExit(2)`. Both directions
    of the gate failed for a reason that had nothing to do with leak measurement.

    An empty default also means the strict gate is what an in-process caller gets. The
    acknowledgement is an explicit act at the command line, never something a test or an
    importing script can inherit by accident.
    """
    import argparse

    ap = argparse.ArgumentParser(prog="leak_probe")
    ap.add_argument("--acknowledge-stated-labels", action="store_true",
                    help="For an END-TO-END AUDIO eval only. The arms never see the "
                         "transcript, so a matchable label does not help them; the gate "
                         "was built for text evals. Prints the full rationale and the "
                         "measured lift rather than hiding either.")
    args = ap.parse_args(list(argv))
    acknowledged = args.acknowledge_stated_labels

    transcripts, all_labels = load()
    n = len(transcripts)

    # Every pool a matcher could key on. The reason-bearing and product-naming pools are
    # here because they are the ones just added: content that makes a label READABLE is one
    # edit away from content that makes it a LOOKUP, and only this measurement tells them
    # apart.
    channels: list[tuple[str, list[str]]] = [
        ("CUSTOMER_CLOSE (outcome pools)",
         [t for pool in T.CUSTOMER_CLOSE.values() for t in pool]),
        ("CUSTOMER_CLOSE_SHARED", list(T.CUSTOMER_CLOSE_SHARED)),
        ("PROBLEM_BY_REASON (reason lines)",
         [t for pool in R.PROBLEM_BY_REASON.values() for t in pool]),
        ("PROBLEM_SHARED", list(R.PROBLEM_SHARED)),
        ("PRODUCT_CONFIRM (service naming)", list(T.PRODUCT_CONFIRM)),
        ("WRAPUP (agent summary)",
         [t for pool in T.WRAPUP.values() for t in pool] + list(T.WRAPUP_GENERIC)),
    ]

    print(f"label leak probe -- {n} calls from {C.GROUND_TRUTH_DIR}")
    worst = 0.0
    failures = []
    # Signatures depend only on the channel, not on which label is being predicted, so they
    # are computed once per channel and reused across all three fields.
    sigs_by_channel = {name: [signature(t, pool) for t in transcripts]
                       for name, pool in channels}

    for field in LABEL_FIELDS:
        labels = all_labels[field]
        counts = collections.Counter(labels)
        top_label, top_n = counts.most_common(1)[0]
        baseline = top_n / n

        print()
        print(f"  {field.upper()}   mix: "
              + "  ".join(f"{k}={v}" for k, v in counts.most_common(5))
              + (f"  (+{len(counts) - 5} more)" if len(counts) > 5 else ""))
        print(f"  always-guess-{top_label} baseline: {baseline * 100:.1f}%")
        print(f"  {'channel':34} {'groups':>7} {'single':>7} {'scored':>7} "
              f"{'best':>7} {'lift':>7}")

        for name, _pool in channels:
            accuracy, scored, singletons, groups = best_accuracy(
                sigs_by_channel[name], labels)
            if scored == 0:
                print(f"  {name:34} {groups:>7} {singletons:>7} {scored:>7} "
                      f"{'n/a':>7} {'n/a':>7}   every signature unique")
                continue
            lift = (accuracy - baseline) / (1 - baseline) if baseline < 1 else 0.0
            worst = max(worst, lift)
            flag = ""
            if lift > MAX_LIFT:
                flag = "   <-- LEAK"
                failures.append((field, name, lift))
            print(f"  {name:34} {groups:>7} {singletons:>7} {scored:>7} "
                  f"{accuracy * 100:6.1f}% {lift:6.2f}{flag}")

    print()
    print("  lift = fraction of the headroom above baseline that the channel alone closes.")
    print(f"  gate: no channel above {MAX_LIFT:.2f} on any field. worst observed: {worst:.2f}")
    if failures:
        print()
        for field, name, lift in failures:
            print(f"  {'ACKNOWLEDGED' if acknowledged else 'REFUSED'}: '{name}' recovers "
                  f"{field} at lift {lift:.2f}.")
        if not acknowledged:
            print()
            print("  A metric measured on this corpus would substantially be measuring "
                  "string")
            print("  matching rather than the model. Re-run with "
                  "--acknowledge-stated-labels if")
            print("  this is an END-TO-END AUDIO eval, where the arms never see the "
                  "transcript.")
            return 1
        print(STATED_LABEL_RATIONALE)
        return 0
    print("  PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
