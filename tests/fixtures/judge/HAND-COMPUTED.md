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

---

# Addendum (2026-08-09): hand-computed expectation for rule-text resolution — written before `resolve_rule_citations`

**Why this exists.** Experiment 6's flags were checked by hand on 2026-08-08 and three of
the four cross-validated "possible ground-truth error" flags turned out to be judge
errors with one shared root cause: `build_judge_prompt` sent the rule *citation*
(`customer reason: prompt.py:4372`) but never the rule *text*, so the judge re-derived
class boundaries from common sense — and this vocabulary is deliberately counterintuitive
exactly where it matters (indecision counts as `save` per prompt.py:4397; refusing to
give a reason IS `customer reason` per prompt.py:4372; `undefined` means out-of-scope,
not unresolved, per prompt.py:4399). The fix inlines the cited text. This section fixes
the resolver's arithmetic by hand before that code exists.

## Citation grammar, hand-derived from every `rule_*` value in retention_v3

Splitting every citation on `;` and `,`, trimming whitespace, every fragment in the pack
matches `<file>:<first>[-<last>]` with exactly four file basenames. Hand-parsed
expectations:

| Citation string | Parses to (file, first, last) |
|---|---|
| `prompt.py:4372` | `("prompt.py", 4372, 4372)` |
| `prompt.py:4390-4391` | `("prompt.py", 4390, 4391)` |
| `prompt.py:4360; prompt.py:4361` | `("prompt.py", 4360, 4360)`, `("prompt.py", 4361, 4361)` |
| `prompt.py:4399; main.py:1018` | `("prompt.py", 4399, 4399)`, `("main.py", 1018, 1018)` |
| `grain` | no parse — reported as unresolved, never dropped or guessed |

## File mapping, verified against the tracked tree on 2026-08-09

Rooted at `production-reference/` (the parent of both apps, because `prompt.txt`
belongs to the MNP sibling):

| Basename | Repo-relative path |
|---|---|
| `prompt.py` | `sentiment-batch-retention-main/src/prompt.py` |
| `main.py` | `sentiment-batch-retention-main/src/main.py` |
| `fact_checker.py` | `sentiment-batch-retention-main/src/modules/fact_checker.py` |
| `prompt.txt` | `sentiment-batch-mnp-develop/config/system_prompt/prompt.txt` |

## Resolution expectations, copied by hand with `sed -n 'Np'` on 2026-08-09

`prompt.py:4372` resolves to exactly this line (leading indentation preserved,
trailing newline stripped):

    ```
                    - ลูกค้าเลี่ยงที่จะบอกเหตุผล หรือ ให้เหตุผลแบบ hate speech / megative reason เช่น เกลียดทรู, เกลียดดีแทค, ไม่ชอบ CP
    ```

`prompt.py:4390-4391` resolves to exactly two lines:

    ```
        - `churn`
            - Client confirms leaving the brand (moving to a competitor).
    ```

`main.py:1018` resolves to exactly:

    ```
                    "enum": ["churn", "save", "unknown", "undefined"],
    ```

## Behavior the implementation must have, fixed now

1. An unparsable fragment (e.g. the `grain` / `merge` mechanics citations) is returned
   in an `unresolved` list, never silently dropped — matching `outcomes.classify`'s rule
   that a failure is scored, not vanished.
2. A `rule_source_root` that does not exist raises `JudgeError`: the caller explicitly
   asked for rule text, so a missing tree is a configuration error, not a silent
   pointer-only downgrade.
3. A parsable fragment whose file is missing from the mapping, or whose line range
   falls outside the file, lands in `unresolved` with the reason attached.
4. The report counts resolved and unresolved parts so a partially-broken resolution is
   visible in the output, not discoverable only by reading prompts.
5. With `rule_source_root=None` (the default), behavior is byte-identical to before —
   pointers only — so the parameter can never silently change an existing caller.

---

# Addendum (2026-08-09, second): label-union lookup, `#N` keys, and replicate arithmetic — written before the defect-4 fix

**Why.** The adversarial review of the first rule-text fix confirmed four defects. Three
are arithmetic enough to fix by hand here first: (defect 4) the prompt quoted rule text
for the ground-truth label only, because the lookup read the item's own
ground-truth-authored `rules` dict, so a competing label never got its rule; the lookup
also missed `#N` second-citation keys entirely; and (the placebo) a unit whose labels had
no rule key silently fell back to the byte-identical pointer prompt while the report said
`enabled: true, unresolved: 0`.

## `#N` keys, hand-read from RET-37 (raw JSONL, not the loader)

    rule_reason:save cost    -> prompt.py:4342
    rule_reason:save cost#2  -> prompt.py:4342; prompt.py:4345

Expectation: the entries for label `save cost` on RET-37 include BOTH keys' citations,
deduplicated at the fragment level — `prompt.py:4342` appears in both values and must be
quoted once, `prompt.py:4345` (the CRITICAL clause) must now be quoted where it was
previously invisible.

## Pack-level citation union, hand-enumerated for two labels (retention_v3, raw JSONL)

    reason / down sell not success -> {prompt.py:4375-4376, prompt.py:4375-4376; prompt.txt:43, prompt.py:4376}
    reason / post to pre           -> {prompt.py:4367, prompt.py:4367-4369, prompt.py:4368, prompt.py:4369}

Expectation: for a disagreement unit whose competing label is `post to pre` but whose own
item never asserts it, the quoted text is the union of those citations' lines,
deduplicated at the line level (4368 and 4369 sit inside 4367-4369 and must not repeat).
The union is built from the SAME testset the runs used — never from another pack.

## Placebo elimination

With rule text enabled, `rule_texts` is a list even when empty, and the prompt's
heading/stance ALWAYS differ from pointer mode — no unit may ever produce a
byte-identical request across the two modes again. A label with no rule anywhere gets a
visible `[no rule text on file for '<label>']` line, and the report counts
`units_without_rule_text`.

## Replicate arithmetic, fixed by hand

Per unit, `repeats` identical calls, each recorded with `replicate` 1..N. Unit-level
aggregation, majority over the recorded verdicts (invalid responses count as their
recorded `unclear`):

| Replicate verdicts | unit_verdict | flipped |
|---|---|---|
| E, E, E | ground_truth_error | no |
| E, E, C | ground_truth_error | yes |
| C, D, C | ground_truth_correct | yes |
| E, D, C | **no_majority** | yes |
| E, unclear, E | ground_truth_error | yes |
| C, C (repeats=2) | ground_truth_correct | no |
| E, C (repeats=2) | **no_majority** | yes |

(E = ground_truth_error, C = ground_truth_correct, D = defensible_disagreement.)

A tie is `no_majority`, never broken by picking — a tie is instability and reporting it
as a verdict would launder exactly the noise the placebo arm exposed. The `units` block
reports `{total, flagged_majority, flipped, no_majority}`; a unit counts as flagged ONLY
on a strict-majority `ground_truth_error`. Sanity: over the 7 rows above, total=7,
flagged_majority=3, flipped=5, no_majority=2.
