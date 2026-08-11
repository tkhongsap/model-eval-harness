# Retention Thai testset — `retention_v1`

Synthetic Thai call transcripts for comparing two LLMs on True Corp's **Retention**
call-analysis app. Twenty items, twenty-two scored rows, no real customer data.

**Everything below describes `retention_v1` unless it says otherwise.** Two later
cumulative packs sit in this directory and have their own sections at the end:
`retention_v2` (100 items, 108 scored rows) and `retention_v3` (138 items, 150 scored
rows, four new families). A separate non-cumulative challenge pack is also documented
at the end.
`retention_v1` is **frozen**: Experiments 1 and 2 cite it, so nothing this document says
about it moves.

| File | What it is |
|---|---|
| `retention_v1.jsonl` | The testset. One JSON object per line, `RET-01` … `RET-20`. UTF-8, **no BOM**, LF only. |
| `retention_v1.gt.csv` | Ground truth flattened to the scorer's grain — one row per `(call_id, phone_number, product)`. 22 rows. |
| `retention_v2.jsonl` | The 100-item pack, `RET-01` … `RET-100`. Same contract, same encoding rules. Its first 20 items are byte-identical to `retention_v1.jsonl`. See [The v2 pack](#the-v2-pack--retention_v2). |
| `retention_v2.gt.csv` | v2 ground truth at the same grain. 108 rows. |
| `retention_v3.jsonl` | The 138-item pack, `RET-01` … `RET-138`. Its first 100 items are byte-identical to `retention_v2.jsonl`. See [The v3 pack](#the-v3-pack--retention_v3). |
| `retention_v3.gt.csv` | v3 ground truth at the same grain. 150 rows. |
| `ASR-EXPECTATION.md` | Hand-derived expectation for the ten `asr_noise` artifact classes in v3, derived before any model was scored on them. |
| `retention_v3.manifest.json` | Versioned dataset contract: hashes, row counts, slice ids, provenance and review status. |
| `retention_challenge_v1.jsonl` | Separate 50-call, 64-row multi-turn challenge pack, `RTC-001` … `RTC-050`; it is not a continuation of the frozen `RET-*` chain. |
| `retention_challenge_v1.gt.csv` | Challenge-pack ground truth at the same product-level grain. |
| `retention_challenge_v1.manifest.json` | Challenge-pack hashes, research provenance, quantitative controls and limitations. |
| `VOCABULARIES.md` | Label vocabularies, provenance and the D1–D14 divergence register. The authority for what a label means. |
| `block_a_clear.jsonl`, `block_b_thai.jsonl`, `block_c_tiebreak.jsonl`, `block_d_escape.jsonl` | The pre-merge drafts. Superseded by `retention_v1.jsonl`; kept only for diff history. **Do not score against them** — they were never run through the loader and fail `validate()` in 155 places. |

---

## What the app under test is

The **Retention** app: `production-reference/sentiment-batch-retention-main/`.

Every `rule_*` citation in this file resolves against that repository:

| Short name | Path |
|---|---|
| `prompt.py` | `sentiment-batch-retention-main/src/prompt.py` — `prompt_v9_16` at `:4313`, the retention semantics |
| `main.py` | `sentiment-batch-retention-main/src/main.py` — the Vertex AI function-calling schema |
| `fact_checker.py` | `sentiment-batch-retention-main/src/modules/fact_checker.py` — the scorer |

**`prompt.txt` is not cited anywhere in this pack.** It belongs to the sibling **MNP** app
(`sentiment-batch-mnp-develop/config/system_prompt/prompt.txt`) and disagrees with the
retention prompt on four decisive points — indecision, `undefine`/`undefined`, `ไปต่างประเทศ`,
and whether a bare price complaint is `save cost`. Citation counts in `retention_v1.jsonl`:
**79 `prompt.py`, 2 `fact_checker.py` (grain and set-union mechanics), 1 `main.py` (the
`undefined` enum).**

## The contract every item satisfies

Per `src/evalgen/testsets.py:7-9`, each ground-truth label carries both of:

* `ev_<dimension>:<label>` — a **verbatim substring of that item's own transcript**;
* `rule_<dimension>:<label>` — a `file:line` citation of the production line that makes that
  span decisive.

Dimensions are `product`, `call_result`, `reason` (the `main`/`secondary`/`third` columns are
all `reason`; rank is discarded at `fact_checker.py:873-878`). Labels are lowercased in the key.

Two key forms beyond the base contract are used here and are **deliberate**:

* **`ev_<dim>:<label>#2`** — a second corroborating span for the same label. `validate()` holds
  it to the same verbatim standard; it binds to no extra gt cell. Used where one label is
  carried by two rows (`RET-16` `save cost`, once for the mobile row and once for the
  broadband row — the evidence dict is flat and has no per-row namespace) or where a second
  span is worth recording (`RET-02`, `RET-04`, `RET-06`).
* **`grain` / `merge`** — mechanics citations, not label citations (`RET-16`, `RET-17`).

---

## Items: 20 · Families

| Family | Items | n | What it stresses |
|---|---|---:|---|
| `clear` | RET-01 … RET-06 | 6 | One product, one reason, spans a production line names directly. The floor. |
| `thai_linguistic` | RET-07 … RET-12 | 6 | Negation scope, speaker attribution, cross-script segmentation, ASR orthography (`เเ` for `แ`, one U+200B), Thai numerals + Buddhist Era. |
| `tiebreak` | RET-13, RET-14, RET-15 | 3 | Two production classes both plausibly match; the item states which one(s) production licenses. |
| `multislot` | RET-16, RET-17 | 2 | Three-row grain in one call (RET-16); comma-packed multi-label cell scored by set union (RET-17). |
| `escape` | RET-18, RET-19, RET-20 | 3 | Refused reason → `unknown` product; out-of-scope billing call → `undefined`; truncated call + nested quotation marks. |

## Ground-truth rows: 22 (not 20)

The scored row is **`(call_id, phone_number, product)`**, not the call
(`fact_checker.py:1075`). `RET-16` is one call carrying three products that reach three
different outcomes, so it emits **three** rows (`5016` × postpaid / tol / tvs). Every other
item emits one. 19 × 1 + 1 × 3 = **22**.

`retention_v1.gt.csv` columns are exactly
`call_id,phone_number,product,call_result,main,secondary,third`, with `product` **lowercased**
to match `fact_checker.py:971-972` (`['postpaid','tol','tvs','unknown']`). `call_id` and
`phone_number` are written as strings and must be loaded as strings — an int `call_id` on
either side of `pd.merge` produces zero matched rows and a silently empty comparison.

---

## Coverage audit

29 reason labels across 22 rows — **1.318 reasons per row**. A degenerate all-empty arm still
scores well on the reason dimension, which is the pathology the harness exists to catch; the
mean is kept low on purpose so that property survives.

### `reason` — 11 classes, all non-zero

| Class | n | Items |
|---|---:|---|
| `network` | 5 | RET-01, RET-11, RET-14, RET-17, RET-20 |
| `promotion related` | 8 | RET-02, RET-07, RET-08, RET-09, RET-13, RET-14, RET-17, RET-20 |
| `device promotion related` | **1** | RET-05 |
| `save cost` | 4 | RET-03, RET-12, RET-16 (postpaid), RET-16 (tol) |
| `contract end` | 2 | RET-05, RET-20 |
| `sale upsell problem` | **1** | RET-15 |
| `dissatisfied service` | 4 | RET-04, RET-11, RET-15, RET-17 |
| `other` | **1** | RET-10 |
| `post to pre` | **1** | RET-06 |
| `customer reason` | **1** | RET-18 |
| `down sell not success` | **1** | RET-13 |

### `retention_outcome` (scored as `call_result`) — 4 classes, all non-zero

| Class | n | Items |
|---|---:|---|
| `save` | 7 | RET-01, RET-03, RET-07, RET-08, RET-14, RET-15, RET-16 (postpaid) |
| `churn` | 12 | RET-02, RET-04, RET-05, RET-06, RET-09, RET-10, RET-11, RET-12, RET-13, RET-16 (tol), RET-17, RET-18 |
| `unknown` | 2 | RET-16 (tvs), RET-20 |
| `undefined` | **1** | RET-19 |

### `product` — 4 keys, all non-zero

| Key | n |
|---|---:|
| `postpaid` | 16 |
| `tol` | 2 |
| `tvs` | 3 |
| `unknown` | **1** |

### `issue_type` — **out of scope, deliberately absent**

`issue_type` appears in **no** gt row, evidence key or rule in this pack. Three reasons, all
verified against the source:

1. It is **not scored**. `fact_checker.py:603-631` builds the scored row from `call_id`,
   `phone_number`, `call_result`, `product`, `main`, `secondary`, `third` — `issue_type` never
   reaches the metrics (`fact_checker.py:1095-1099`), and `GT_COLUMNS`
   (`testsets.py:108`) has no column for it.
2. It has **no semantics in the app under test**. `config/system_prompt/retention.yml` and
   `src/prompt.py` contain **zero** occurrences of `issue_type`, `FUP`, `Drop`, `Coverage`,
   `Outage` or any disambiguation rule (the two `issue_type` strings in `retention.yml` are
   inside the example JSON). The retention model receives only the bare 8-value enum at
   `main.py:989`. No line in the retention app makes any Thai span decide an `issue_type`, so
   under this pack's own non-negotiable no `issue_type` label can be cited.
3. The `issue_type` definitions and disambiguation rules in `VOCABULARIES.md §4` come from
   `prompt.txt:106-169` — the **MNP** app.

**Consequence, stated plainly:** this pack cannot detect an `issue_type` regression at all.
If `issue_type` is to be evaluated, either the disambiguation text has to be added to the
retention prompt first and cited from there, or `issue_type` has to be scored outside the
production `fact_checker` and documented as a harness-only extra field.

---

## The Thai was DRAFTED BY AN LLM. Native-speaker sign-off is OUTSTANDING.

Every transcript in this pack was written by a language model, not transcribed from a call and
not reviewed by a native Thai speaker. **No naturalness sign-off has been obtained.** Until one
is, treat "the model handled this Thai correctly" as evidence about *this text*, not about
Thai.

What was measured mechanically (all 20 items):

* fillers (`เอ่อ`, `คือ`, `อือ`, `อ๋อ`) — 2 to 12 per item;
* particles (`อ่ะ`, `เนี่ย`, `ไง`, `แหละ`, `นะ`) — 7 to 19 per item;
* `มั้ย` **and** `ไหม` both present in **all 20** items (they were uniform in 12 of 20 before
  the fix pass — a single agent drifting between the two spellings inside one call is the
  spoken-register marker; uniformity is the written-register default);
* self-repair / truncation markers (`...`, `—`) — at least 1 per item;
* no turn exceeds ~20 Thai words (cap is ~25);
* `ค่ะ`/`คะ` confusion present in every item with a female agent. **RET-03 has none** — its
  agent is male and uses `ครับ` throughout, so the marker does not apply.

What is still wrong, and known to be wrong:

* **Dialogue structure is an LLM's idea of a call.** Speaker alternation is close to perfect,
  backchannel turns are rare, and there are only three places in the pack where the line drops
  or speakers overlap (RET-16, RET-18, RET-20). Real calls have holds, repeats, "ขอโทษนะคะ
  ไม่ได้ยินค่ะ", and topic drift. This pack mostly does not.
* **14 of 76 evidence spans (18%) are still literal strings from a production keyword list**
  (down from 47 of 70 before the fix pass). They are concentrated in the `clear` family, where
  a literal span is the point. Listed for review: RET-01 `เน็ตช้า`; RET-02 `โปรโมชั่นหมดแล้ว`,
  `มีโปรโมชั่นถูกกว่านี้ไหม`; RET-03 `ขอยกเลิกรหัสย้ายค่าย`, `อยากลดค่าใช้จ่าย`; RET-04 `รอคิวนาน`;
  RET-05 `พี่จะขอรหัส`; RET-06 `ไม่เอาค่ะ พี่อยากใช้เติมเงิน`, `ไม่อยากใช้รายเดือนแล้ว`,
  `เติมเงินควบคุมง่ายกว่า`; RET-11 `สัญญาณขาดๆหายๆ`; RET-14 `ขอยกเลิกรหัสย้ายค่าย`; RET-18
  `ไม่อยากบอก`; RET-20 `มีโปรโมชั่นถูกกว่านี้ไหม`. To the extent a model scores on those spans by
  lookup rather than comprehension, the pack over-credits it.

---

## Limitation — what this measures, and what it does not

**This testset measures labelling behaviour on synthetic Thai *text*. It does not measure
production accuracy, because production does not read text.**

The Retention app is handed an **audio file** and asked to identify the speakers, transcribe
them, and label the call in one pass (`prompt.py:4314-4315`: "You will receive an audio file
conversation between client and call center agent … to identify who is client, who is agent").
Everything upstream of the labelling decision — diarisation, ASR error on Thai tone marks and
proper nouns, code-switching, crosstalk, telephone-band distortion, silence and hold music — is
absent here and is a large part of where production error actually lives. A model that scores
well on this pack has demonstrated that it can apply `prompt_v9_16`'s label definitions to
clean, correctly-attributed Thai text. That is a necessary condition for production accuracy
and nowhere near a sufficient one.

Two consequences worth stating rather than implying:

* **A score from this pack is not a production accuracy estimate** and must not be reported as
  one. It is a comparison between two models on one controlled dimension.
* **Ranking transfer is unproven.** The model that labels this text better may or may not be
  the model that labels the audio better; nothing here tests that, and the two failure surfaces
  are different.

Additionally: 20 items / 22 rows is a small sample. Six reason classes, one outcome class and
one product key sit at **support 1**, where a single miss swings that class's recall from 1.00
to 0.00. Per-class metrics for those classes cannot separate two models; only the aggregate and
the well-supported classes can.

---

## Verification

```bash
# structural + label contract (the shipped loader and validator)
python -c "import sys; sys.path.insert(0,'src'); \
from evalgen.testsets import load_testset, validate; \
print(validate(load_testset('tests/fixtures/testsets/retention_v1.jsonl', app='retention')))"
```

Expected: `[]`. Anything else means a label has drifted from the text it claims to describe.
**Gate CI on this returning empty** — the four pre-merge blocks shipped without ever being run
through their own loader, and returned 155 problems when they finally were.

Current state, verified: `validate()` → **0 problems**; **76/76** evidence spans are
character-for-character substrings of their own `transcript_th`; UTF-8 with no BOM, LF-only;
`call_id` 5001–5020 and `phone_number` 0810000001–0810000020, all inside the synthetic ranges,
no duplicates; **0** reason spans that occur only on `เจ้าหน้าที่` lines
(`prompt.py:4382-4387` requires the reason phrase to be customer speech).

`retention_v1.jsonl` sha256 `c367a478d89bb047acc1ea5806fc36b75b0b9f561b62a264aa0c2188493b2b0f`
— editing a transcript is the one operation that can silently break the substring check, so
re-run the validator after any edit.

---

## The v2 pack — `retention_v2`

**100 items, 108 ground-truth rows.** Built 2026-08-05. The plan, the arguments made
*against* building it, and the as-built audit are in `docs/testset-v2-plan.md`.

`RET-01` … `RET-20` are the v1 items **copied verbatim**: the first 20 lines of
`retention_v2.jsonl` hash to `c367a478…`, which is the sha256 of the whole of
`retention_v1.jsonl` above. `RET-21` … `RET-100` are new. `retention_v1.*` itself is
untouched, so Experiments 1 and 2 stay reproducible against it.

Same contract, enforced by the same `validate()`: every label owes a byte-exact
`ev_<dim>:<label>` span from its own transcript and a `file:line` `rule_<dim>:<label>`
citation; a reason span must be customer speech; UTF-8, no BOM, LF only.

| | v1 | v2 |
|---|---:|---:|
| items | 20 | 100 |
| scored rows | 22 | 108 |
| `call_id` | 5001–5020 | 5001–5100 |
| `phone_number` | `0810000001`–`0810000020` | `0810000000`–`0810000099` |

**v2 spent the whole `08100000xx` block — all 100 numbers that pattern could spell.** So
`PHONE_PATTERN` was widened to `^0810000[0-9]{3}$` on 2026-08-06
(`src/evalgen/testsets.py:135`). It is one of the three controls keeping customer
identifiers out of git, so that was a deliberate, reviewed change and never a
convenience. The new block is a strict superset of the old one — a digit moved from the
fixed prefix to the variable tail — so every value in the table above still matches, both
packs stay byte-identical, and `validate()` returns the same empty problem list under
either pattern. Capacity is now 1000, `0810000000`–`0810000999`: 100 in use, 900 free for
a v3.

### What it buys: the support-1 classes are gone

The six reason classes sitting at **support 1** in v1 — the ones the Limitation section
above warns cannot separate two models — are gone. Same 11 classes, minimum support **6**:

| Class | v1 | v2 | | Class | v1 | v2 |
|---|---:|---:|---|---|---:|---:|
| `promotion related` | 8 | 16 | | `post to pre` | **1** | 10 |
| `network` | 5 | 14 | | `other` | **1** | 10 |
| `save cost` | 4 | 13 | | `sale upsell problem` | **1** | 10 |
| `dissatisfied service` | 4 | 12 | | `contract end` | 2 | 9 |
| `device promotion related` | **1** | 10 | | `customer reason` | **1** | 8 |
| | | | | `down sell not success` | **1** | 6 |

Counted from `retention_v2.gt.csv` directly, comma-splitting `main`/`secondary`/`third`,
not copied from the build plan.

### Known limitation — the `other` class can be passed by keyword

**Eight of the ten items asserting `other` are flood calls** (`น้ำท่วม`); the remaining two
are the TruePoint/dtac-reward pair. The transcripts are not near-duplicates (max pairwise
6-gram Jaccard 0.19) and their products and outcomes vary, but **a model can score the
entire class by learning "flood → `other`" without learning the class** — the same
lookup-not-comprehension over-crediting the 18%-literal-span note above records for v1,
and this pack exists to avoid it. `prompt.py:4380` enumerates two further `other` cases
(`ลูกค้าอยู่ๆเปลี่ยนใจ ไม่ยกเลิกแล้ว`, and the rewards case) that are untested or barely tested.
**Fix before this class is quoted:** re-voice 2–3 flood items as changed-mind or rewards
variants.

Two further as-built defects are recorded rather than fixed, both in
`docs/testset-v2-plan.md`: one near-duplicate pair (RET-36 / RET-65, 6-gram Jaccard 0.243
against a mean of 0.080) and 55 of 100 transcripts converging on the same closing.

### What v2 does not change

Everything in "The Thai was DRAFTED BY AN LLM" and "Limitation — what this measures, and
what it does not" applies unchanged. Native-speaker sign-off is still outstanding, the
Thai is still an LLM's idea of a Thai call, production is still handed **audio** while
this pack is handed clean pre-tagged text, and `RECONCILED` is still `NO`. Five times the
items does not touch any of those.

### Verification

```bash
# structural + label contract
python -c "import sys; sys.path.insert(0,'src'); \
from evalgen.testsets import load_testset, validate; \
print(validate(load_testset('tests/fixtures/testsets/retention_v2.jsonl', app='retention')))"

# pack-level check: testset, ground truth and prompt together. No key, no network, no cost.
python scripts/evalgen.py check \
    --testset tests/fixtures/testsets/retention_v2.jsonl \
    --gt tests/fixtures/testsets/retention_v2.gt.csv
```

Expected: `[]`, then `OK. No problem found.` over **100 items / 108 rows** and families
`clear=30, thai_linguistic=30, tiebreak=17, multislot=10, escape=13`. Both were run at
the time this section was written.

Recorded in `docs/testset-v2-plan.md` and verified independently of the authoring agents:
100 unique ids, `call_id`s and `phone_number`s; **369 evidence spans all byte-exact, every
reason span on a `ลูกค้า:` line**; 0 of 1,807 turns over the 120-character limit;
`retention_v1.*` byte-identical to HEAD.

`retention_v2.jsonl` sha256 `9c91b036b7b4f102bc3683ea1a73050597a62677d40294b12bad32da844cc039`.
The same warning applies as to v1: editing a transcript is the one operation that can
silently break the substring check, so re-run the validator after any edit.

---

## The v3 pack — `retention_v3`

**138 items, 150 ground-truth rows.** Built 2026-08-06. `RET-01` … `RET-100` are v2
copied verbatim: the first 100 lines of `retention_v3.jsonl` hash to `9c91b036…`, the
sha256 of the whole of `retention_v2.jsonl` above. `retention_v1.*` and `retention_v2.*`
are untouched, so Experiments 1-4 stay reproducible against them.

Same contract, same `validate()`, same encoding rules as v1 and v2.

| | v1 | v2 | v3 |
|---|---:|---:|---:|
| items | 20 | 100 | 138 |
| scored rows | 22 | 108 | 150 |
| `call_id` | 5001–5020 | 5001–5100 | 5001–5100, 5101–5138 |
| `phone_number` | `0810000001`–`0810000020` | `0810000000`–`0810000099` | + `0810000101`–`0810000138` |

`RET-101` … `RET-138` use `call_id = 5000+n`, `phone_number = "0810000" + f"{n:03d}"`,
asserted as an invariant by `test_testset_pack.py` rather than left to convention.

### Four new families — everything v1/v2 had zero coverage of

| Family | Items | n | What it stresses |
|---|---|---:|---|
| `long_context` | RET-101 … RET-112 | 12 | Six Experiment-3 items (both arms correct on all 3 replicates) dilated to 3x and 10x by inserting label-inert filler, screened against the reason-trigger vocabulary at `prompt.py:4330-4381`. Every licensing turn stays byte-identical and in order, so every `ev_*` span and `rule_*` citation carries over free; `gt` is identical to the base. Grows turn *count*, not turn length — the 120-char cap is never the constraint. Stops at 10x (~11,000 chars) because that is the point the transcript's own tokens first exceed the system prompt's. |
| `asr_noise` | RET-113 … RET-122 | 10 | One ASR-shaped artifact class per item: tone-mark loss, missing word spaces, Thai vs Arabic numerals, proper-noun mangling, homophones, stutter, mid-turn truncation, speaker-label leakage, plus RET-11's `เเ`/U+200B artifacts as controls (RET-121, RET-122). Governing expectation, hand-derived from raw codepoints with nothing from `evidence.py` imported: [`ASR-EXPECTATION.md`](./ASR-EXPECTATION.md). Written twice — the first version's own producing script had imported the code it existed to check independently; that failure is recorded in the replacement document rather than quietly redone. |
| `code_switch` | RET-123 … RET-132 | 10 | English product/package terms mid-Thai sentence, at varying density. |
| `regression` | RET-133 … RET-138 | 6 | Named tripwires read per item, never as a family verdict: the RET-11 shape (dropped-reason), two `other` routes that do not depend on the flood keyword (changed-mind, rewards), the discount/down-sell boundary now unblocked by `VOCABULARIES.md` Rule A, a bare price complaint, and a `#2` corroborating-evidence key form. |

### Known limitation — this is a budget overrun against the pre-registered plan, on purpose

`docs/eval-improvement-plan.md:158` pre-registered **+8 to 12 items** for the next
authoring pass; this added 38. The justification is a different sizing rule, not a
relaxed one: that budget was sized to buy discriminating power on dimensions the pack
already covered, while v3 buys first coverage of four it did not cover at all. Recorded
here, before the fact, rather than argued after it — matching how the phone-block
widening above is a reviewed control change and not a convenience.

**v3 inherits every v1/v2 defect it did not have a reason to fix**, because fixing one
would break the prefix-sha invariant the paired `long_context` design depends on: RET-85's
duplicated evidence span (ships on a **dated** allowlist in `test_testset_pack.py`,
compared in both directions so a fix or a new violation both fail loudly), the RET-36/
RET-65 near-duplicate pair, 55 of 100 v2 transcripts converging on the same closing, and
8 of 10 v2 `other` items keying off the flood keyword.

### What v3 does not change

Everything in "The Thai was DRAFTED BY AN LLM", "Limitation — what this measures, and
what it does not", and "What v2 does not change" applies unchanged, to every item
including the new ones: no native-speaker sign-off, production is still handed audio,
`RECONCILED` is still `NO`. **`long_context` does not test the context window** — every
candidate model has 262k+ tokens available — it tests whether the decisive phrase is
still findable once the call is longer, which is a different question. **`asr_noise`
imitates the shape of transcription error; it does not exercise the transcription
stage itself**, since production does ASR and labelling in one pass from audio and this
pack still starts from clean pre-tagged text.

### Verification

```bash
# structural + label contract
python -c "import sys; sys.path.insert(0,'src'); \
from evalgen.testsets import load_testset, validate; \
print(validate(load_testset('tests/fixtures/testsets/retention_v3.jsonl', app='retention')))"

# pack-level check: testset, ground truth and prompt together. No key, no network, no cost.
python scripts/evalgen.py check \
    --testset tests/fixtures/testsets/retention_v3.jsonl \
    --gt tests/fixtures/testsets/retention_v3.gt.csv
```

Expected: `[]`, then `OK. No problem found.` over **138 items / 150 rows** and families
`clear=30, thai_linguistic=30, tiebreak=17, multislot=10, escape=13, long_context=12,
asr_noise=10, code_switch=10, regression=6`. Both were run at the time this section was
written.

Verified independently of the authoring agents, by `test_testset_pack.py` and by hand:
138 unique ids, 138 unique `call_id`s and `phone_number`s; 518 evidence keys, all
byte-exact substrings of their own transcript; every reason span on a customer-attributed
line; 0 turns over the 120-character limit (max observed 107); `retention_v1.*` and
`retention_v2.*` byte-identical to HEAD; first 100 lines of `retention_v3.jsonl` hash to
`retention_v2.jsonl`'s own sha256.

`retention_v3.jsonl` sha256 `ff7f728ca597795ea93497c244ccfb04e0c0846a1e5355662013b82456ad1dff`.
The same warning applies as to v1 and v2: editing a transcript is the one operation that
can silently break the substring check, so re-run the validator after any edit.

---

## The separate challenge pack — `retention_challenge_v1`

**50 original synthetic calls, 64 scored product rows.** This pack is deliberately not
named `retention_v4`: Experiments 1–7 cite the cumulative `RET-*` assets, while this pack
uses `RTC-001` … `RTC-050` and new synthetic keys (`5201` … `5250`) so results cannot be
mistaken for an extension of those frozen bytes.

Every call has 18 customer/agent turns. Five families of ten cover compound history,
negotiation reversals, multiple products, interaction noise and outcome boundaries.
Eleven calls have multiple product rows and three have three product rows. All four
products, all four outcomes and all eleven reasons are represented; each reason appears
in at least three distinct calls. All evidence spans are unique within their transcript,
and reason evidence is customer speech.

The research and label-first design matrix are in
[`docs/retention-challenge-v1-plan.md`](../../../docs/retention-challenge-v1-plan.md).
Public sources informed scenario selection and dialogue phenomena only. The committed
retention prompt remains the label authority through each item's `rule_*` citations.

```bash
.venv/Scripts/python scripts/evalgen.py check \
    --testset tests/fixtures/testsets/retention_challenge_v1.jsonl \
    --gt tests/fixtures/testsets/retention_challenge_v1.gt.csv

.venv/Scripts/python -m pytest tests/test_retention_challenge_pack.py -q
```

This is still synthetic Thai text with no native-speaker sign-off and no audio. It does
not measure ASR, diarisation, crosstalk or production accuracy, and every result remains
`RECONCILED: NO` until the repository's normal live-reference requirement is met.
