# `asr_noise` — hand-derived expectation for the ten artifact classes

**Written 2026-08-06. `src/evalgen/evidence.py` is NOT modified and must not be modified
on the strength of this document.** `ASR_SUBSTITUTIONS` still holds exactly two entries.

## Why this file was written twice, and why the first one did not count

An earlier version of this document was produced during the `retention_v3` authoring run.
It declared itself hand-derived and pre-code. Adversarial review found that the scratch
script behind it — `measure.py`, docstring *"Read-only measurements for
asr_expectation.md"* — **imported `asr_normalise` and `ASR_SUBSTITUTIONS` from
`evalgen.evidence` and ran them.** The numbers in it were therefore produced by the very
code the document exists to check independently. It was then deleted along with the
scratch part files by an over-broad cleanup instruction, so it is not recoverable.

Both failures are worth recording rather than quietly redoing:

1. **The document was verified by reading its own claim about itself.** It said it was
   hand-derived; that assertion was accepted. An assertion is not evidence. The check that
   mattered — *what did the script that produced these numbers import* — was not run until
   review.
2. This is the **same class of error** as the `keyword` metric retracted earlier in this
   work, which was built from the data it judged and reported a format difference as a
   fidelity difference. `evidence.py:141` already carries the rule that came out of that:
   a new class of forgiveness arrives *"as a new category with its own hand-computed
   expectation, never by widening `asr_normalise` until the number goes away."*

This version is derived from the committed spans and their Unicode codepoints, read
directly out of `retention_v3.jsonl`. Nothing in `evalgen.evidence` was imported, called,
or consulted for a number. The verdicts below are fixed **now**, while no model has been
scored on these ten items.

## The decision each class needs

`evidence_rates` sorts every emitted `keyword` segment into three buckets:

| Bucket | Fires when | Means |
|---|---|---|
| `verbatim_split` | byte-exact substring of the transcript | the model quoted what it was given |
| `near_miss` | matches after `asr_normalise` on both sides | the model emitted **correct** Thai against a transcript that did not |
| `hard_miss` | neither | not in the transcript in any orthography |

Two independent questions per class, and conflating them is how `asr_normalise` gets
widened until a number looks good:

- **(a) Ought a model that repairs the artifact be forgiven?** A judgement about whether
  repairing it is *reading* or *rewriting*.
- **(b) Could `ASR_SUBSTITUTIONS` express it at all?** It is a tuple of literal
  `(old, new)` pairs applied unconditionally to **both** sides. Several classes below
  cannot be written that way, and one that can would be actively unsafe.

### The governing principle for (a)

Production is handed **audio** and transcribes it itself, so a model emitting correct Thai
from corrupted text is doing what production does. That argues for forgiveness. But
forgiveness is only safe where the repair is **deterministic and lossless** — where there
is exactly one correct answer and no information had to be invented.

**Forgive where the mapping is unambiguous. Do not forgive where the model had to guess.**
A guess that happens to be right and a guess that is wrong are indistinguishable in the
bucket, so silently forgiving guesses hides fabrication behind a normalisation rule.

---

## The ten classes

| # | Item | Artifact | Repair is | (a) verdict | (b) expressible? |
|---|---|---|---|---|---|
| 1 | RET-113 | tone mark absent — `เน็ตชา` for `เน็ตช้า` (missing U+0E49) | guessing | **`hard_miss`** | No — safely |
| 2 | RET-114 | word-internal spaces removed | deterministic-ish | **`hard_miss`** | No |
| 3 | RET-115 | Thai digits — `๑,๒๕๐` for `1,250` | deterministic | **`near_miss`** | **Yes** |
| 4 | RET-116 | proper noun mangled — `คอนเซ็นเตอร์` for `คอลเซ็นเตอร์` | guessing | **`hard_miss`** | No |
| 5 | RET-117 | homophone — `สันยา` for `สัญญา` | guessing | **`hard_miss`** | No — safely |
| 6 | RET-118 | stutter — `ผม ผมไม่ได้…` | algorithmic | **`hard_miss`** | No |
| 7 | RET-119 | mid-word truncation — `ค่าใช้จ่า` for `ค่าใช้จ่าย` | guessing | **`hard_miss`** | No |
| 8 | RET-120 | speaker label leaked into text | algorithmic | **`hard_miss`** | No |
| 9 | RET-121 | `เเ` (U+0E40 U+0E40) for `แ` (U+0E41) — **control** | deterministic | **`near_miss`** | Already is |
| 10 | RET-122 | U+200B inside words — **control** | deterministic | **`near_miss`** | Already is |

### Reasoning on the three that are forgiven

**RET-121 and RET-122 are the controls and are already handled.** Two SARA E is never
legitimate Thai, and a zero-width space carries no content. Both are one-to-one, lossless,
and cannot corrupt correct text. They are why the pack has controls at all: if these two
ever stop landing in `near_miss`, `asr_normalise` has broken.

**RET-115 (Thai digits) is the only class this document would extend the table for**, and
even then only as ten pairs `๐-๙ → 0-9`. That mapping is total, unambiguous, and cannot
alter a non-numeric character. Converting numerals is transcription normalisation, not
reconstruction — the information is fully present in the source.

**It is not being added.** `ASR_SUBSTITUTIONS` is unchanged, so RET-115 will currently
score `hard_miss` for a normalising model. That is recorded as a **known, expected
mismatch between this expectation and the code** rather than fixed, because changing the
table is a separate argued edit and doing it in the same breath as writing the expectation
is exactly the coupling this file exists to break. If a run shows it, the finding is
already written down here in advance.

### Why the other seven are not forgiven

- **Tone marks, homophones, proper nouns, truncation (1, 4, 5, 7):** each repair requires
  choosing among candidates. `เน็ตชา` could be `เน็ตช้า`; `สันยา` could be `สัญญา`. The
  model may be right, but it inferred rather than read, and a rule that forgives inference
  cannot tell a correct inference from a fabrication.
- **Word spaces and stutter (2, 6):** expressible only as an *algorithm* (strip all
  spaces; collapse repeated tokens), not as literal pairs. Stripping all spaces on both
  sides would also destroy RET-115's span, where a space is load-bearing between the
  amount and the following word — one class's normalisation silently corrupting another's
  evidence is precisely the failure mode.
- **Speaker-label leakage (8):** the leaked label is real text in the transcript. A model
  that removes it is editing the source, and one that keeps it is quoting faithfully. The
  faithful behaviour is already `verbatim_split`; nothing needs forgiving.

## What a run may legitimately change

Nothing here. If a model's behaviour disagrees with a verdict above, the disagreement is
the finding and it gets written down next to this table — the verdicts are not revised to
match. If a genuinely new artifact class appears that is deterministic and lossless, it
gets its own row here **first**, argued on the same principle, and only then is
`ASR_SUBSTITUTIONS` considered.
