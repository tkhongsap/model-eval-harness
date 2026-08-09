---
type: plan
created: 2026-08-09
status: goal contract, in execution
tags: [work/true, project/intelligence-layer, evaluation, llm-judge]
---

# Error severity: the goal contract, and the plan that satisfies it

Follows `docs/llm-judge-direction.md` (the SciCode comparison and recommendation) and the
Build and Verification Contract in `CLAUDE.md`. Nothing here changes a scored dimension,
a verdict, or the migration recommendation.

---

## 1. The goal contract

Stated before any file is edited, in the four parts `CLAUDE.md` requires.

### Outcome

Every wrong answer the harness already counts as a flat zero gains a **recorded error
category**, so a report can say *how* an arm was wrong and not only *how often*. The
deliverable is a per-arm severity profile printed beside the existing dimension table.

### Authoritative source

| Question | Authority |
|---|---|
| Which units an arm got wrong | `evalharness.compare` — the same correctness code the paired verdict uses. Severity never re-implements correctness. |
| What the legal classes are | `evalharness.labelspaces` — `REASON_CLASSES` (11), `CALL_RESULT_CLASSES` (4), copied verbatim from `fact_checker.py`. |
| Whether two classes are neighbours | The production rule text at `prompt.py:4321-4399`, quoted verbatim into the prompt by the machinery built on 2026-08-09. Not the judge's intuition, and not class-name similarity. |
| The expected arithmetic | `tests/fixtures/judge/SEVERITY-HAND-COMPUTED.md`, written **before** `severity.py`. |

### Scope boundary

**In scope:** the `call_result` and `reason` dimensions, on the units the scorer already
counts as wrong, for both arms of a pair.

**Out of scope, deliberately:**

- **`product`.** Experiment 5A returned zero informative verdicts on `product` across all
  nine comparisons scored (every one landed `d < 6`), and the dimension carries the
  reproduced null-phone drop. Its comparison grain is the call, not the row, so it would
  need its own set arithmetic. Deferred with a reason, not forgotten.
- **Any change to a score.** No severity value joins `PairedVerdict`, `decision()`, or a
  `MechanismRow`. Enforced by an AST test, the same way `judge.py`'s isolation is.
- **Any change to the ground truth.** A severity category is an opinion about a *model's*
  answer. It says nothing about whether the reference label is right; that is what the
  existing judge is for, and its 5-flag queue is untouched by this work.
- **Re-litigating the migration decision.** `docs/migration-decision-2026-08-07.md`
  stands. If the severity profile turns out to favour a candidate, that is a footnote
  for a future comparison, not a reopening.

### Observable done criteria

1. `tests/fixtures/judge/SEVERITY-HAND-COMPUTED.md` exists and is dated **earlier in the
   file history than** `src/evalgen/severity.py`.
2. `PYTHONPATH=src python -m pytest tests/ -q -rs` green in **both** modes (standalone and
   with `TRUE_SOURCE_ROOT` set), with the observed counts and every skip reason reported —
   not compared against a hardcoded number.
3. A deterministic pass over the committed v3 runs completes with **zero model calls** and
   prints the category distribution plus the exact size of the judged remainder.
4. Every unit the scorer calls wrong receives exactly one category. The count of
   categorised units equals the count of wrong units from **`comparison_units`** —
   asserted by a test, on a fixture that contains a multi-product call so the assertion
   can actually fail.

   *(Corrected 2026-08-09 after review: this criterion originally named
   `comparison_clusters`, which is wrong for this diagnostic and would have made the
   code the outlier. `comparison_clusters` is the population for **inference**, where two
   products from one call must not count as two independent customers. Severity is not
   inference: it describes one answer, and an answer is given per scored row. The cost of
   row grain is that one call with three product rows contributes three units to the
   counts and rates — stated in the report, on the page and in the JSON, rather than
   left for a reader to infer. The reviewer also found that the test named for this
   criterion could not fail, because every call in its fixture had exactly one product.)*
5. The judged run completes with zero transport errors, zero identity mismatches, and no
   silent fallback: any unit whose rule text failed to resolve is visible in the report.
6. An adversarial multi-agent review finds no surviving defect, or every surviving finding
   is fixed and recorded.

---

## 2. The taxonomy, and why it is not the one I recommended

`docs/llm-judge-direction.md` proposed a flat four-level scale
(`over_labelling` / `near_family` / `cross_family` / `fabricated_class`). Working the
arithmetic by hand before writing code found two problems with it, so the shipped
taxonomy differs. **Recording the change rather than quietly shipping it** is the point of
this section.

**Problem 1: the flat scale has no slot for a missing label.** The `reason` dimension is a
set (`main`, `secondary`, `third` unioned, `fact_checker.py:873`). An answer can be a
strict *subset* of the truth — it asserted nothing false and missed something. The flat
scale would have to file that as a substitution, which is exactly wrong: no wrong class
was ever named.

**Problem 2: a total order over all four is not defensible.** Is an unsupported extra
reason worse than a missed one? That depends on what the monthly report does with the
category, which is a question for the app owners, not for this repository. Asserting an
order anyway would be inventing a business judgment and hiding it in a constant.

So the shipped taxonomy is **two tiers with one ordering claim inside the second**:

| Tier | Category | Meaning | Decided by |
|---|---|---|---|
| **Mis-scoping** — the answer names only classes the transcript licenses, but the wrong number of them | `over_labelling` | truth ⊊ answer | set arithmetic |
| | `under_labelling` | answer ⊊ truth, answer non-empty | set arithmetic |
| **Mis-classification** — the answer names a class the transcript does not license | `near_family` | wrong class, but the neighbouring one: a specific quoted clause separates them | **judge** |
| | `cross_family` | wrong class from a different part of the rule set | **judge** |
| | `fabricated_class` | a class outside the declared label space | set arithmetic (**hard cap**) |
| **Not a severity judgment** — counted, never dropped | `no_answer` | answer empty, truth non-empty | set arithmetic |
| | `unsupported_claim` | answer non-empty, truth empty — sub-reason `empty_ground_truth` or `no_ground_truth_row` | set arithmetic |
| | `missing_output` | no prediction record at all | set arithmetic |
| | `invalid_output` | record present, `parse_ok == False` | set arithmetic |
| | `unclear_family` | a substitution the judge could not resolve, or where its replicates tied | judge, no majority |

**Two renames happened during hand computation, and are recorded here rather than
shipped quietly** — the point this section opens with. `orphan_claim` became
`unsupported_claim`, broadened to cover a ground-truth row that exists and carries no
label as well as one that does not exist at all, because both mean "the answer claimed
where the truth claims nothing" and the difference is a sub-reason, not a category. And
the single `invalid_output` split into `missing_output` (no record) and `invalid_output`
(`parse_ok == False`), because the fixture's rows 1 and 2 need them apart: a parse failure
must never be reported as a fabricated class, and merging them would sanction exactly
that. `tests/fixtures/judge/SEVERITY-HAND-COMPUTED.md` is the governing document and
already carried both.

**The one ordering claim: mis-classification is more severe than mis-scoping**, because
mis-scoping mis-counts a class the call actually supports while mis-classification asserts
a class it does not. Inside mis-classification, `near_family < cross_family <
fabricated_class`: a fine rule boundary missed, versus the wrong neighbourhood entirely,
versus leaving the vocabulary. No order is asserted between `over_labelling` and
`under_labelling`, and the report prints them side by side rather than summing them into
one number.

**The hard cap, in the SciCode sense.** `fabricated_class` is checked *first*, before the
subset relations. An answer that contains every true label plus one class that does not
exist in `REASON_CLASSES` is `fabricated_class`, never `over_labelling`, and no judge is
consulted — there is nothing to judge. This is evaluation order doing the work a prose
rule would only claim.

---

## 3. What the judge is asked, and what it is not trusted with

The judge sees exactly one binary question, on exactly the units set arithmetic cannot
settle: **is this substitution near or cross?** It cannot reclassify a deterministic
category, cannot see the other arm, and cannot change a score.

Taken from `T481__fast_monte_carlo/tests/llm_judge.py`, deliberately:

1. **A hard cap in the prompt.** *If the transcript contains no span that could trigger
   the answer's class under its own quoted rule, the family is `cross`* — whatever surface
   similarity the two class names carry. Name similarity is the credulity trap in this
   vocabulary (`promotion related` and `device promotion related` are adjacent in the
   class list and separated by an explicit clause; `save cost` and `promotion related` read
   as unrelated and are separated by a single CRITICAL line at `prompt.py:4345`).
2. **Demonstrated, not claimed.** Two byte-exact evidence gates, both required for a
   decisive answer: `cited_span` must occur verbatim in the transcript (the gate
   `judge.py` already enforces), and `cited_rule_line` must occur verbatim in the rule
   text **this prompt quoted**. A judge that paraphrases a rule it was handed fails the
   gate and lands in `unclear_family`, scored, never dropped.

Deliberately **not** taken from it:

1. **The silent no-API-key fallback.** T481's judge substitutes a keyword heuristic when
   no key is present, and the resulting score is indistinguishable downstream from a real
   one. Here, a missing key or an unresolvable rule root **refuses**. That refusal already
   exists in `run_judge` for the rule root and is reused verbatim.
2. **The score entering the grade.** Severity is a diagnostic surface like `evidence.py`'s
   rates.
3. **Single-shot scoring.** The judge was measured on 2026-08-09 flipping its verdict on
   4 of 8 byte-identical requests at temperature 0, and 18.1% of units flipped across the
   e6c replicated run. Severity therefore runs 3 replicates per unit with a strict
   majority; a tie is `unclear_family`, never broken by picking.

**No p-values on this output.** The severity profile is a distribution over units that are
not independent (several rows can come from one call), and this repository has already
been bitten once by treating replicated rows as independent units. The report prints
counts and rates, states the unit grain, and computes no test.

---

## 4. Build order

Fixture first, deterministic before judged, cheapest gate before the expensive one.

| # | Step | Cost | Gate before proceeding |
|---|---|---|---|
| 1 | `SEVERITY-HAND-COMPUTED.md` — decision table, hard-cap ordering, hand-classified real units, majority table | 0 | It is written down before `severity.py` exists |
| 2 | `severity.py` — deterministic classifier only | 0 | Reproduces the hand table byte for byte |
| 3 | `severity.py` — judge path, both evidence gates, replicate majority | 0 | Placebo elimination proven: a judged request is never byte-identical to a request the deterministic path would have skipped |
| 4 | `evalgen severity --deterministic-only` over the v3 runs | 0 | Every wrong unit categorised; the judged remainder is a printed number, not an estimate |
| 5 | Both suite modes | 0 | Green, with observed counts and skip reasons reported |
| 6 | The judged remainder | ~$0.08 at the estimate in `llm-judge-direction.md`; the real figure comes from step 4 | Zero transport errors, zero identity mismatches, zero silent rule-text fallbacks |
| 7 | Adversarial multi-agent verification | tokens only | Every surviving finding fixed or recorded |

Step 4 gating step 6 is the whole point of the deterministic-first design: the spend is
approved against a counted remainder, not a guess.

---

## 5. Standing constraints this work does not get to relax

- No expectation edited to make a test pass. If the hand computation and the code
  disagree, one of them found something, and the answer is written into the fixture.
- No gate weakened. The judge's comparability, sha, classification-downgrade and
  private-out gates apply unchanged to the severity command.
- Nothing added to the closed deviation list.
- No code path prints `RECONCILED: YES`.
- No customer identifier, absolute path, or OS account name in any shareable export — the
  2026-08-09 `rule_text.root` leak is a regression test, and the severity export gets the
  same test rather than the same assumption.
- `out/` stays gitignored. Only an aggregate summary is ever a candidate for commit.

---

## 6. What this cannot buy

It cannot make the answer key more right — that is the existing judge's job, and its
5-flag queue still needs a human. It cannot compare arms on severity with any statistical
force: the profile is descriptive. It measures errors against a **synthetic** pack, so a
severity profile here is a property of `retention_v3`, not of production traffic. And it
does not move `RECONCILED: NO` one inch closer to `YES` — only the real labelled batch
does that, which is why the Ask 1 email is drafted in the same change.
