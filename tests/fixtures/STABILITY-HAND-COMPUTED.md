# Stability decomposition: the expectation, computed by hand before `src/evalgen/stability.py`

**Written 2026-08-09.** Same discipline as `tests/fixtures/WORKED-COMPUTATION.md` and
`tests/fixtures/judge/SEVERITY-HAND-COMPUTED.md`: the arithmetic is settled on paper
first, so a failing test has two independent derivations to compare rather than one
implementation checking itself.

The real items in section 3 were classified by **reading the payload fields out of
`out/runs/20260806-025645Z-v3-qwen27b/run.jsonl` directly** — product name,
`retention_outcome`, the three `.reason` slots, and the unscored fields — not by running
the module this file specifies. A script was used to *locate* candidates; the
classification below is by eye.

---

## 1. Why this diagnostic exists, and the constraint it ships under

Experiment 7 failed Qwen3.6 27B on stability alone: `BEHIND (-129/129)`, against
`UNDERPOWERED` / `INDISTINGUISHABLE` on all three quality dimensions. The gate is defined
as **exact structured response agreement across replicates**, so it counts any byte that
moves — including bytes in `recommendation`, `keyword` and `call_event_detection`, which
the production schema requests and **no metric reads**.

This module measures how much of that instability the scorer can actually see.

> **Honesty constraint, recorded before the code exists.** We already know from a
> 2026-08-09 measurement that scoring stability at the label level instead of the raw
> level would flip Qwen 27B's verdict. The direction of that change is known **in
> advance**, which is exactly the situation this repository's rules exist to prevent. This
> diagnostic therefore **cannot replace the pre-registered gate** in that comparison. It is
> an additional recorded number printed *beside* the gate, and this paragraph is quoted in
> the module that computes it. Anyone proposing to make it the gate must do so on its
> merits, in writing, knowing that the answer is already known.

---

## 2. Definitions, and the decision table

The unit is **one item (call) within one arm**, across that arm's replicates.

| Term | Definition |
|---|---|
| `observable` | the item has **≥ 2** replicates. With one replicate stability is not observable, and such an item is counted separately — **never as stable**, which would let a single-replicate run report 0% instability. |
| `raw_unstable` | the model's raw response text differs across replicates (sha256 over `raw_content`) |
| `scored_unstable` | the **scored fingerprint** differs across replicates |
| `cosmetic_only` | `raw_unstable` **and not** `scored_unstable` |

**The scored fingerprint is whatever the scorer reads, obtained from the scorer's own
code** — `flatten.to_rows(payload, item, parse_ok=...)` then `records.from_row` — as the
order-insensitive set of `(product, call_result, frozenset(reasons), parse_ok)`.

It is not a re-derivation. Section 3's RET-04 is the reason: a lookalike that compared the
three reason *slots* would call that item unstable, and the scorer would not, because
`parse_reasons` unions `main`/`secondary`/`third` into a **set** (`fact_checker.py:873`).

### Constructed cases, one per branch

| # | replicates | raw text | scored fingerprint | expected |
|---|---|---|---|---|
| c1 | 1 | — | — | `not_observable` |
| c2 | 2 | identical | identical | stable |
| c3 | 2 | differ | identical | **`cosmetic_only`** |
| c4 | 3 | differ | one replicate differs | `scored_unstable` |
| c5 | 2 | differ | one replicate has `parse_ok=False` | `scored_unstable` — `parse_ok` is part of the fingerprint, so a replicate that failed to parse is a scored change, not a cosmetic one |
| c6 | 2 | **identical** | **differs** | **impossible; raise.** Identical text that produces two different scored fingerprints means the flatten is non-deterministic, which is a defect to surface, not a category to report. |

### Unscored-field attribution

Descriptive and computed **independently** of the classification above: for each item,
which of `recommendation`, `call_event_detection`, `keyword` differ across replicates.

Attribution is reported for scored-unstable items too. A `cosmetic_only` item is *defined*
by its scored fingerprint holding still, not by which unscored field moved — conflating
the two would make the attribution unfalsifiable.

`keyword` is nested inside each product block's `main`/`secondary`/`third`, so it is
compared separately from the scored parts of the same block.

---

## 3. Real items, classified by hand

Arm `v3-qwen27b`, 3 replicates each.

| item | scored fingerprint per replicate | unscored fields that differ | expected |
|---|---|---|---|
| **RET-03** | `(Postpaid, save, {save cost})` × 3 — identical | `recommendation` only | `cosmetic_only` |
| **RET-02** | `(TOL, churn, {promotion related, save cost})`, same, then `(TOL, churn, {promotion related})` | `recommendation`, `keyword` | `scored_unstable` |
| **RET-04** | `(TVS, churn, {dissatisfied service})` × 3 — identical | `recommendation`, `call_event_detection`, `keyword` | `cosmetic_only` |

**RET-04 is the case this fixture exists for.** Its replicates put the label in different
*slots* — replicate 2 writes `dissatisfied service` into `main`, `secondary` **and**
`third`, while replicates 1 and 3 write it only into `main`. The raw text differs, the slot
assignment differs, and the scorer sees `{dissatisfied service}` all three times because it
unions the slots into a set. An implementation that compares slots rather than the set
reports this as instability that does not exist.

**RET-02 is the genuine one:** replicate 3 drops `save cost` from the reason set entirely.
That is a label the scorer reads, so it is a real scored change — and note it also moves
two unscored fields, which is why attribution must not be used to decide the category.

---

## 4. Aggregate arithmetic

Denominator is **observable items**, never all items.

Worked by hand over a constructed 7-item arm:

| item | observable | raw_unstable | scored_unstable |
|---|---|---|---|
| a1 | yes | no | no |
| a2 | yes | yes | no |
| a3 | yes | yes | yes |
| a4 | yes | yes | no |
| a5 | yes | no | no |
| a6 | **no** (1 replicate) | — | — |
| a7 | yes | yes | yes |

Hand totals: `items` **7** · `observable` **6** · `not_observable` **1** ·
`raw_unstable` **4** (a2, a3, a4, a7) · `scored_unstable` **2** (a3, a7) ·
`cosmetic_only` **2** (a2, a4).

Rates over the 6 observable items: raw **4/6 = 66.7%** · scored **2/6 = 33.3%** ·
cosmetic **2/6 = 33.3%**.

Cosmetic share **of the instability**, which is the number this diagnostic exists to
report: `cosmetic_only / raw_unstable` = **2/4 = 50.0%**. Reported as `0.0` rather than
`nan` when `raw_unstable` is zero, matching `evidence._with_rates`, `summarize_judgments`
and `severity_profile`.

---

## 5. What a passing test does not prove

That the decomposition is *useful*. An arm whose instability is 100% cosmetic is not
thereby safe to deploy: the raw text is what a downstream consumer of `recommendation`
would read, and nothing here measures whether that text is any good. This module answers
one narrow question — *can the scorer see this instability* — and the answer must never be
restated as *does this instability matter*.

---

## 6. Corrections

None yet. Corrections go here in place, struck through, never deleted — if the hand
computation and the code disagree, one of them has found something, and which one gets
written down here.
