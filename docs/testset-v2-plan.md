# Test set v2 — 100 items, and what it can and cannot buy

**Written:** 2026-08-05
**Requested by:** the owner, after the expansion critique. Proceeding on their call.

## Context

Three independent critiques argued against expanding this pack. The owner has heard them
and directed expansion anyway; this is their project and their decision. What follows is
the build, executed with the mitigations those critiques identified rather than in spite
of them.

**Recorded so the resulting numbers are read correctly**, not to reopen the decision:

1. **The mechanism table will probably get worse, not better.** Its verdict rule is FAIL
   if *any* item in a group fails on every replicate — monotone decreasing in group size.
   `multislot` goes 2 -> 10 items; a row that currently reads FAIL/PASS may well go
   FAIL/FAIL, which is *less* discriminating than today.
2. **The pre-registered verdict bands do not survive the change.** They are absolute
   counts (`>= +6` AHEAD) calibrated to 22 scored rows. At ~110 rows the same discordance
   rate makes a non-INDISTINGUISHABLE verdict roughly 2.5x more likely from noise alone.
   **Task 0 re-derives them before a single item is authored** — deriving them afterwards
   would be choosing the rule to fit the result.
3. **Semantic coverage will barely move.** The existing 81 citations resolve to 31
   distinct production lines, 28 of them inside `prompt.py:4321-4399`. That ~80-line
   region *is* the retention rule set. New items re-cite rules already covered — they add
   surface variety, not new rules under test.
4. **This is not a holdout.** Same author, same procedure, same head. Errors will
   correlate with the existing pack rather than being independent of it.
5. **Native-speaker sign-off remains outstanding.** It was outstanding for 20 items and
   will be outstanding for 100. The Thai is an LLM's idea of a Thai call.

**What expansion genuinely does buy:** more items in the discordant cells that per-dimension
`net` and McNemar actually read; the six support-1 reason classes lifted to a support where
a single miss no longer swings recall from 1.00 to 0.00; and far more model output to
inspect, which is what was asked for.

## Scope

- **New file** `tests/fixtures/testsets/retention_v2.jsonl` + `retention_v2.gt.csv`.
  `retention_v1.*` is **frozen and untouched**, so Experiments 1 and 2 stay reproducible.
- Items `RET-01`..`RET-020` are the v1 items, copied verbatim. `RET-21`..`RET-100` are new.
- The `RET-` prefix is mandatory: `export_xlsx.verify()` asserts every id starts with it.

### Identifier allocation (both blocks are near-exhausted afterwards)

| | v1 (in use) | v2 new | Remaining after |
|---|---|---|---|
| `call_id` `^5[0-9]{3}$` | 5001-5020 | 5021-5099, 5100 | 899 |
| `phone` `^08100000[0-9]{2}$` *(the block as it stood)* | ...01-...20 | ...21-...99, ...00 | **0** |

`RET-100` takes `0810000000` and `5100`. **The phone block was fully consumed at 100
items.** Any 101st item required widening `PHONE_PATTERN`, which is one of the three
controls keeping customer identifiers out of git — a deliberate, reviewed change, never a
convenience.

**That widening happened, 2026-08-06**: `^08100000[0-9]{2}$` → `^0810000[0-9]{3}$`
(`src/evalgen/testsets.py:135`), reviewed as the control change this section said it
would have to be. One digit moves from the fixed prefix to the variable tail, which makes
the new block a strict superset: every number allocated in the table above still matches,
so no file listed here changed and `validate()` returns the same empty problem list on
`retention_v1.jsonl` and `retention_v2.jsonl` under either pattern. Capacity is now 1000
— `0810000000`–`0810000999` — with the same 100 in use and 900 free. The `call_id` row is
untouched and still has 899.

### Family distribution

| Family | v1 | new | v2 total |
|---|---:|---:|---:|
| `clear` | 6 | 24 | 30 |
| `thai_linguistic` | 6 | 24 | 30 |
| `tiebreak` | 3 | 14 | 17 |
| `multislot` | 2 | 8 | 10 |
| `escape` | 3 | 10 | 13 |
| **Total** | **20** | **80** | **100** |

Proportions are held roughly constant rather than skewed toward the support-1 classes.
Those classes sit in the `both_wrong` cell — both arms already fail them deterministically
— and piling items there grows a cell that contributes **zero** discriminating power.

## The item contract every new item must satisfy

Enforced by `testsets.validate()`; an item failing any of these is not shippable.

- 11 fields exactly, no more, no fewer. Unknown keys are a hard load error.
- `transcript_th`: spoken-register Thai, 15-20 speaker-prefixed turns, **every turn <= 120
  characters**, `เจ้าหน้าที่:` / `ลูกค้า:` prefixes, fillers and particles present.
- `gt`: one row per product in the call; all five `GT_COLUMNS` written explicitly, `""`
  for absent.
- **Every label owes `ev_<dim>:<label>`** — a *byte-exact substring of that item's own
  transcript*.
- **Every label owes `rule_<dim>:<label>`** — a citation matching
  `^[A-Za-z0-9_][A-Za-z0-9_./-]*\.(py|txt|ya?ml|md):\d+(-\d+)?$` into production source.
- Reason cells are comma-split: `"network, save cost"` is **two** labels owing two
  evidence keys and two rule keys.
- A reason span must be **customer speech** (`prompt.py:4382-4387`), not agent speech.
- Labels come only from the closed retention space (`labelspaces.py`).
- File stays UTF-8, **no BOM**, **LF only**. A single CR makes `load_testset` refuse.

## Done criteria

1. `retention_v2.jsonl` holds exactly 100 items, `RET-01`..`RET-100`, ids and phones unique.
2. `evalgen check --testset retention_v2.jsonl --gt retention_v2.gt.csv` reports **0 problems**.
3. Every gt row has a matching CSV row under `cli._gt_disagreements`, and no CSV row is unclaimed.
4. The full suite stays green in **both** modes — v1 tests must not move, since v1 did not.
5. Both models run over all 100 items; per-call input tokens, output tokens, cost and
   latency land in the workbook's `Per call` sheet and the response in `run.jsonl`.

## Verification protocol

Unchanged from `CLAUDE.md`'s Build and Verification Contract. In particular: **no test
expectation is edited to make a run pass**, and the v1 fixtures are not touched at all.

## Cost

100 items x 3 replicates x 2 models = **600 calls**. At the measured ~$0.0011/call that is
roughly **$0.70**. Three replicates, not five: five costs 67% more and, as Experiment 2
established, improves only `N_flip` and the mechanism verdicts — the aggregate table is
scored on replicate 1 regardless.

---

## As-built: what shipped, and what is wrong with it

Verified independently of the authoring agents: 100 items, `validate()` 0 problems,
`evalgen check` 0 problems over 108 gt rows, 100 unique ids / call_ids / phone_numbers,
**369 evidence spans all byte-exact and every reason span on a `ลูกค้า:` line**, 0 of
1,807 turns over the 120-character limit, `retention_v1.*` byte-identical to HEAD.

Reason-class support went from six classes at n=1 to a minimum support of 6:

| class | v1 | v2 | | class | v1 | v2 |
|---|---:|---:|---|---|---:|---:|
| promotion related | 8 | 16 | | post to pre | 1 | 10 |
| network | 5 | 14 | | other | 1 | 10 |
| save cost | 4 | 13 | | sale upsell problem | 1 | 10 |
| dissatisfied service | 4 | 12 | | contract end | 2 | 9 |
| device promotion related | 1 | 10 | | customer reason | 1 | 8 |
| | | | | down sell not success | 1 | 6 |

### Known limitations — found by adversarial review, recorded rather than fixed

**1. The `other` class is 80% one scenario, and can be passed by keyword.** Eight of the
ten items asserting `other` are flood calls (`น้ำท่วม`); the remaining two are the
TruePoint/dtac-reward pair. The transcripts are not near-duplicates (max pairwise 6-gram
Jaccard 0.19) and their products and outcomes vary, but **a model can score the entire
class by learning "flood -> other" without learning the class**, which is exactly the
lookup-not-comprehension failure this pack is supposed to avoid. `prompt.py:4380`
enumerates two further cases (`ลูกค้าอยู่ๆเปลี่ยนใจ ไม่ยกเลิกแล้ว`, and the rewards case)
that are untested or barely tested. **Fix before this class is quoted: re-voice 2-3 flood
items as changed-mind or rewards variants.**

**2. One near-duplicate pair.** RET-36 and RET-65 share the same
`Postpaid/churn/contract end` triple and several verbatim turns (6-gram Jaccard 0.243,
against a mean of 0.080 across the 80 new items). RET-65 earns its place by testing Thai
numerals and Buddhist Era (`๓๐ เมษายน ๒๕๖๘`), but its dialogue should be re-voiced.

**3. Citation pairing is inconsistent with `VOCABULARIES.md`, matching v1 rather than the
doc.** `VOCABULARIES.md:1090-1097` prescribes a `prompt.py` + `prompt.txt` pairing for
`down sell not success` and for enumerated `other`. All six new `down sell not success`
items cite only the `prompt.py` half — but so do the frozen v1 originals, so the new items
are consistent with precedent and `validate()` only checks citation *form*. Left alone
deliberately: changing the 80 new items would make them inconsistent with the 20 frozen
ones. Resolve for the whole pack or not at all.

**4. Closings converge.** 55 of 100 transcripts end in a `ขอบคุณค่ะ/ครับ` variant.
Harmless today — no evidence span sits on a final turn — but it is a template artifact.

### The limitations that did not change, and cannot be fixed by authoring

Native-speaker sign-off is still outstanding. The Thai is still an LLM's idea of a Thai
call. Production is still handed **audio** while this pack is handed clean pre-tagged
**text**, so diarisation, ASR error and speaker misattribution remain invisible. And
`RECONCILED` is still `NO`. Five times the items does not touch any of these.
