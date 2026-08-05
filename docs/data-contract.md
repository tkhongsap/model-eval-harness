---
type: spec
created: 2026-08-04
status: draft
tags: [work/true, project/intelligence-layer, evaluation]
---

# Data contract: what the evaluation harness needs

**Status: draft, not sent.** For Anan Sanongchitcharorn's team (Retention app first).

This asks for the smallest set of data that makes a defensible Gemini vs self-hosted
comparison possible. It deliberately excludes customer phone numbers and call content.
Every item states why it is needed, so anything that looks wrong can be pushed back on
rather than guessed at.

Scope of this document: **Retention only**. MNP, RTR, Sentiment QA and Telesales follow the
same shape once Retention is proven, but their label spaces and thresholds differ and each
needs its own version.

---

## Ask 1: the first two header rows. No data. (Costs 5 minutes, unblocks the most.)

Send the **first two rows only** of
`/Control Management/Call Center/Sentiment Analysis Retention Reason/fact_check/ground_truth/input/Post Evaluate Sentiment Analysis Retention.xlsm`,
sheet `Raw Data with User`. Header rows, zero data rows.

**Why this is first.** `prepare_ground_truth` reconstructs column names from a **two-row
header block**, merging `data[0]` and `data[1]` (`fact_checker.py:517-525`). We are currently
guessing how those two rows combine. Two header rows settle it exactly, and contain no
customer record at all, so there is no privacy question to resolve before sending them.

## Ask 2: row counts and class distribution (a number, not a file)

Per workbook, please confirm:

| Question | Why we need it |
|---|---|
| Total labelled row count | Sizes the sample. This is open decision #2 in the evaluation framework and currently blocks the sample design entirely. |
| Count per `call_result` value (`save` / `churn` / `unknown` / `undefined`) | Minority classes drive the sample size. A class with 12 examples cannot be gated. |
| Count per `reason` class (the 11 in `fact_checker.py:857-869`) | Same, and tells us which reasons are measurable at all. |
| Distinct `call_id` count vs row count | Rows are at **product** grain, not call grain. We need both numbers to compute the right denominators. |
| **Count of rows where `phone_number` is null, blank, or `0`** | **See the note below. This number is the size of a blind spot in the current product metric.** |
| Date range covered | So the eval sample is not drawn from a single campaign or shift. |

### Why the null `phone_number` count matters

`pre_process` maps `phone_number` values of `0` and `'0'` to `None` (`fact_checker.py:718-730`).
The product dimension is then computed on `groupby(['call_id','phone_number'])` (`:1080-1081`),
**without `dropna=False`**, and pandas drops NaN group keys by default.

So every call with a null, blank, or zero phone number is **silently dropped from the product
metric**, while still being scored in `call_result` and `reason`. Nothing in the report says so:
a product class that was never evaluated reports `weight = 0, accuracy = 1.0000`, which reads as
a perfect score.

We are not asking you to change production. We reproduce the same behaviour so the numbers stay
comparable, and we count the dropped rows so the loss is visible. But we need to know how large it
is: if it is a handful of rows it is a footnote, and if it is 10% of the workbook then the product
dimension is not currently measuring what the monthly report implies it measures.

This also settles a question from the framework table: "300 calls = 10% of total call volume."
Monthly actuals across the four apps are roughly 83,000 files, so 300 is about 0.4%. If 10%
refers to the labelled workbook rather than production volume, this answers both questions at once.

## Ask 3: the ground-truth extract

**Columns to send:**

| Column | Form | Notes |
|---|---|---|
| `call_id` | as-is | join key |
| `phone_number` | **HMAC-SHA256, as text** | see "Why phone_number is hashed and not dropped" below |
| `product` | as-is | part of the join key and its own scored dimension |
| `call_result` | as-is | scored dimension |
| `main`, `secondary`, `third` | as-is | the reason labels |

**Columns to exclude, explicitly:**

- The raw `phone_number` (send the hash instead)
- Any free-text notes, agent comments, or verbatim call-content columns
- Agent names, employee IDs, customer names, any other identifier

The evaluation joins on `call_id`, the phone hash, and `product`, and scores `call_result`,
the reason set, and `product`. Nothing else is used, so nothing else needs to travel.

## Ask 4: the prediction extract, per arm

One file per arm (Gemini incumbent, and later each candidate), same items, same order-independent keys:

| Field | Why |
|---|---|
| `call_id`, `phone_hash`, `product` | the join key, supplied **explicitly** |
| `call_result`, `main`, `secondary`, `third` | the model's answers |
| `parse_ok` (boolean) | **critical, see below** |
| `raw_output` (the model's structured output verbatim) | lets us recompute if a parse rule changes |
| `model_version` as **observed from the response**, not a constant | RTR writes a hardcoded literal (`fact_checker.py:851`); we need what actually answered |

**Please supply the join key explicitly rather than letting us re-derive it.** Production
recovers `call_id` by string-parsing a GCS `fileUri` (`fact_checker.py:565`), which silently
yields `None` when a filename lacks an underscore. That is fine in production, where the same
code wrote the filename. It is not fine across two different backends.

**`parse_ok` is not optional.** If an item is dropped because the model returned unparseable
output, and we cannot see that it was dropped, the arm that fails more often scores **higher**.
We verified this against the production scorer: a prediction set that is entirely empty scores
**0.909 weighted accuracy** on the reason dimension, clearing both the `acceptable: 85` and
`good: 90` bands, with recall 0.0. Holding the error count equal, an arm with 2 parse failures
outscores an arm with 2 wrong-but-parseable answers. A self-hosted model without forced tool
calling is exactly the arm that produces unparseable output, so without `parse_ok` the
comparison is biased in the candidate's favour and would not survive review.

---

## Why `phone_number` is hashed and not dropped

It is a **join key**, not a display field. The scorer merges on
`['call_id', 'phone_number', 'product']` (`fact_checker.py:1075`) and groups on
`['call_id', 'phone_number']` for the product dimension (`:1080-1081`). Dropping it breaks the
join; keeping it raw puts customer MSISDNs on machines outside SharePoint's controls, on a
project whose entire justification is data residency.

So: `HMAC-SHA256(phone_number, key)`, sent as text.

Three requirements:

1. **Computed identically on the ground truth and on every arm.** A hash mismatch is
   indistinguishable from a missing prediction, and would show up as a false regression.
2. **Normalise before hashing.** Production maps `phone_number` values of `0` and `'0'` to
   `None` and blanks to `None` (`fact_checker.py:718-730`). Apply the same normalisation
   first, or two representations of the same number will hash differently.
3. **Your team keeps the key.** We never need it. When the review needs to listen to a
   specific call, we hand back the hashed key and you resolve it inside your systems.

## What we are not asking for

- **The `.xlsm` workbook itself.** We do not need it and would rather not hold it.
- **Any audio.** Nothing on our side transcribes. This line read *"the harness scores
  model outputs; it does not run models"* until 2026-08-05, when `src/evalgen/` landed;
  the harness does call models now, but only ever with text it already holds. The audio
  path stays entirely inside production, and what we are asking for is unchanged.
- **Transcripts.** None exist in production (audio goes to Gemini in one call), and the
  transcript question is a separate decision that has not been made.
- **Anything from Sentiment QA yet.** Retention is first because it is the only app in the
  estate whose scorer computes a real four-cell confusion matrix. QA and Telesales hard-set
  `FN = TN = 0`, so their recall is structurally 100% and cannot fail.

## What happens on our side

The harness reads only the two extracts above, from a directory outside any git repository
(enforced at runtime, not by convention). It emits per-dimension metrics with explicit
coverage counts, a paired incumbent-vs-candidate difference, and a per-item regression list
keyed by the hash. No customer identifier is written to any report or committed anywhere.

Until a run has been reconciled against the app's own live Gemini fact-check report, every
report the harness prints is stamped `RECONCILED: NO` and is not a migration verdict.
