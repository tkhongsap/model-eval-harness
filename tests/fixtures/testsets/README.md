# Retention Thai testset — `retention_v1`

Synthetic Thai call transcripts for comparing two LLMs on True Corp's **Retention**
call-analysis app. Twenty items, twenty-two scored rows, no real customer data.

| File | What it is |
|---|---|
| `retention_v1.jsonl` | The testset. One JSON object per line, `RET-01` … `RET-20`. UTF-8, **no BOM**, LF only. |
| `retention_v1.gt.csv` | Ground truth flattened to the scorer's grain — one row per `(call_id, phone_number, product)`. 22 rows. |
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
