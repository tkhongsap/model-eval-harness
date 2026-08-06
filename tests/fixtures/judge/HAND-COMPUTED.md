# Hand-computed expectation for the judge aggregation — written before `judge.py`

**Written 2026-08-07, before `src/evalgen/judge.py` exists.** Per `CLAUDE.md`'s Build and
Verification Contract and the direct lesson of the retracted `keyword` metric
(`docs/eval-improvement-plan.md`): a new metric gets a hand-computed expectation authored
before its implementation, or it is a scorer built from the data it judges.

## What this fixture governs, and what it does not

The judge itself — an independent LLM asked to adjudicate a disputed label — cannot have
a "hand-computed expected verdict," because its output is a subjective judgment, not a
derivation. What CAN be hand-computed, and is the actual load-bearing logic in
`judge.py`, is everything downstream of the raw model text:

1. **Parsing.** A raw judge response is either valid JSON with an allowed `verdict` value,
   or it is not, and the two cases must be told apart deterministically.
2. **Aggregation.** Given a fixed list of parsed verdicts, the summary counts and rates
   are pure arithmetic.

This fixture fixes both **before** `judge.py` is written. If the implemented code
disagrees with the table below, the code is wrong until this document says otherwise —
matching the standing rule in `CLAUDE.md`: ask whether the fixture is wrong before
assuming the code is, then write down which one was and why.

## The verdict space

Four values, matching the shape of a real epistemic state rather than a binary:

| Verdict | Means |
|---|---|
| `ground_truth_correct` | The judge agrees the ground truth is right; the disputed answer is simply wrong. |
| `defensible_disagreement` | The judge thinks the disputed answer is ALSO reasonable given the transcript — a genuine ambiguity, not a fabrication. |
| `ground_truth_error` | The judge believes the ground truth itself is the mistake — the disputed answer is the better reading. This is the strong claim, one category above `defensible_disagreement`, and is what would have caught RET-11's ground-truth defect automatically had this existed then. |
| `unclear` | The judge cannot decide: transcript is ambiguous, or the model's own output could not be classified. |

**Two verdicts never come from the model and exist only as parser fallbacks:**
`unclear` is also the landing state for a response that fails to parse or names a verdict
outside this table — never a crash, matching `outcomes.classify`'s convention that a
failure is scored, not dropped. A field distinguishes the two paths: `parse_error: bool`.
A genuine `unclear` from the model has `parse_error=False`; a parser fallback has
`parse_error=True`. Both count toward the `unclear` bucket in the rate table, because
both mean "no usable adjudication," but `parse_error` stays visible in the raw record so
a run with a broken schema is distinguishable from a run full of genuinely ambiguous
items.

## The ten raw responses (constructed, not real model output)

| # | Raw response | Parses to |
|---|---|---|
| 1 | `{"verdict":"ground_truth_correct","cited_span":"...","rationale":"..."}` | `ground_truth_correct`, parse_error=False |
| 2 | `{"verdict":"ground_truth_correct","cited_span":"...","rationale":"..."}` | `ground_truth_correct`, parse_error=False |
| 3 | `{"verdict":"ground_truth_correct","cited_span":"...","rationale":"..."}` | `ground_truth_correct`, parse_error=False |
| 4 | `{"verdict":"ground_truth_correct","cited_span":"...","rationale":"..."}` | `ground_truth_correct`, parse_error=False |
| 5 | `{"verdict":"defensible_disagreement","cited_span":"...","rationale":"..."}` | `defensible_disagreement`, parse_error=False |
| 6 | `{"verdict":"defensible_disagreement","cited_span":"...","rationale":"..."}` | `defensible_disagreement`, parse_error=False |
| 7 | `{"verdict":"ground_truth_error","cited_span":"...","rationale":"..."}` | `ground_truth_error`, parse_error=False |
| 8 | `{"verdict":"unclear","cited_span":"","rationale":"..."}` | `unclear`, parse_error=False |
| 9 | `not json at all, the model refused to follow the schema` | `unclear`, parse_error=**True** (JSON decode failure) |
| 10 | `{"verdict":"probably_correct","cited_span":"...","rationale":"..."}` | `unclear`, parse_error=**True** (verdict outside the enum) |

## Hand-computed aggregate, n=10

| Bucket | count | rate |
|---|---:|---:|
| `ground_truth_correct` | 4 | 0.4 |
| `defensible_disagreement` | 2 | 0.2 |
| `ground_truth_error` | 1 | 0.1 |
| `unclear` (genuine + parse fallback) | 3 | 0.3 |
| **total** | **10** | **1.0** |
| of which `parse_error=True` | 2 | 0.2 of total |

Arithmetic: 4+2+1+3 = 10. 4+2+1+2(genuine unclear... wait 1 genuine unclear (#8) + 2 parse
fallbacks (#9, #10) = 3 unclear total. Rates are counts/10. Nothing here is derived from
running `judge.py` — it is derived from reading the ten rows above and counting by hand.

## What a passing implementation must do

`tests/test_judge.py` feeds these exact ten raw strings through `parse_judge_response`
and then `summarize_judgments`, and asserts the table above byte-for-byte: bucket
counts, `total == 10`, and `parse_error_count == 2`. It does not call any network client
— `parse_judge_response`/`summarize_judgments` are pure functions over strings and lists,
exactly like `evidence.asr_normalise` and `fabrication`'s counters are pure functions
over text, and both are already tested this way with no API key required.

## What this fixture does not cover, and must not be asked to

**This document says nothing about whether the judge model is any good.** A judge that
always says `ground_truth_correct` would pass every test above trivially while being
useless. That question — does the judge's actual adjudication carry signal — is answered
empirically in Experiment 6, against real disagreement items, and reported with the same
`RECONCILED: NO`-style honesty as everything else in this project: the judge is a
diagnostic opinion from a third model, not a ground truth, and its own agreement rate
with the harness's ground truth is itself just another number to read carefully, not a
verdict to trust blindly.
