# Eval improvement plan — post-Experiment-1

**Written:** 2026-08-05
**Status:** Phase 1 executing. Phase 2 partially blocked. Phase 3 blocked on data this repo does not hold.

## Context

Experiment 1 returned INDISTINGUISHABLE on all three scored dimensions. Three
independent critique passes on the proposed follow-up work established that the
obvious next step — expanding the test set from 20 to 50 items — would **degrade**
the report's headline deliverable rather than sharpen it. This plan records what is
being done instead, why, and how each item will be verified.

### The three findings that set the agenda

1. **The mechanism table is already saturated.** Four of five rows read FAIL/FAIL.
   Only `multislot` (n=2) discriminates. The verdict rule is monotone decreasing in
   group size — FAIL if *any* item fails on every replicate — so adding items can
   only push a row toward FAIL. Growing `multislot` from 2 to ~5 has roughly a
   two-in-three chance of collapsing the only informative row to FAIL/FAIL.

2. **The pre-registered bands are absolute counts calibrated to n=22.** At ~55 rows
   with the same discordance rate, the probability that noise alone yields a
   non-INDISTINGUISHABLE verdict rises from ~19% to ~46%. Expanding without
   re-deriving the bands silently breaks them; re-deriving them after seeing `reason`
   land at +5 is the move `EXPERIMENTS.md:91-93` already refused in writing.

3. **The rule space is nearly exhausted.** 81 citations resolve to 31 distinct lines,
   28 of them inside `prompt.py:4321-4399`. New items can only re-cite rules already
   covered, so the pack would grow 2.5x in size and ~0% in semantic coverage.

### A correction that shaped this plan

`EXPERIMENTS.md:131-132` claims Gemini's `keyword` is "a comma-stitched fabrication
that does not appear verbatim", and ranks acting on it as recommended next step 4.
**That claim is false.** Verified three ways:

- The schema description — identical in `src/evalgen/schemas/retention.json` and
  production `main.py:977` — instructs **"Use comma separation."** It is sent to both
  arms on every call.
- Comma-split, Gemini's keyword segments are verbatim substrings of the transcript at
  63/63 = 100% (base run). Whole-string matching scores the same data 27/40 = 67.5%.
- `fact_checker.py` reads `keyword` **zero** times. It is not scored in production.

The real signal runs the other way: **Qwen emits 0 commas across 257 keyword fields**
in every run, ignoring an explicit schema instruction 100% of the time. Gemini
complies. This is a diagnostic about the candidate, not a scored dimension.

**Process lesson, recorded because it is the point.** The false result was produced by
building a scorer from the data it would judge, with no hand-computed expectation
written first — exactly what `CONTRIBUTING.md:8-13` and `AGENTS.md:50-53` forbid. Every
new metric in this plan therefore carries a hand-computed fixture authored **before**
its implementation.

**Corollary that governs Phase 1:** `EXPERIMENTS.md` is now known to contain at least
one false claim. Its remaining claims are treated as hypotheses to verify, not facts to
act on. Task 1.2 in particular must confirm the RET-11 defect independently before any
fixture is edited.

---

## Phase 1 — executable now, no new Thai authored

### 1.1 Correct the false `keyword` claim

- **Outcome:** `EXPERIMENTS.md` no longer asserts Gemini fabricates keywords, and
  records the schema-compliance finding in its place.
- **Authoritative source:** `main.py:977` / `schemas/retention.json` (the instruction),
  `fact_checker.py` (absence of `keyword`), the four run logs (comma counts).
- **Scope boundary:** edits `EXPERIMENTS.md` only. Does not change any score.
- **Done when:** the false sentence is gone, the correction states what was measured
  and how, and recommended next step 4 is re-written or withdrawn.

### 1.2 Fix RET-11's ground truth — *only if the defect is real*

- **Outcome:** either RET-11 gains the omitted label with evidence span and rule
  citation, or the claim is refuted and `EXPERIMENTS.md` corrected a second time.
- **Authoritative source:** `prompt.py:4361` (the licensing rule) read against
  RET-11's own `transcript_th`. Both models emitting the label is corroboration, not
  proof.
- **Scope boundary:** `retention_v1.jsonl`, `retention_v1.gt.csv`, the README's class
  counts and sha256. No other item is touched.
- **Done when:** `evalgen check` returns 0 problems, the gt CSV and item agree under
  `_gt_disagreements`, the README sha matches the file, and the suite is green in both
  modes. **If the claim is refuted, no fixture changes at all.**

### 1.3 Arbitrate the two open class boundaries

- **Outcome:** a written, cited arbitration of `prompt.py:4333` vs `4375` (discount
  requests) and of `other` as an unbounded catch-all.
- **Authoritative source:** the cited production lines, read in full.
- **Scope boundary:** documentation only in this phase. Any resulting fixture change is
  a separate, argued edit — not bundled here.
- **Done when:** each boundary has a stated rule, the items it affects (RET-02, RET-12)
  are named with the effect on each, and the arbitration is written where the next
  author will find it (`VOCABULARIES.md`).

### 1.4 Raise the candidate arm's replicates 3 → 5

- **Outcome:** the nondeterministic arm is measured with enough replicates that its
  net numbers are quotable. `reason` net already moved +5 → +4 between passes.
- **Scope boundary:** a real API run costing ~$1-2. **Gated on explicit approval** —
  this plan does not spend money without it.
- **Done when:** approval is given and a 5-replicate candidate run exists, or the item
  is explicitly deferred and recorded as deferred.

### 1.5 `src/evalgen/evidence.py` — deterministic, diagnostic, never a scored dimension

- **Outcome:** a module reporting, per (arm, prompt): comma-split verbatim rate,
  whole-string verbatim rate **side by side**, customer-vs-agent attribution of each
  span, near-miss counts kept separate, and schema-comma compliance.
- **Authoritative source:** `records.py:57-60` for the comma-split convention;
  `prompt.py:4382-4387` for the customer-speech rule; the transcript for attribution.
- **Scope boundary:** lives in `src/evalgen/` (never `evalharness/` — `test_boundary.py`
  forbids it). Mirrors `fabrication.py`: takes paths, returns a dict, no CLI. **Does not
  feed any verdict, does not join the deviation list, does not rank the arms.**
- **Done when:** a hand-computed expectation written **before** the implementation
  agrees with the code on the committed runs; near-misses are reported apart from
  violations, with RET-11's `เเ`/`แ` ASR artifact correctly classified as a near-miss
  rather than a failure; and the suite is green in both modes.

### 1.6 Close the three real performance-reporting gaps

- **Outcome:** cost-per-correct-answer, throughput (tokens/sec), and a performance
  section in the text comparison report, which today shows none of it.
- **Authoritative source:** the fields already in `run.jsonl`; no new capture needed.
- **Scope boundary:** `report.py` gains a section and `ArmSummary` gains fields;
  `cli.py` populates them. Section order is an argument (`report.py:521-527`) — the new
  section goes with the aggregates, not before the mechanism table.
- **Done when:** the report prints tokens, cost and latency per arm; cost-per-correct
  is derived from the scored dimensions rather than invented; both modes green.
- **Not attempted:** time-to-first-token. It requires a streaming client and would
  change what every existing run means. Recorded as a known gap instead.

---

## Phase 2 — partially blocked

### 2.1 Re-derive the verdict bands with a power calculation — executable

- **Outcome:** bands expressed so they survive a change in n, with the derivation
  written down, plus the noise-driven false-verdict rate at n=22 and at candidate
  larger n.
- **Critical constraint:** derived from the arithmetic alone. The derivation must not
  reference the observed +5, or it is choosing the rule to fit the result.
- **Done when:** written into `EXPERIMENTS.md` with the calculation shown, **before**
  any new item exists.

### 2.2 Author additional items — BLOCKED, and deliberately so

Blocked on two prerequisites this repo cannot satisfy alone:

- **Native-speaker sign-off.** Outstanding since the pack was written
  (`README.md:146-151`). Every naturalness claim is currently LLM self-assessment.
- **Arbitrated boundaries (1.3).** `down sell not success` is one of the classes an
  expansion would target, and it sits on an unresolved boundary.

When unblocked: **+8 to 12 items, not +30**, targeted at regions that produce
*discordant* cells, distributed to protect the `multislot` row rather than swamp it.
Support-1 classes are explicitly **not** targets — they sit in `both_wrong`, which
contributes zero discriminating power.

---

## Phase 3 — blocked, and the only thing that makes any of this a verdict

`RECONCILED: NO`. No number this harness produces has been checked against the
Retention app's own live Gemini fact-check report. Everything in Phases 1 and 2 raises
the *precision* of an instrument of *unknown accuracy*. Requires True's ground-truth
workbook and fact-check report. **Nothing in this repository can perform this check,
and no code path prints `RECONCILED: YES`.**

---

## Verification protocol

Per `CLAUDE.md`'s Build and Verification Contract, applied to every task above:

1. **Both suite modes**, every time — a green standalone run does not prove the
   differential still agrees:
   ```bash
   .venv/Scripts/python -m pytest tests/ -q
   TRUE_SOURCE_ROOT=<repo>/production-reference/sentiment-batch-retention-main \
     .venv/Scripts/python -m pytest tests/ -q
   ```
   Baseline before this plan: **375 passed / 11 skipped** standalone,
   **386 passed / 0 skipped** differential.
2. **`evalgen check`** returns 0 problems after any fixture edit.
3. **No expectation is edited to make a test pass.** If a hand-computed fixture and the
   code disagree, one of them has found something; work out which and write the answer
   down (`CLAUDE.md`).
4. **No gate is weakened.** The pins, the coverage refusal, the manifest block and the
   `RECONCILED` stamp stay as they are.
5. **New metrics get a hand-computed expectation authored before the implementation.**
   This is the direct lesson of the retracted `keyword` result.

## Risks

| Risk | Mitigation |
|---|---|
| RET-11 "defect" is itself wrong | Task 1.2 verifies before editing; refutation is an acceptable outcome |
| `evidence.py` becomes a de-facto 4th dimension | Diagnostic only; never read by `render()`'s verdict path; asserted by test |
| Fixture edit breaks hardcoded counts across ~6 test files | Enumerated up front; counts updated with the change, never the reverse |
| Near-miss counter hides real violations | Near-misses reported apart from violations, never netted |
