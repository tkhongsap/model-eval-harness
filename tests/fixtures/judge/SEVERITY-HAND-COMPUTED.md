# Error severity: the expectation, computed by hand before `src/evalgen/severity.py` exists

**Written 2026-08-09.** Same discipline as `tests/fixtures/WORKED-COMPUTATION.md` and this
directory's `HAND-COMPUTED.md`: the arithmetic is derived on paper first, so that when a
test fails there are two independent derivations to compare and the code is not the only
witness to its own correctness.

Nothing in this file was produced by running `severity.py`. The real label triples in
section 3 were read out of `out/reports/judge-e6c-gemini-vs-27b.json`, which is an output
of the **scorer and the existing judge**, not of the classifier being specified here.

---

## 1. The decision table, in evaluation order

`classify(truth, answer, record)` returns exactly one category. Order matters and is part
of the specification, not an implementation detail.

| # | Condition | Category | Why it sits at this position |
|---|---|---|---|
| 1 | prediction record is absent | `missing_output` | There is no answer to classify. Name shared with `compare._record_error_type` rather than invented alongside it. |
| 2 | `record.parse_ok` is False | `invalid_output` | The output never parsed, so its label content is not a classification claim. A failure skeleton can carry values copied from ground truth to keep its row shape; those are not an answer. Checked **before** the vocabulary so a parse failure is never reported as a fabrication. |
| 3 | `answer - vocabulary` is non-empty | `fabricated_class` | **The hard cap.** An answer naming a class outside the declared label space can never grade milder, whatever else is true of it — including when it contains every correct label. Placed above the subset tests precisely so `{truth} ∪ {invented}` cannot be laundered as `over_labelling`. |
| 4 | `truth` is empty, `answer` is not | `unsupported_claim` | Every label is unsupported; there is no correct core to have over-extended. Sub-reason recorded: `empty_ground_truth` (the row exists and carries no label) or `no_ground_truth_row` (orphan). |
| 5 | `answer` is empty, `truth` is not | `no_answer` | Asserted nothing. Not a wrong class. |
| 6 | `truth ⊊ answer` | `over_labelling` | Every true label found, plus extras. |
| 7 | `answer ⊊ truth` | `under_labelling` | Missed labels, invented none. |
| 8 | otherwise | `substitution` | Both `truth - answer` and `answer - truth` are non-empty. **This is the only category a model is asked about.** |

Rows 1-7 need no judge. Row 8 is resolved to `near_family`, `cross_family` or
`unclear_family` by the replicated judge described in section 4.

**Vocabulary.** `evalharness.labelspaces.REASON_CLASSES` (11) and `CALL_RESULT_CLASSES`
(4), compared after the normalisation `records.parse_reasons`/`norm_text` already applies
(strip + lowercase). Out-of-vocabulary labels are reachable: `parse_reasons` accepts any
text, and this repository has recorded providers that do not honour a `strict: True`
schema. If a run reports zero `fabricated_class`, that is evidence constrained decoding
held, and is to be reported as such — never as proof the check is unnecessary.

### The one ordering claim

**Mis-classification** (`near_family` < `cross_family` < `fabricated_class`) is more severe
than **mis-scoping** (`over_labelling`, `under_labelling`), because mis-scoping mis-counts a
class the call supports while mis-classification asserts a class it does not.

**No order is asserted between `over_labelling` and `under_labelling`.** Which is worse
depends on what the monthly report does with the category, which is a question for the app
owners. The report prints them side by side and never sums them into one severity number.

---

## 2. Constructed cases: one per branch, worked by hand

Reason dimension throughout. `V` = `REASON_CLASSES`.

| # | truth | answer | record state | walk | expected |
|---|---|---|---|---|---|
| c1 | `{save cost}` | — | no record | row 1 | `missing_output` |
| c2 | `{save cost}` | `{save cost}` | `parse_ok=False` | row 2 (not row 6; the sets match but the output is not an answer) | `invalid_output` |
| c3 | `{save cost}` | `{save cost, กลัวแพง}` | ok | row 3: `{กลัวแพง} ⊄ V` | `fabricated_class` |
| c4 | `{save cost}` | `{save cost, other}` | ok | rows 1-5 no; row 6 yes | `over_labelling` |
| c5 | `∅` | `{other}` | ok, GT row exists | row 4, sub-reason `empty_ground_truth` | `unsupported_claim` |
| c6 | `∅` | `{other}` | ok, no GT row | row 4, sub-reason `no_ground_truth_row` | `unsupported_claim` |
| c7 | `{network, save cost}` | `∅` | ok | row 5 | `no_answer` |
| c8 | `{network, save cost}` | `{network}` | ok | row 7 | `under_labelling` |
| c9 | `{network}` | `{save cost}` | ok | row 8: `truth-answer={network}`, `answer-truth={save cost}` | `substitution` |
| c10 | `∅` | `{กลัวแพง}` | ok | row 3 beats row 4 | `fabricated_class` |
| c11 | `{save cost}` | `{save cost, other, กลัวแพง}` | ok | row 3 beats row 6 — **the hard cap's load-bearing case** | `fabricated_class` |

c11 is the case that fails if the ordering is ever "simplified": every true label is
present and one extra is legal, so a subset-first implementation returns `over_labelling`
and the invented class disappears from the report.

---

## 3. Real units, classified by hand

Read from `out/reports/judge-e6c-gemini-vs-27b.json` (replicate 1, `reason`). Chosen for
branch coverage, **not sampled** — the rates at the end of this section describe these
eleven units and nothing else, and must never be quoted as the pack's profile.

`INC` = `google/gemini-2.5-flash`, `CAND` = `qwen/qwen3.6-27b`, as recorded in that report.

| item | arm | truth | answer | hand walk | expected |
|---|---|---|---|---|---|
| RET-02 | INC | `{promotion related}` | `{down sell not success, other, promotion related}` | truth ⊊ answer | `over_labelling` |
| RET-02 | CAND | `{promotion related}` | `{promotion related, save cost}` | truth ⊊ answer | `over_labelling` |
| RET-06 | INC | `{post to pre}` | `{dissatisfied service, post to pre, save cost}` | truth ⊊ answer | `over_labelling` |
| RET-06 | CAND | `{post to pre}` | `{post to pre, save cost}` | truth ⊊ answer | `over_labelling` |
| RET-09 | INC | `{promotion related}` | `{device promotion related, promotion related}` | truth ⊊ answer | `over_labelling` |
| RET-09 | CAND | — | — | scorer says correct; never reaches the classifier | — |
| RET-13 | INC | `{down sell not success, promotion related}` | `{dissatisfied service, promotion related, save cost}` | `truth-answer={down sell not success}`, `answer-truth={dissatisfied service, save cost}` | `substitution` |
| RET-13 | CAND | `{down sell not success, promotion related}` | `{promotion related, save cost}` | `truth-answer={down sell not success}`, `answer-truth={save cost}` | `substitution` |
| RET-14 | INC | `{network, promotion related}` | `{network}` | answer ⊊ truth | `under_labelling` |
| RET-14 | CAND | `{network, promotion related}` | `{promotion related}` | answer ⊊ truth | `under_labelling` |
| RET-19 | INC | `∅` | `{other}` | truth empty, GT row present | `unsupported_claim` (`empty_ground_truth`) |
| RET-19 | CAND | — | — | answer also empty; correct | — |
| RET-29 | INC | — | — | correct | — |
| RET-29 | CAND | `{promotion related}` | `{promotion related, save cost}` | truth ⊊ answer | `over_labelling` |

**RET-09 INC is the case that shows why the taxonomy is not just "how different are the
labels".** The extra label `device promotion related` is the *neighbouring* class to
`promotion related` — exactly the near-family shape — but it was **added**, not
substituted. Set arithmetic files it as `over_labelling` and **no model is called**. A
design that judged similarity instead of set relations would spend a call here and report
a near-miss on an answer that never displaced anything.

### Hand totals for these eleven classified units

Counted by tallying the table above, twice, independently:

| Category | INC | CAND | total |
|---|---:|---:|---:|
| `over_labelling` | 3 (02, 06, 09) | 3 (02, 06, 29) | 6 |
| `under_labelling` | 1 (14) | 1 (14) | 2 |
| `substitution` | 1 (13) | 1 (13) | 2 |
| `unsupported_claim` | 1 (19) | 0 | 1 |
| **wrong units** | **6** | **5** | **11** |

Rates, denominator = that arm's wrong units (**never** all units — a rate over all units
would fall whenever an arm got more right, which is not what "how it fails" means):

- INC `over_labelling` rate = 3/6 = **50.0%**
- CAND `over_labelling` rate = 3/5 = **60.0%**
- judged remainder over these eleven = 2 units = **18.2%**

---

## 4. The judged remainder: what is asked, and how replicates collapse

Only `substitution` units are sent. One call per unit per replicate, blinded by
construction (the arm is never named — there is nothing to blind, since only one answer is
under review).

**The question:** are the labels the answer substituted *in* the neighbours of the labels
it substituted *out* — separated by a specific clause in the quoted production rule — or
do they come from a different part of the rule set?

**Worst-of, stated in the prompt.** A unit can substitute in several labels (RET-13 INC
substitutes in two). If **any** substituted-in label fails the near test, the unit is
`cross_family`. One call per unit, and the conservative direction is the one that cannot
flatter an arm.

**Two evidence gates, both required for a decisive answer:**

1. `cited_span` occurs byte-for-byte in the transcript — the gate `judge.py` already
   applies.
2. `cited_rule_line` occurs byte-for-byte **in the rule text this prompt quoted**. A judge
   that paraphrases a rule it was handed has not demonstrated the boundary it claims.

Failing either gate is not a crash and not a dropped row: the response lands in
`unclear_family` with the validation error recorded, matching `outcomes.classify`.

**A unit with no rule text is never sent.** Gate 2 cannot be satisfied when no production
rule resolved for any label in play, so the call could only ever return `unclear_family`
at a cost. Those units are classified `unclear_family` with sub-reason `no_rule_text`
before any call is made, and counted in the report so the gap is visible rather than
absorbed. This is the same deterministic-first instinct as row 3 of the decision table:
never pay a model to answer a question the inputs already settle.

### Majority table (the same strict rule as `judge._unit_aggregation`, re-derived here)

Strict majority: `top_count * 2 > len(replicates)`. A tie is never broken by picking.

| unit | replicate verdicts | top count | majority? | family | flipped? |
|---|---|---|---|---|---|
| s1 | near, near, near | near 3 | yes | `near_family` | no |
| s2 | near, near, cross | near 2 | yes | `near_family` | yes |
| s3 | cross, near, cross | cross 2 | yes | `cross_family` | yes |
| s4 | near, cross, unclear | 1/1/1 | no | `unclear_family` | yes |
| s5 | near, unclear, near | near 2 | yes | `near_family` | yes |
| s6 | near, cross | 1/1 tie | no | `unclear_family` | yes |
| s7 | unclear, unclear, near | unclear 2 | yes | `unclear_family` | yes |

Hand totals: `near_family` **3** (s1, s2, s5) · `cross_family` **1** (s3) ·
`unclear_family` **3** (s4, s6, s7) · units **7** · flipped **6** · no_majority **2**.

`unclear_family` is the union of two different things and that is deliberate: a majority
for `unclear` (s7) and no majority at all (s4, s6) both mean *this repository does not know
the family*, and inventing a distinction between them in the headline would suggest a
precision the measurement does not have. Both counts are reported separately underneath.

---

## 5. What a passing test does not prove

A judge that answered `near` to everything would satisfy every arithmetic check in this
file while being useless, exactly as `HAND-COMPUTED.md` says of the four-way verdict. The
arithmetic being right is not the claim that the family judgments are any good. The
observable check on that is the flip rate: the existing judge flipped 18.1% of units at
temperature 0 on a four-way question, and a two-way question that flips at a comparable
rate is a coin, not a measurement. **Report the flip rate next to the profile, always.**

---

## 6. Corrections and notes

Corrections go here in place, struck through, never deleted — if the hand computation and
the code disagree, one of them has found something, and which one gets written down here.

### Correction 1 (2026-08-09): the `fabricated_class` sub-reason is a token, not the label

Section 2 cases c3, c10 and c11 originally expected the invented labels themselves as the
sub-reason: ~~`("fabricated_class", "กลัวแพง")`~~. **They now expect
`("fabricated_class", "outside_vocabulary")`.**

The hand computation was wrong, and an adversarial review found it. The reasoning it
missed: a fabricated label is *by definition* the one string in this module that a **model
wrote** rather than one drawn from a validated label space — every other category has
`answer ⊆ vocabulary`. `sub_reason` shipped on `_SHAREABLE_UNIT_FIELDS`, and
`assert_shareable_payload` rejects a bare Thai MSISDN and nothing else, so an arm that
emitted a customer name or a hyphen-separated number into a label cell would have
published it in a shareable export that passed every guard.

Nothing about the taxonomy or the hard cap changes: `fabricated_class` is still evaluated
before the subset tests and still wins over `over_labelling` on c11. The invented labels
remain available to a reviewer as `fabricated_labels` on the **restricted** record, and
the shareable export carries `fabricated_label_count` — which preserves the one
reader-facing use section 1 names for this category, evidence about whether constrained
decoding held, with no free text.

This is recorded rather than quietly applied because a hand-computed expectation edited to
match code is worth nothing. Here the code was changed too; the fixture is being corrected
because it was **wrong about data safety**, not because a test went red.

### Note 1 (2026-08-09): `call_result` cannot produce a mis-scoping category

**Note added 2026-08-09, during implementation, before any run was interpreted.**
`call_result` is a single-label dimension, so its label sets never hold more than one
element. Rows 6 and 7 of the decision table — `over_labelling` (truth ⊊ answer) and
`under_labelling` (answer ⊊ truth) — are therefore **unreachable** on that dimension: a
one-element set cannot strictly contain another non-empty one. Every wrong `call_result`
answer that names a class lands on row 8, `substitution`.

This is a property of the dimension, not a measurement. A `call_result` profile reporting
`mis-scoping: 0` says nothing about the arm, and must never be read as "this arm does not
over-label" — on `call_result` no arm can. The per-dimension breakdown exists partly so
this cannot be hidden inside a pooled number, and the same is true of the mirror
observation: **every** near/cross judgment spent on `call_result` is spent there because
substitution is the only outcome available, so that dimension will always dominate the
judged remainder relative to how much information it carries.
