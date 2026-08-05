# Label vocabularies for the Retention testset

**This is the answer key for `rule_<label>` citations.** Every value, every Thai keyword and
every line reference below was transcribed mechanically from the production sources listed
under Provenance. Nothing here is paraphrased or invented. To justify a ground-truth label,
cite a line from this document and you are citing production.

**Scope.** Retention call analysis only. MNP, RTR, Sentiment QA and Telesales reuse some of
these names with different meanings; do not carry a citation across apps.

**How to use it.** Each label on a testset item needs two things:

1. `ev_<label>` — a **verbatim substring of that item's own transcript**.
2. `rule_<label>` — a `file:line` citation from this document under which that span makes the
   label follow.

If no line below makes the label follow from the span, the item is under-specified.
**Rewrite the transcript until a rule applies. Do not argue the label.**

---

## Provenance

| Short name | Path | Lines | SHA-256 (first 16) |
|---|---|---:|---|
| `main.py` | `sentiment-batch-retention-main/src/main.py` | 1375 | `09cb5c7da606092a` |
| `fact_checker.py` | `sentiment-batch-retention-main/src/modules/fact_checker.py` | 1597 | `b35152d99c4ea294` |
| `prompt.py` | `sentiment-batch-retention-main/src/prompt.py` | 4709 | `66b0ae43a04ac58a` |
| `system_prompt/retention.yml` | `sentiment-batch-retention-main/config/system_prompt/retention.yml` | 95 | `f41a208b32456d04` |
| `fact_checker/retention.yml` | `sentiment-batch-retention-main/config/fact_checker/retention.yml` | 91 | `d954402f92be9624` |
| `prompt.txt` | `sentiment-batch-mnp-develop/config/system_prompt/prompt.txt` | 260 | `266b0c9cb1e39922` |

All six files are **read-only production source**. Line numbers below are valid only against
these digests. If a digest changes, re-derive this document before trusting a citation.

### Which file wins

**The retention pipeline's *user* prompt is not in the repository.** `fact_checker/retention.yml:35-39`
fetches it from SharePoint at run time:

```yaml
user_prompt_path:
input:
type: sharepoint
site_name: AIandAutomationTeamControlManagement
path: /Control Management/Call Center/Sentiment Analysis Retention Reason/prompt_control/sentiment/prompt.txt
```

So the two prompt files available locally are both proxies:

- **`prompt.txt`** — the **MNP** app's prompt. It is the only file anywhere with per-reason
  Thai keyword lists, which is why it is the keyword authority here. But it belongs to a
  sibling app and diverges from the retention schema in five places (D1, D3, D5, D6, and the
  field name `call_result` vs `retention_outcome`).
- **`prompt.py`** — a **historical** stack of 30 retention prompt versions (`prompt_v1` at
  `:1` through `prompt_v9_16` at `:4313`, plus `prompt_tar_v8` at `:4476`). Nothing imports
  it. `prompt_v9_16` is the newest and is the closest local approximation of what production
  runs today.

Precedence, in order:

1. **`main.py` `get_analysis_schema()` (`:955-1056`) decides what a model may emit.** It is a
   Vertex AI function-calling schema; its `enum` lists are hard constraints.
2. **`fact_checker.py` `target_classes` decides what is actually measured.** A value outside
   these lists scores as nothing at all.
3. **`prompt.py:4313-4473` (`prompt_v9_16`) decides retention semantics** — what a span
   *means* for a retention call.
4. **`prompt.txt` supplies the Thai evidence**, and only that.

Every conflict between these is recorded in the divergence register at the end.

---

## Grain, and which field carries which label

The scored row is **`(call_id, phone_number, product)`** — one row per product mentioned in
the call, not one row per call.

| Testset field | Model JSON key | Scored column | Schema | Scorer |
|---|---|---|---|---|
| `reason` (main / secondary / third) | `product.<Key>.<rank>.reason` | `main` / `secondary` / `third` | `main.py:970-974` | `fact_checker.py:857-869` |
| `retention_outcome` | `product.<Key>.retention_outcome` | **`call_result`** | `main.py:1016-1020` | `fact_checker.py:791` |
| `product` | the **keys** of the `product` object | `product` | `main.py:1033-1043` | `fact_checker.py:971-973` |
| `issue_type` | `product.<Key>.network_issue.issue_type` | not scored | `main.py:987-990` | — |
| `churn_probability` | `product.<Key>.network_issue.churn_probability` | not scored | `main.py:996` | — |
| `call_event_detection` | top-level `call_event_detection` | not scored | `main.py:1044-1048` | — |

**The rename is real.** The model emits `retention_outcome`; the scorer reads a column called
`call_result`. One line bridges them:

**`fact_checker.py:622`**

```python
'call_result': product_result.get("retention_outcome", ""),
```

`rule_retention_outcome` may cite either name — they are the same label.

Only three dimensions are scored (`fact_checker.py:1095-1099`). `issue_type`,
`churn_probability` and `call_event_detection` still belong in the testset — they are what the
Retention app hands to the network team — but their `rule_*` citations are documentation, not
a gate.

---

# 1. `reason` — 11 values

## The authoritative list

Schema enum, the closed set a model may emit:

**`main.py:972`**

```python
"enum": ["network", "promotion related", "device promotion related", "save cost", "contract end", "sale upsell problem", "dissatisfied service", "post to pre", "customer reason", "down sell not success", "other"],
```

Scorer `target_classes`, the set that gets measured:

```python
  857          target_classes = [
  858              'network',
  859              'promotion related',
  860              'device promotion related',
  861              'save cost',
  862              'contract end',
  863              'sale upsell problem',
  864              'dissatisfied service',
  865              'other',
  866              'post to pre',
  867              'customer reason',
  868              'down sell not success'
  869          ]
```

**Same 11 strings, different order.** The order differs a third time in the schema's own prose:

**`main.py:964`**

```python
REASON_CATEGORIES = "network, promotion related, device promotion related, save cost, contract end, sale upsell problem, dissatisfied service, other, post to pre, customer reason, down sell not success"
```

Order never reaches a metric — `get_reasons_set` builds a set — but it does defeat any
citation that names a reason by position. See **D12**.

## Scorer mechanics that change what a `reason` label means

```python
  871          def get_reasons_set(row, suffix):
  872              reasons = set()
  873              for col in ['main', 'secondary', 'third']:
  874                  val = row.get(f'{col}{suffix}')
  875                  if pd.notna(val) and str(val).strip() != '':
  876                      # Split by comma if multiple reasons are in one cell
  877                      parts = [p.strip().lower() for p in str(val).split(',')]
  878                      reasons.update(parts)
  879              return reasons
```

Three consequences a testset author must design around:

1. **Rank is discarded.** `main`, `secondary` and `third` are unioned into one set
   (`:873-878`). Ground truth `main = network, secondary = save cost` versus a prediction of
   `main = save cost, secondary = network` scores as a **perfect match**. Never build an item
   whose only distinguishing feature is rank. (**D9**)
2. **Cells are comma-split** (`:877`). A cell containing `"network, save cost"` becomes two
   labels. This is intended for multi-label cells — and it is also why the 12th, prompt-only
   reason is structurally unscorable. (**D1**, **D10**)
3. **Each cell is lowercased on its own line** (`:877`), independently of `pre_process`. The
   reason dimension therefore survives case drift even where `call_result` does not.

## The 11 values

Each entry gives the exact string, where the schema and scorer declare it, the MNP definition
and keyword lines, the current retention definition, and the individual evidence spans.

**Any span in an "Evidence spans" list is a legitimate `ev_reason`** provided it appears
verbatim in that item's own transcript.

### 1.1 `network`

| Where | Citation |
|---|---|
| Schema enum | `main.py:972` |
| Scorer class | `fact_checker.py:858` |
| MNP entry | `prompt.txt:2` (numbered #1) |
| MNP definition | `prompt.txt:3` |
| MNP keywords | `prompt.txt:4` |
| Retention definition | `prompt.py:4330` |

**`prompt.txt:3`**

```text
- Definition: Issues related to network quality, coverage, speed, or connectivity.
```

**`prompt.txt:4`**

```text
- Keywords: เน็ตช้า, สัญญาณไม่เสถียร, ดูวิดีโอแล้วกระตุก, เล่นเกมแล้วหลุด, สัญญาณขาดๆหายๆ, ไฟดับสัญญาณหาย, ดูอะไรไม่ได้เลย, หมุนโหลด, เน็ตกากมาก, หลุดบ่อย, ค้างช้า, ไม่มีคลื่น, เน็ตล่ม, เน็ตไม่เหมือนเดิม, เน็ตกระตุก, ไม่มีสัญญาณ, โทรไม่ได้เลย, สัญญาณแย่มาก, ไม่ค่อยมีสัญญาณ, โทรไม่ติด, gps ไม่เสถียร, ไปเที่ยวไม่มีสัญญาณ
```

**Evidence spans** (`prompt.txt:4`, split on the prompt's own commas):

- `เน็ตช้า`
- `สัญญาณไม่เสถียร`
- `ดูวิดีโอแล้วกระตุก`
- `เล่นเกมแล้วหลุด`
- `สัญญาณขาดๆหายๆ`
- `ไฟดับสัญญาณหาย`
- `ดูอะไรไม่ได้เลย`
- `หมุนโหลด`
- `เน็ตกากมาก`
- `หลุดบ่อย`
- `ค้างช้า`
- `ไม่มีคลื่น`
- `เน็ตล่ม`
- `เน็ตไม่เหมือนเดิม`
- `เน็ตกระตุก`
- `ไม่มีสัญญาณ`
- `โทรไม่ได้เลย`
- `สัญญาณแย่มาก`
- `ไม่ค่อยมีสัญญาณ`
- `โทรไม่ติด`
- `gps ไม่เสถียร`
- `ไปเที่ยวไม่มีสัญญาณ`

Current retention wording (`prompt.py:4330-4330`):

```text
- ปัญหาเกิดจาก internet เช่น เน็ตช้า, เล่นเน็ตไม่ได้, ไม่มีสัญญาณ
```

### 1.2 `promotion related`

| Where | Citation |
|---|---|
| Schema enum | `main.py:972` |
| Scorer class | `fact_checker.py:859` |
| MNP entry | `prompt.txt:6` (numbered #2) |
| MNP definition | `prompt.txt:7` |
| MNP keywords | `prompt.txt:8` |
| Retention definition | `prompt.py:4333-4335` |

**`prompt.txt:7`**

```text
- Definition: Issues related to promotions not as agreed, failed subscription, ended too quickly, or not worthwhile.
```

**`prompt.txt:8`**

```text
- Keywords: สมัครโปรโมชั่นแล้วใช้ไม่ได้, มีโปรโมชั่นถูกกว่านี้ไหม, โปรโมชั่นหมดแล้ว, โปรโมชั่นไม่ตรงตามที่โฆษณา, เน็ตหมดเร็วเกินไป, โปรโมชั่นคู่แข่งดีกว่า, ขอโปรโมชั่นเหมือนค่ายอื่นแล้วไม่ได้หรือไม่มี, อยากได้โปรเดิม, โปรที่ดีกว่า, อยากได้โปรโมชั่นเดิมหรือถูกกว่า, โปรโมชั่นที่ดีกว่าหรือส่วนลดเยอะกว่า, สนใจโปรโมชั่น, ราคาโปรโมชั่นปัจจุบันแพงเกินไป, ส่วนลดโปรโมชั่นเดิมหมดแล้ว, เน็ตไม่พอใช้, เพื่อนได้โปรโมชั่นดีกว่า, อยากใช้โปรโมชั่นเดิม, ไม่ได้โปรโมชั่นตามสื่อโฆษณา, อยากได้โปรโมชั่นเหมือนลูกค้าเปิดเบอร์ใหม่, โปรโมชั่นที่ใช้อยู่ไม่คุ้มค่า, ไม่ดูแลโปรโมชั่นลูกค้าเก่า
```

**Evidence spans** (`prompt.txt:8`, split on the prompt's own commas):

- `สมัครโปรโมชั่นแล้วใช้ไม่ได้`
- `มีโปรโมชั่นถูกกว่านี้ไหม`
- `โปรโมชั่นหมดแล้ว`
- `โปรโมชั่นไม่ตรงตามที่โฆษณา`
- `เน็ตหมดเร็วเกินไป`
- `โปรโมชั่นคู่แข่งดีกว่า`
- `ขอโปรโมชั่นเหมือนค่ายอื่นแล้วไม่ได้หรือไม่มี`
- `อยากได้โปรเดิม`
- `โปรที่ดีกว่า`
- `อยากได้โปรโมชั่นเดิมหรือถูกกว่า`
- `โปรโมชั่นที่ดีกว่าหรือส่วนลดเยอะกว่า`
- `สนใจโปรโมชั่น`
- `ราคาโปรโมชั่นปัจจุบันแพงเกินไป`
- `ส่วนลดโปรโมชั่นเดิมหมดแล้ว`
- `เน็ตไม่พอใช้`
- `เพื่อนได้โปรโมชั่นดีกว่า`
- `อยากใช้โปรโมชั่นเดิม`
- `ไม่ได้โปรโมชั่นตามสื่อโฆษณา`
- `อยากได้โปรโมชั่นเหมือนลูกค้าเปิดเบอร์ใหม่`
- `โปรโมชั่นที่ใช้อยู่ไม่คุ้มค่า`
- `ไม่ดูแลโปรโมชั่นลูกค้าเก่า`

Current retention wording (`prompt.py:4333-4335`):

```text
- ปัญหาเกิดจากตัวโปรโมชัน เช่น **ราคาโปรโมชันแพง**, โปรหมดอายุ, อยากย้ายกลับไปโปรก่อนหน้านี้, แพคเกจราคาสูง, ลูกค้าขอส่วนลด, โปรโมชันมีอินเทอร์เน็ตน้อย
- Exclusions:
- CRITICAL: **คำพูดที่พนักงานเสนอ/แจกแจงโปรโมชันเพื่อยื้อลูกค้า ไม่ถูกนับว่าเป็น promotion related เพราะไม่ใช่ root cause ของปัญหาเป็นแค่ offer**
```

### 1.3 `device promotion related`

| Where | Citation |
|---|---|
| Schema enum | `main.py:972` |
| Scorer class | `fact_checker.py:860` |
| MNP entry | `prompt.txt:10` (numbered #3) |
| MNP definition | `prompt.txt:11` |
| MNP keywords | `prompt.txt:12` |
| Retention definition | `prompt.py:4338-4339` |

**`prompt.txt:11`**

```text
- Definition: Issues related to device promotions (unclear terms, unavailability, colors, high price vs competitors).
```

**`prompt.txt:12`**

```text
- Keywords: โปรโมชั่นเครื่องซ้ำซ้อนไม่เข้าใจ, ไม่มีเครื่องสีที่อยากได้, ค่ายอื่นเครื่องถูกกว่า, ส่วนลดค่าเครื่องของค่ายอื่นเยอะกว่า, ค่ายอื่นรับเครื่องได้เลย, ที่อื่นเครื่องไม่ต้องมัดจำ, เครื่องแพงกว่า, จะซื้อเครื่องแต่โปรที่ต้องใช้ร่วมราคาสูงกว่าค่ายอื่น, ของแถมจากการซื้อเครื่องน้อย, ที่อื่นราคาเครื่องถูกกว่า, ไม่มีเครื่องเลย, รอเครื่องนาน
```

**Evidence spans** (`prompt.txt:12`, split on the prompt's own commas):

- `โปรโมชั่นเครื่องซ้ำซ้อนไม่เข้าใจ`
- `ไม่มีเครื่องสีที่อยากได้`
- `ค่ายอื่นเครื่องถูกกว่า`
- `ส่วนลดค่าเครื่องของค่ายอื่นเยอะกว่า`
- `ค่ายอื่นรับเครื่องได้เลย`
- `ที่อื่นเครื่องไม่ต้องมัดจำ`
- `เครื่องแพงกว่า`
- `จะซื้อเครื่องแต่โปรที่ต้องใช้ร่วมราคาสูงกว่าค่ายอื่น`
- `ของแถมจากการซื้อเครื่องน้อย`
- `ที่อื่นราคาเครื่องถูกกว่า`
- `ไม่มีเครื่องเลย`
- `รอเครื่องนาน`

Current retention wording (`prompt.py:4338-4339`):

```text
- ปัญหาเกี่ยวกับโปรโมชันผูกเครื่อง เช่น ซื้อโปรผูกเครื่องเลยจะยกเลิก, ไม่มีเครื่อง ไม่มีรุ่น, อุปกรณ์ชำรุด สูญหาย, ซื้อเครื่องผูกโปรเบอร์เดิม
- ซื้อโทรศัพท์ใหม่ ย้ายค่ายเบอร์เดิม
```

### 1.4 `save cost`

| Where | Citation |
|---|---|
| Schema enum | `main.py:972` |
| Scorer class | `fact_checker.py:861` |
| MNP entry | `prompt.txt:14` (numbered #4) |
| MNP definition | `prompt.txt:15` |
| MNP keywords | `prompt.txt:16` |
| Retention definition | `prompt.py:4342-4345` |

**`prompt.txt:15`**

```text
- Definition: Issues related to direct costs where customers want to reduce expenses explicitly.
```

**`prompt.txt:16`**

```text
- Keywords: อยากลดค่าใช้จ่าย, อยากยกเลิกบางบริการเพื่อลดค่าใช้จ่าย, ค่าใช้จ่ายสูงเกินทิ่คิดไว้, ต้องการราคาถูกลง, ประหยัดค่าใช้จ่าย, ค่าใช้จ่ายรายเดือนสูง
```

**Evidence spans** (`prompt.txt:16`, split on the prompt's own commas):

- `อยากลดค่าใช้จ่าย`
- `อยากยกเลิกบางบริการเพื่อลดค่าใช้จ่าย`
- `ค่าใช้จ่ายสูงเกินทิ่คิดไว้`
- `ต้องการราคาถูกลง`
- `ประหยัดค่าใช้จ่าย`
- `ค่าใช้จ่ายรายเดือนสูง`

Current retention wording (`prompt.py:4342-4345`):

```text
- ลูกค้าไม่ได้ใช้งานแล้ว, ย้านบ้าน, ไปต่างประเทศ, หรือ พูดออกมาในทำนองที่ว่า **ต้องการลดค่าใช้จ่าย**
- Exclusions:
- CRITICAL: **คำพูดที่พนักงานเสนอโปรโมชันเพื่อยื้อลูกค้า ไม่ถูกนับว่าเป็น save cost**
- CRITICAL: **การที่ลูกค้าขอลดราคาโปรโมชันหรืออยากได้โปรถูก ยังไม่ใช่ save cost ต้องแจ้งว่าอยากลดค่าใช้จ่ายด้วย**
```

### 1.5 `contract end`

| Where | Citation |
|---|---|
| Schema enum | `main.py:972` |
| Scorer class | `fact_checker.py:862` |
| MNP entry | `prompt.txt:18` (numbered #5) |
| MNP definition | `prompt.txt:19` |
| MNP keywords | `prompt.txt:20` |
| Retention definition | `prompt.py:4348-4351` |

**`prompt.txt:19`**

```text
- Definition: Issues related to customers whose contracts have ended and wish to make changes/cancel.
```

**`prompt.txt:20`**

```text
- Keywords: หมดสัญญาแล้ว, โปรโมชั่นที่ใช้อยู่หมดสัญญาแล้ว, เบอร์นี้ไม่ได้ใช้เป็นเบอร์หลัก, ย้ายค่ายมาซื้อเครื่องครบสัญญาแล้ว, ไม่อยากต่อสัญญา, ครบสัญญา, เบอร์นี้ติดสัญญาซื้อเครื่องแต่ครบแล้ว, ไม่ใช่เบอร์หลัก, เปิดเบอร์นี้เพราะซื้อเครื่องตอนนี้หมดสัญญา
```

**Evidence spans** (`prompt.txt:20`, split on the prompt's own commas):

- `หมดสัญญาแล้ว`
- `โปรโมชั่นที่ใช้อยู่หมดสัญญาแล้ว`
- `เบอร์นี้ไม่ได้ใช้เป็นเบอร์หลัก`
- `ย้ายค่ายมาซื้อเครื่องครบสัญญาแล้ว`
- `ไม่อยากต่อสัญญา`
- `ครบสัญญา`
- `เบอร์นี้ติดสัญญาซื้อเครื่องแต่ครบแล้ว`
- `ไม่ใช่เบอร์หลัก`
- `เปิดเบอร์นี้เพราะซื้อเครื่องตอนนี้หมดสัญญา`

Current retention wording (`prompt.py:4348-4351`):

```text
- ลูกค้าแจ้งว่าหมดสัญญา ใช้ในกรณีที่เป็น โปรโมชันผูกเครื่อง หรือ สัญญาเบอร์สวย
- Exclusion:
- It is not contract end if the agent mentions the client is still under contract as a defense/explanation.
- The client or the agent merely mentions the length of usage (e.g., "I've been using this for 5 years," "ใช้มา 5 ปีแล้ว") without explicitly stating that the contract has officially ended and that is the reason for cancellation.
```

### 1.6 `sale upsell problem`

| Where | Citation |
|---|---|
| Schema enum | `main.py:972` |
| Scorer class | `fact_checker.py:863` |
| MNP entry | `prompt.txt:22` (numbered #6) |
| MNP definition | `prompt.txt:23` |
| MNP keywords | `prompt.txt:24` |
| Retention definition | `prompt.py:4354-4357` |

**`prompt.txt:23`**

```text
- Definition: Unwanted upselling, forced activation, or misunderstanding of terms caused by agents.
```

**`prompt.txt:24`**

```text
- Keywords: โดนบังคับสมัครโปร, ไม่เคยขอแต่โดนเพิ่มบริการ, ขายเกินจริง, ลูกค้ายังไม่ตอบรับเลยเพิ่มให้พี่แล้ว, เสนอโปรโมชั่นที่แพง, ยังไม่ตอบตกลง, ไม่ตรงตามที่แจ้ง, พนักงานบอกโปรหมดอายุ, เข้าใจว่าถ้าไม่เปิดเบอร์ยังไม่มีค่าบริการ, เจ้าหน้าที่บอกว่าซิมฟรี, ไม่ได้สมัครเลย
```

**Evidence spans** (`prompt.txt:24`, split on the prompt's own commas):

- `โดนบังคับสมัครโปร`
- `ไม่เคยขอแต่โดนเพิ่มบริการ`
- `ขายเกินจริง`
- `ลูกค้ายังไม่ตอบรับเลยเพิ่มให้พี่แล้ว`
- `เสนอโปรโมชั่นที่แพง`
- `ยังไม่ตอบตกลง`
- `ไม่ตรงตามที่แจ้ง`
- `พนักงานบอกโปรหมดอายุ`
- `เข้าใจว่าถ้าไม่เปิดเบอร์ยังไม่มีค่าบริการ`
- `เจ้าหน้าที่บอกว่าซิมฟรี`
- `ไม่ได้สมัครเลย`

Current retention wording (`prompt.py:4354-4357`):

```text
- ปัญหาจากการขายเพิ่ม เช่น พนักงานเสนอโปรหรือบริการที่ลูกค้าไม่ต้องการ หรือไม่เข้าใจเงื่อนไข, ลูกค้าโดนบังคับสมัคร, ลูกค้ายังไม่ตอบรับเลยแต่สมัครให้แล้ว, โปรปรับขึ้นอัตโนมัติโดยลูกค้าไม่รู้, มีแพคเกจเสริมเข้ามาโดยไม่ได้กด
- ลูกค้าแจ้งว่าพนักงานบอกราคาโปรแบบหนึ่ง แต่พอเรียกเก็บกลับเป็นอีกราคาหนึ่ง
- โปรโมชันไม่ตรงตามที่พนักงานแจ้ง, ไม่เหมือนที่คุยกันไว้
- ไม่ได้ใช้งานแต่มียอดค้างชำระ
```

### 1.7 `dissatisfied service`

| Where | Citation |
|---|---|
| Schema enum | `main.py:972` |
| Scorer class | `fact_checker.py:864` |
| MNP entry | `prompt.txt:26` (numbered #7) |
| MNP definition | `prompt.txt:27` |
| MNP keywords | `prompt.txt:28` |
| Retention definition | `prompt.py:4360-4364` |

**`prompt.txt:27`**

```text
- Definition: Poor customer service experience (rude staff, slow response, unhelpful).
```

**`prompt.txt:28`**

```text
- Keywords: พนักงานพูดไม่ดี, พนักงานไม่ช่วยอะไรเลย, รอนานมาก, บริการแย่, ไม่ใส่ใจลูกค้า, ไม่พอใจบริการของคนขาย, ไม่พอใจบริการ Call Center, ไม่พอใจบริการ Shop, ติดต่อ call center ยาก, ไม่ดูแลลูกค้า, ที่นี่ไม่มีศูนย์แล้ว, ใช้งานมาตั้งนานพอจะย้ายค่ายก็มาให้โปรโมชั่นถูก, ไม่ดูแล, ถูกหลอก, รอคิวนาน, วิดีโอคอลรอนาน, พนักงานพูดไม่รู้เรื่อง, รอสายนาน, เจอแต่มะลิ, ไม่เจอคนเลย, สาขาไม่ทำให้, แก้ไขช้า
```

**Evidence spans** (`prompt.txt:28`, split on the prompt's own commas):

- `พนักงานพูดไม่ดี`
- `พนักงานไม่ช่วยอะไรเลย`
- `รอนานมาก`
- `บริการแย่`
- `ไม่ใส่ใจลูกค้า`
- `ไม่พอใจบริการของคนขาย`
- `ไม่พอใจบริการ Call Center`
- `ไม่พอใจบริการ Shop`
- `ติดต่อ call center ยาก`
- `ไม่ดูแลลูกค้า`
- `ที่นี่ไม่มีศูนย์แล้ว`
- `ใช้งานมาตั้งนานพอจะย้ายค่ายก็มาให้โปรโมชั่นถูก`
- `ไม่ดูแล`
- `ถูกหลอก`
- `รอคิวนาน`
- `วิดีโอคอลรอนาน`
- `พนักงานพูดไม่รู้เรื่อง`
- `รอสายนาน`
- `เจอแต่มะลิ`
- `ไม่เจอคนเลย`
- `สาขาไม่ทำให้`
- `แก้ไขช้า`

Current retention wording (`prompt.py:4360-4364`):

```text
- ลูกค้าแจ้งว่าสาเหตุเป็นเพราะ ความไม่พึงพอใจต่อการให้บริการของหนักงาน เช่น การตอบช้า ไม่ช่วยแก้ปัญหา หรือพนักงานพูดไม่ดี, ลูกค้าร้องเรียน, ขอนัดเลื่อนชำระ แต่ไม่ได้รับอนุมัติ
- Focuses specifically on the quality of service/interaction from staff/agent, not issues with the physical product/network itself (e.g., "the agent didn't follow up," "the agent was rude").
- บริการที่ศูนย์ shop ไม่ช่วยเลย
- ลูกค้าไม่พอใจการประเมินคะแนน
- ลูกค้า complain ว่าพนักงานสมัครบริการโดยที่ตนไม่ได้ขอ
```

### 1.8 `post to pre`

| Where | Citation |
|---|---|
| Schema enum | `main.py:972` |
| Scorer class | `fact_checker.py:866` |
| MNP entry | `prompt.txt:30` (numbered #8) |
| MNP definition | `prompt.txt:31` |
| MNP keywords | `prompt.txt:32` |
| Retention definition | `prompt.py:4367-4369` |

**`prompt.txt:31`**

```text
- Definition: Customer specifically requests to switch from Postpaid to Prepaid.
```

**`prompt.txt:32`**

```text
- Keywords: สาขาแนะนำให้กดย้ายค่ายเป็นเติมเงิน, ร้านให้กดย้ายค่ายเป็นเติมเงิน, ไม่อยากใช้รายเดือนแล้ว, ขอเปลี่ยนเป็นแบบเติมเงิน, อยากใช้แบบเติมเงิน, ไม่อยากผูกกับบิล, ไม่อยากจ่ายรายเดือน, ขอเลิกใช้รายเดือน, เติมเงินสะดวกกว่า, ต้องการเปลี่ยนเป็นเติมเงินเพราะถูกกว่า, อยากเปลี่ยนเป็นเติมเงินเพราะโปรดีกว่า, อยากเปลี่ยนเป็นเติมเงินเพราะโปรถูกกว่า, เติมเงินควบคุมง่ายกว่า, อยากเปลี่ยนเป็นเติมเงิน
```

**Evidence spans** (`prompt.txt:32`, split on the prompt's own commas):

- `สาขาแนะนำให้กดย้ายค่ายเป็นเติมเงิน`
- `ร้านให้กดย้ายค่ายเป็นเติมเงิน`
- `ไม่อยากใช้รายเดือนแล้ว`
- `ขอเปลี่ยนเป็นแบบเติมเงิน`
- `อยากใช้แบบเติมเงิน`
- `ไม่อยากผูกกับบิล`
- `ไม่อยากจ่ายรายเดือน`
- `ขอเลิกใช้รายเดือน`
- `เติมเงินสะดวกกว่า`
- `ต้องการเปลี่ยนเป็นเติมเงินเพราะถูกกว่า`
- `อยากเปลี่ยนเป็นเติมเงินเพราะโปรดีกว่า`
- `อยากเปลี่ยนเป็นเติมเงินเพราะโปรถูกกว่า`
- `เติมเงินควบคุมง่ายกว่า`
- `อยากเปลี่ยนเป็นเติมเงิน`

Current retention wording (`prompt.py:4367-4369`):

```text
- client want to change payment from postpaid(รายเดือน) to prepaid(เติมเงิน)
- ลูกค้าต้องการยกเลิก รายเดือน (Postpaid) เป็น เติมเงิน (Prepaid)
- CRITICAL: **หากได้ยินว่า มีการจะเปลี่ยน รายเดือน เป็น เติมเงิน จะนับว่ามีเหตุผล `post to pre` เสมอ**
```

### 1.9 `customer reason`

| Where | Citation |
|---|---|
| Schema enum | `main.py:972` |
| Scorer class | `fact_checker.py:867` |
| MNP entry | `prompt.txt:34` (numbered #9) |
| MNP definition | `prompt.txt:35` |
| MNP keywords | `prompt.txt:36` |
| Retention definition | `prompt.py:4372` |

**`prompt.txt:35`**

```text
- Definition: Customer refuses to give a reason or states it is personal.
```

**`prompt.txt:36`**

```text
- Keywords: ไม่มีไรคะ เหตุผลส่วนตัว, อยากย้ายเฉยๆ, ไม่อยากบอก, อยากย้ายเฉยๆ ไม่บอกได้ไหม, ไม่มีอะไร ไม่อยากใช้แล้ว
```

**Evidence spans** (`prompt.txt:36`, split on the prompt's own commas):

- `ไม่มีไรคะ เหตุผลส่วนตัว`
- `อยากย้ายเฉยๆ`
- `ไม่อยากบอก`
- `อยากย้ายเฉยๆ ไม่บอกได้ไหม`
- `ไม่มีอะไร ไม่อยากใช้แล้ว`

Current retention wording (`prompt.py:4372-4372`):

```text
- ลูกค้าเลี่ยงที่จะบอกเหตุผล หรือ ให้เหตุผลแบบ hate speech / megative reason เช่น เกลียดทรู, เกลียดดีแทค, ไม่ชอบ CP
```

### 1.10 `down sell not success`

| Where | Citation |
|---|---|
| Schema enum | `main.py:972` |
| Scorer class | `fact_checker.py:868` |
| MNP entry | `prompt.txt:42` (numbered #11) |
| MNP definition | `prompt.txt:43` |
| MNP keywords | `prompt.txt:44` |
| Retention definition | `prompt.py:4375-4376` |

**`prompt.txt:43`**

```text
- Definition: Customer asked to lower the price/change plan, but the agent refused or couldn't provide it.
```

**`prompt.txt:44`**

```text
- Keywords: ขอลดโปรโมชั่นแล้วแต่เจ้าหน้าที่ก็ลดให้ไม่ได้, ขอเปลี่ยนโปรโมชั่นลดลงเจ้าหน้าไม่ให้, ติดต่อขอลดโปรโมชั่นหลายรอบแล้วก็ทำไม่ได้, ต้องการโปรโมชั่นราคา XXX เจ้าหน้าที่บอกว่าไม่มี
```

**Evidence spans** (`prompt.txt:44`, split on the prompt's own commas):

- `ขอลดโปรโมชั่นแล้วแต่เจ้าหน้าที่ก็ลดให้ไม่ได้`
- `ขอเปลี่ยนโปรโมชั่นลดลงเจ้าหน้าไม่ให้`
- `ติดต่อขอลดโปรโมชั่นหลายรอบแล้วก็ทำไม่ได้`
- `ต้องการโปรโมชั่นราคา XXX เจ้าหน้าที่บอกว่าไม่มี`

Current retention wording (`prompt.py:4375-4376`):

```text
- ลูกค้าไม่ได้โปรโมชั่นราคาลดตามที่ต้องการ
- ก่อนหน้านี้มีเจ้าหน้าที่เสนอโปรโมชั่นราคาลดลงแต่ยังไม่ถูกใจ
```

### 1.11 `other`

| Where | Citation |
|---|---|
| Schema enum | `main.py:972` |
| Scorer class | `fact_checker.py:865` |
| MNP entry | `prompt.txt:46` (numbered #12) |
| MNP definition | `prompt.txt:47` |
| MNP keywords | `prompt.txt:48` |
| Retention definition | `prompt.py:4379-4381` |

**`prompt.txt:47`**

```text
- Definition: Reasons not covered above OR specific call situations like "Callback/Busy".
```

**`prompt.txt:48`**

```text
- Keywords: จะไปต่างประเทศ, ไม่ใช้เบอร์นี้แล้ว, เปลี่ยนงาน, ใช้เบอร์อื่นแทน, ย้ายตามครอบครัว, บริษัทให้ย้าย, ลูกให้ย้าย, เบอร์ไม่สวยเหมือนที่แจ้ง, ร้องเรียน กสทช, ฟ้องร้อง, คดีความ, เอาเปรียบผู้บริโภค, ไม่มีใครใช้งาน, เจ้าของเสียชีวิต, อยากลองเปลี่ยน, เกลียดทรู, เกลียดดีแทค, ไม่ชอบ CP, อีก 10 นาทีโทรมาใหม่, ขับรถอยู่, ติดประชุม, ไม่สะดวกคุย, เดี๋ยวโทรกลับมาใหม่นะ
```

**Evidence spans** (`prompt.txt:48`, split on the prompt's own commas):

- `จะไปต่างประเทศ`
- `ไม่ใช้เบอร์นี้แล้ว`
- `เปลี่ยนงาน`
- `ใช้เบอร์อื่นแทน`
- `ย้ายตามครอบครัว`
- `บริษัทให้ย้าย`
- `ลูกให้ย้าย`
- `เบอร์ไม่สวยเหมือนที่แจ้ง`
- `ร้องเรียน กสทช`
- `ฟ้องร้อง`
- `คดีความ`
- `เอาเปรียบผู้บริโภค`
- `ไม่มีใครใช้งาน`
- `เจ้าของเสียชีวิต`
- `อยากลองเปลี่ยน`
- `เกลียดทรู`
- `เกลียดดีแทค`
- `ไม่ชอบ CP`
- `อีก 10 นาทีโทรมาใหม่`
- `ขับรถอยู่`
- `ติดประชุม`
- `ไม่สะดวกคุย`
- `เดี๋ยวโทรกลับมาใหม่นะ`

Current retention wording (`prompt.py:4379-4381`):

```text
- เหตุผลอื่นๆ
- ตัวอย่าง เช่น ลูกค้าไปใช้สิทธิ์ แลก True point หรือ dtac reward ไม่ได้, ลูกค้าอยู่ๆเปลี่ยนใจ ไม่ยกเลิกแล้ว
- เจอภัยพิบัติทางธรรมชาติ เช่น อุทกกภัย, นำ้ท่วม
```

### 1.12 `true point, dtac reward` — PROMPT ONLY, NOT A VALID LABEL

**Do not use this value.** It is reason #10 in `prompt.txt` and appears in every historical
retention prompt from `prompt_v1` (`prompt.py:23`) through `prompt_v8_5` (`prompt.py:1789`).
It is **not in the schema enum** (`main.py:972`) and **not in the scorer's `target_classes`**
(`fact_checker.py:857-869`). Recorded here only so nobody re-adds it.

**`prompt.txt:39`**

```text
- Definition: Issues with redeeming points, rewards, or privileges.
```

**`prompt.txt:40`**

```text
- Keywords: กดใช้สิทธิ์ของทรูการ์ดไม่ได้เลย, สิทธิ์เต็ม, ถูกลดเกรด, ไม่มีร้านให้ใช้สิทธิ์, สิทธิ์แบล็คการ์ดหลุด, กดรับสิทธิ์ไม่ได้
```

Three independent reasons it cannot be used:

- It is absent from the schema enum, so a function-calling model cannot legally emit it.
- The string **contains a comma**, so `get_reasons_set` (`fact_checker.py:877`) splits it into
  `true point` and `dtac reward` — two tokens matching no scored class. It is unscorable even
  if it were emitted.
- The current retention prompt **folds it into `other`**:

**`prompt.py:4380`**

```text
- ตัวอย่าง เช่น ลูกค้าไปใช้สิทธิ์ แลก True point หรือ dtac reward ไม่ได้, ลูกค้าอยู่ๆเปลี่ยนใจ ไม่ยกเลิกแล้ว
```

**Rule: a points-or-rewards transcript is labelled `other`.** Cite `prompt.py:4380` alongside
`prompt.txt:48`.

## Cross-list collisions inside `reason`

Computed exactly: spans that appear **verbatim in two different reason keyword lists**.

**None.** Every reason keyword span is unique to its list, so any single span is
decisive for exactly one reason.

---

# 2. `retention_outcome` (scored as `call_result`) — 4 values

## The authoritative list

```python
 1016              "retention_outcome": {
 1017                  "type": "STRING",
 1018                  "enum": ["churn", "save", "unknown", "undefined"],
 1019                  "description": "Determine the final decision of the client: churn, save, unknown or undefined."
 1020              },
```

Scorer `target_classes`:

**`fact_checker.py:791`**

```python
target_classes = ['save', 'churn', 'unknown', 'undefined']
```

Report display order:

**`fact_checker.py:769`**

```python
desired_order = ['save', 'churn', 'unknown', 'undefined', 'total']
```

## Scorer mechanics

**`fact_checker.py:756`**

```python
call_result_df = merged_df.dropna(subset=['call_result_gt']).copy()
```

Rows with a **null ground-truth** outcome are dropped before scoring; rows with a null
**prediction** are kept and count against the model. A testset item with no ground-truth
`retention_outcome` is silently deleted from this dimension while still being scored on
`reason` and `product` — three dimensions, three denominators. **Always set one.** (**D11**)

## The 4 values

Definitions come from `prompt.txt:51-66` (MNP wording) and `prompt.py:4389-4399` (current
retention wording). **Where they disagree, `prompt.py` governs a retention item.** See **D4**.

### 2.1 `save`

| Where | Citation |
|---|---|
| Schema enum | `main.py:1018` |
| Scorer class | `fact_checker.py:791` |
| MNP definition | `prompt.txt:55` |
| MNP example keywords | `prompt.txt:56` |
| Retention definition | `prompt.py:4393-4397` |

**`prompt.txt:55`**

```text
- Definition (Eng): Customer decides to continue using the service after receiving retention efforts or alternative offers from the call center agent based on the overall context, even if the customer initially requested to think about it but did not explicitly cancel, with acceptance when the call center agent asks to cancel the code or porting cancellation, including agreeing to continue using the existing service while considering or requesting a comparison without making a payment to close the balance for a porting code.
```

**`prompt.txt:56`**

```text
- Example keyword (Thai): โอเคครับ เดี๋ยวใช้ต่ออีกเดือน, ขอบคุณสำหรับโปรใหม่, จะลองใช้อีกครั้ง, พนักงานช่วยดีมาก, กดผิด, ใช้งานต่อ, ไม่ย้ายแล้ว, ลองดู, ขอยกเลิกรหัส, ขอยกเลิกการโอนย้าย, ให้โอกาส, ถ้าได้แบบนี้ก็ไม่ย้ายไปไหนหรอก, ขอบคุณมากที่ดูแล, ขอบคุณที่ให้โอกาสอีกครั้ง, ขอยกเลิกรหัสย้ายค่าย, ยังไม่ชำระเงินเผื่อขอรหัส, งั้นใช้กับทางทรูไปก่อน, ยกเลิก pin ไปก่อนนะคะ, ใช้งาน Dtac ต่อไป, ไม่ได้ย้ายค่าบเบอร์นี้
```

**Evidence spans** (`prompt.txt:56`):

- `โอเคครับ เดี๋ยวใช้ต่ออีกเดือน`
- `ขอบคุณสำหรับโปรใหม่`
- `จะลองใช้อีกครั้ง`
- `พนักงานช่วยดีมาก`
- `กดผิด`
- `ใช้งานต่อ`
- `ไม่ย้ายแล้ว`
- `ลองดู`
- `ขอยกเลิกรหัส`
- `ขอยกเลิกการโอนย้าย`
- `ให้โอกาส`
- `ถ้าได้แบบนี้ก็ไม่ย้ายไปไหนหรอก`
- `ขอบคุณมากที่ดูแล`
- `ขอบคุณที่ให้โอกาสอีกครั้ง`
- `ขอยกเลิกรหัสย้ายค่าย`
- `ยังไม่ชำระเงินเผื่อขอรหัส`
- `งั้นใช้กับทางทรูไปก่อน`
- `ยกเลิก pin ไปก่อนนะคะ`
- `ใช้งาน Dtac ต่อไป`
- `ไม่ได้ย้ายค่าบเบอร์นี้`

Current retention definition — **broader than the MNP one**, and the version to cite for a
retention item:

```text
- `save`
- Client confirms staying loyal to the brand/service, OR
- Client accepts the agent's counter-offer/persuasion, OR
- Client let the agent try to fix the problem then agent will contact client later OR
- Client expresses indecision or asks for time to think ("ลังเล ขอเวลาคิดก่อน ยังตัดสินใจไม่ได้"). This is counted as a 'save' because the final decision to churn has not been executed or confirmed.
```

Note the last clause at `prompt.py:4397`: **indecision counts as `save`**, not `unknown`.
"ขอเวลาคิดก่อน" is a `save`. See **D4**.

### 2.2 `churn`

| Where | Citation |
|---|---|
| Schema enum | `main.py:1018` |
| Scorer class | `fact_checker.py:791` |
| MNP definition | `prompt.txt:58` |
| MNP example keywords | `prompt.txt:59` |
| Retention definition | `prompt.py:4390-4392` |

**`prompt.txt:58`**

```text
- Definition (Eng): Customer decides to cancel the service despite retention efforts or alternative offers from the call center agent, even changing to prepaid or directly refusing to continue the service.
```

**`prompt.txt:59`**

```text
- Example keyword (Thai): ขอยกเลิกครับ, ไม่ใช้บริการแล้ว, ไม่คุ้มที่จะจ่ายต่อ ,ขอลองย้ายไปก่อน, ให้โอกาสหลายรอบแล้ว ก็ไม่ดี, ทำไมพึ่งมาดูแล, เคยขอแล้วไม่ให้, ไม่ค่ะ ไม่รับ, พี่จะขอรหัส, ขอรหัสย้ายค้าย, ขอรหัส PIN, พี่แกะเครื่องไปแล้ว ซื้อเครื่องใหม่กับค่ายอื่นแล้ว, กำลังโอนย้ายข้อมูล, รูดบัตรไปแล้ว, ไม่เป็นไรคะ ขอรหัสโอนย้ายค่ะ, ไม่เอาคะ ไปรับ sim แล้ว, ไม่มีอะไร อยากย้ายเฉยๆ, ปล่อยพี่ไปเถอะ, ลูกทำให้ ลูกให้ย้าย, ไม่เอาค่ะ พี่อยากใช้เติมเงิน, ไม่มีประโยชน์อะไรแล้ว ไม่เป็นไร
```

**Evidence spans** (`prompt.txt:59`):

- `ขอยกเลิกครับ`
- `ไม่ใช้บริการแล้ว`
- `ไม่คุ้มที่จะจ่ายต่อ`
- `ขอลองย้ายไปก่อน`
- `ให้โอกาสหลายรอบแล้ว ก็ไม่ดี`
- `ทำไมพึ่งมาดูแล`
- `เคยขอแล้วไม่ให้`
- `ไม่ค่ะ ไม่รับ`
- `พี่จะขอรหัส`
- `ขอรหัสย้ายค้าย`
- `ขอรหัส PIN`
- `พี่แกะเครื่องไปแล้ว ซื้อเครื่องใหม่กับค่ายอื่นแล้ว`
- `กำลังโอนย้ายข้อมูล`
- `รูดบัตรไปแล้ว`
- `ไม่เป็นไรคะ ขอรหัสโอนย้ายค่ะ`
- `ไม่เอาคะ ไปรับ sim แล้ว`
- `ไม่มีอะไร อยากย้ายเฉยๆ`
- `ปล่อยพี่ไปเถอะ`
- `ลูกทำให้ ลูกให้ย้าย`
- `ไม่เอาค่ะ พี่อยากใช้เติมเงิน`
- `ไม่มีประโยชน์อะไรแล้ว ไม่เป็นไร`

Current retention definition:

```text
- `churn`
- Client confirms leaving the brand (moving to a competitor).
- Client successfully changes their service from a Postpaid/Contract plan to a Prepaid plan, even if they technically remain with the brand (as this is treated as a loss of the higher-value postpaid contract).
```

**Postpaid to Prepaid is `churn`** (`prompt.py:4392`), even though the customer stays with the
brand. An item labelled `reason = post to pre` normally carries `retention_outcome = churn`
unless the agent talked them out of it.

### 2.3 `unknown`

| Where | Citation |
|---|---|
| Schema enum | `main.py:1018` |
| Scorer class | `fact_checker.py:791` |
| MNP definition | `prompt.txt:61` |
| MNP example keywords | `prompt.txt:62` |
| Retention definition | `prompt.py:4398` |

**`prompt.txt:61`**

```text
- Definition (Eng): Customer is undecided about whether to continue or cancel the service during the conversation or unable to make a decision on their own without explicit cancellation or porting cancellation when there is no response when the call center agent asks to cancel the code or porting cancellation, or unable to complete the conversation with the customer making it impossible to determine the final outcome of the customer, or due to call disconnection during the conversation, or the call center agent asked but never got back.
```

**`prompt.txt:62`**

```text
- Example keyword (Thai): อีก 10 นาทีโทรมาใหม่, ขับรถอยู่, ติดประชุม, ไม่สะดวกคุย, เดี๋ยวโทรกลับมาใหม่นะ
```

**Evidence spans** (`prompt.txt:62`):

- `อีก 10 นาทีโทรมาใหม่`
- `ขับรถอยู่`
- `ติดประชุม`
- `ไม่สะดวกคุย`
- `เดี๋ยวโทรกลับมาใหม่นะ`

**`prompt.py:4398`**

```text
- `unknown` (Conversation ends before making a final decision due to an unresolved outcome, such as the **call being technically interrupted or crashing (e.g., dropped call)**, or any other reason where the client did not explicitly state a final outcome of `churn` or `save`)
```

The retention wording is **narrower**: it names a technically interrupted or dropped call.
Prefer it. `prompt.txt:61`'s "customer is undecided" clause is superseded — see **D4**.

### 2.4 `undefined`

| Where | Citation |
|---|---|
| Schema enum | `main.py:1018` — spelled **`undefined`** |
| Scorer class | `fact_checker.py:791` — spelled **`undefined`** |
| MNP definition | `prompt.txt:63-64` — spelled **`undefine`** |
| Retention definition | `prompt.py:4399` — spelled **`undefined`** |

```text
- `undefine`
- Definition (Eng): Identify that the conversation is not about requesting a porting code or porting out from the customer or call center agent never mentioned porting code or porting out during the call.
```

**`prompt.py:4399`**

```text
- `undefined` (Conversation irrelevant to retention / The client did not call to discuss changing, cancelling, or downgrading their service, and therefore the agent did not need to perform a retention effort (persuade them to stay loyal to the brand). This key is used when the focus of the call is completely outside the scope of retention)
```

**Use `undefined`, with the `d`.** `prompt.txt:63` is missing it — see **D3**.

Plain-language rule: `undefined` means **the call was never about retention at all** — no
porting code, no cancellation, no downgrade, so the agent had nothing to save. It is not a
weaker `unknown`. `unknown` means retention *was* in play and the outcome was never settled.

## Collisions between outcome keywords and reason keywords

Computed exactly: spans appearing verbatim in both an outcome list and a reason list.

| Span | Outcome | Reason | Lines |
|---|---|---|---|
| `ขับรถอยู่` | `unknown` | `other` | `prompt.txt:62` + `prompt.txt:48` |
| `ติดประชุม` | `unknown` | `other` | `prompt.txt:62` + `prompt.txt:48` |
| `อีก 10 นาทีโทรมาใหม่` | `unknown` | `other` | `prompt.txt:62` + `prompt.txt:48` |
| `เดี๋ยวโทรกลับมาใหม่นะ` | `unknown` | `other` | `prompt.txt:62` + `prompt.txt:48` |
| `ไม่สะดวกคุย` | `unknown` | `other` | `prompt.txt:62` + `prompt.txt:48` |

These are **by design** — a callback request is reason `other` *and* outcome `unknown` — but
it means one span cannot carry both fields. **An item using one of these spans must supply a
separate `ev_` span for the other field.**

---

# 3. `product` — 4 keys

## The authoritative list

Schema — the `product` object's keys, with `additionalProperties: False`:

```python
 1033                  "product": {
 1034                      "type": "OBJECT",
 1035                      "description": "Analysis of the specific products (Postpaid, TOL, TVS, unknown) mentioned in the call. Only include product keys that were mentioned.",
 1036                      "properties": {
 1037                          "Postpaid": PRODUCT_ANALYSIS_SCHEMA,
 1038                          "TOL": PRODUCT_ANALYSIS_SCHEMA,
 1039                          "TVS": PRODUCT_ANALYSIS_SCHEMA,
 1040                          "unknown": PRODUCT_ANALYSIS_SCHEMA
 1041                      },
 1042                      "additionalProperties": False # Enforce use of only the defined product keys
 1043                  },
```

Scorer `target_classes` — **lowercase**:

```python
  971          target_classes = [
  972              'postpaid', 'tol', 'tvs', 'unknown'
  973          ]
```

## Is the product membership test case-sensitive? Exactly this:

```python
  987          for i, cls in enumerate(target_classes, 1):
  988              cls_lower = cls.lower()
  989  
  990              is_in_gt = merged_df['product_gt'].apply(lambda x: cls_lower in x)
  991              is_in_pred = merged_df['product_pred'].apply(lambda x: cls_lower in x)
```

**The test itself is case-SENSITIVE.** `x` is a Python `set` of strings and `cls_lower in x`
is exact string equality against every member. `.lower()` at `:988` is a **no-op** — the
target classes at `:971-973` are already lowercase, so it lowercases nothing that was not
already lowercase and it never touches `x`.

Case-insensitivity comes from **one place upstream and nowhere else** — `pre_process`
lowercases and strips every `object`-dtype column, including `product`, in both frames:

```python
  732              # Normalize string columns to lowercase
  733              for col in prediction_df.columns:
  734                  if prediction_df[col].dtype == 'object':
  735                      prediction_df[col] = prediction_df[col].str.lower().str.strip()
  736  
  737              for col in aligned_gt_df.columns:
  738                  if aligned_gt_df[col].dtype == 'object':
  739                      aligned_gt_df[col] = aligned_gt_df[col].str.lower().str.strip()
```

The ordering is load-bearing: `pre_process` runs at `:1069`; the
`groupby(['call_id','phone_number'])['product'].apply(set)` that builds `product_gt` /
`product_pred` runs at `:1080-1081`. By the time `:990-991` tests membership, the sets already
hold lowercase strings.

**Consequences for a testset:**

- Write products as `postpaid`, `tol`, `tvs`, `unknown` in fixture CSVs. The model emits
  `Postpaid` / `TOL` / `TVS`; production folds the case. Either casing is defensible as ground
  truth **only if** the harness reproduces `:732-739`.
- The fold is guarded by `dtype == 'object'` (`:733`, `:738`). If that guard evaluates False,
  the lowercasing silently does not happen and `:990-991` becomes case-sensitive in effect.
  That is already the C3 regression case in `tests/fixtures/CASES.md`.
- `:990-991` reads off `merged2_df`, built by `groupby(['call_id','phone_number'])` at
  `:1080-1081` **without `dropna=False`**, so rows with a null `phone_number` never reach the
  product metric at all.

## The 4 keys

| Key (schema) | Scored as | Schema line | Scorer line | Definition |
|---|---|---|---|---|
| `Postpaid` | `postpaid` | `main.py:1037` | `fact_checker.py:972` | `prompt.py:4321` |
| `TOL` | `tol` | `main.py:1038` | `fact_checker.py:972` | `prompt.py:4322` |
| `TVS` | `tvs` | `main.py:1039` | `fact_checker.py:972` | `prompt.py:4323` |
| `unknown` | `unknown` | `main.py:1040` | `fact_checker.py:972` | `prompt.py:4324` |

Definitions, verbatim:

```text
- `Postpaid`: ลูกค้า Mobile แบบ จ่ายค่าบริการรายเดือน
- `TOL`: ลูกค้า True Online เกี่ยวกับ Internet บ้าน
- `TVS`: ลูกค้า True Vision ดูทีวีแบบสมัครสมาชิกรายเดือน , รายครึ่งปี , รายปี , กล่องขายขาด , กล่อง True ID TV (Streaming)
- `unknown`: Can't determine the product type
```

And the schema's own one-liner:

**`main.py:1035`**

```python
"description": "Analysis of the specific products (Postpaid, TOL, TVS, unknown) mentioned in the call. Only include product keys that were mentioned.",
```

**There is no `Prepaid` product key.** `prompt.txt:239-250` defines a separate `product_type`
field that does include `Prepaid` — a field the retention schema does not have. See **D5**. A
customer moving to prepaid is labelled under the product they are leaving (usually
`postpaid`), with `reason = post to pre` and `retention_outcome = churn`.

---

# 4. `issue_type` — 8 values

Conditional. `main.py:983-1007` makes the whole `network_issue` object nullable (`:985`), and
`prompt.txt:99-104` makes `issue_type` the trigger for every other field in the block.

**`main.py:989`**

```python
"enum": ["Speed", "Outage", "Drop", "Coverage", "FUP", "Installation", "Support", "Voice Quality"]
```

```text
<activation_rule>
issue_type is the PRIMARY TRIGGER for this entire section:
- If a network issue IS detected → set issue_type to the matching category, then populate ALL other fields below with meaningful values.
- If NO network issue is detected → set issue_type to null, and ALL other fields must also be null.
- Do NOT set dependent fields to null just because the customer's sentiment is neutral or positive. As long as issue_type has a value, every field must be populated.
</activation_rule>
```

**Not scored** — `fact_checker.py:1095-1099` computes `call_result`, `reason` and `product`
only.

## The 8 values

### 4.1 `Speed`

| Where | Citation |
|---|---|
| Schema enum | `main.py:989` |
| Definition | `prompt.txt:112` |
| Thai examples | `prompt.txt:113` |

**`prompt.txt:112`**

```text
Definition: Network performance is slower than expected WITHOUT the customer having exceeded their data limit. Causes delays, buffering, or lag while the connection remains active.
```

**`prompt.txt:113`**

```text
Thai examples: เน็ตช้า, ค้าง, หลุดโหลด, โหลดช้า, เน็ตกระตุก, ดูวิดีโอแล้วกระตุก, เล่นเกมแล้วแลค, เน็ตไม่เร็วเหมือนเดิม, หมุนโหลด, บัฟเฟอร์ตลอด
```

**Evidence spans** (`prompt.txt:113`):

- `เน็ตช้า`
- `ค้าง`
- `หลุดโหลด`
- `โหลดช้า`
- `เน็ตกระตุก`
- `ดูวิดีโอแล้วกระตุก`
- `เล่นเกมแล้วแลค`
- `เน็ตไม่เร็วเหมือนเดิม`
- `หมุนโหลด`
- `บัฟเฟอร์ตลอด`

### 4.2 `Outage`

| Where | Citation |
|---|---|
| Schema enum | `main.py:989` |
| Definition | `prompt.txt:116` |
| Thai examples | `prompt.txt:117` |

**`prompt.txt:116`**

```text
Definition: Complete loss of ALL network services — customer cannot make calls, send messages, or use data at all. The service is entirely non-functional for an extended period.
```

**`prompt.txt:117`**

```text
Thai examples: ใช้อะไรไม่ได้เลย, ไม่มีสัญญาณเลย, เน็ตล่ม, โทรไม่ได้เลย, สัญญาณหายหมด, ไม่มีคลื่นเลย, ดับหมด, เน็ตไม่มาเลย, ใช้ไม่ได้ทั้งวัน, ระบบล่ม
```

**Evidence spans** (`prompt.txt:117`):

- `ใช้อะไรไม่ได้เลย`
- `ไม่มีสัญญาณเลย`
- `เน็ตล่ม`
- `โทรไม่ได้เลย`
- `สัญญาณหายหมด`
- `ไม่มีคลื่นเลย`
- `ดับหมด`
- `เน็ตไม่มาเลย`
- `ใช้ไม่ได้ทั้งวัน`
- `ระบบล่ม`

### 4.3 `Drop`

| Where | Citation |
|---|---|
| Schema enum | `main.py:989` |
| Definition | `prompt.txt:120` |
| Thai examples | `prompt.txt:121` |

**`prompt.txt:120`**

```text
Definition: An unexpected disconnection during an active session (call drops mid-conversation, data session cuts out suddenly). The connection was working but terminates abruptly before the user ends it.
```

**`prompt.txt:121`**

```text
Thai examples: สายหลุด, โทรแล้วหลุดบ่อย, เน็ตหลุดๆติดๆ, สัญญาณขาดๆหายๆ, เล่นเกมแล้วหลุด, คุยอยู่ดีๆก็ตัด, หลุดบ่อย, ตัดสายบ่อย, เน็ตหลุดตลอด, สัญญาณไม่เสถียร
```

**Evidence spans** (`prompt.txt:121`):

- `สายหลุด`
- `โทรแล้วหลุดบ่อย`
- `เน็ตหลุดๆติดๆ`
- `สัญญาณขาดๆหายๆ`
- `เล่นเกมแล้วหลุด`
- `คุยอยู่ดีๆก็ตัด`
- `หลุดบ่อย`
- `ตัดสายบ่อย`
- `เน็ตหลุดตลอด`
- `สัญญาณไม่เสถียร`

### 4.4 `Coverage`

| Where | Citation |
|---|---|
| Schema enum | `main.py:989` |
| Definition | `prompt.txt:124` |
| Thai examples | `prompt.txt:125` |

**`prompt.txt:124`**

```text
Definition: Network signal is weak, poor, or unavailable in specific geographic areas due to insufficient infrastructure, physical obstacles, or distance from cell sites. The issue is location-dependent.
```

**`prompt.txt:125`**

```text
Thai examples: ไม่มีสัญญาณตรงบ้าน, ไปเที่ยวไม่มีสัญญาณ, ที่ทำงานสัญญาณไม่ดี, ไม่ค่อยมีสัญญาณ, สัญญาณแย่มากแถวนี้, ไม่มีคลื่นในตึก, ชั้นใต้ดินไม่มีสัญญาณ, ต่างจังหวัดไม่มีสัญญาณ, ในบ้านสัญญาณไม่เข้า, สัญญาณเข้าไม่ถึง
```

**Evidence spans** (`prompt.txt:125`):

- `ไม่มีสัญญาณตรงบ้าน`
- `ไปเที่ยวไม่มีสัญญาณ`
- `ที่ทำงานสัญญาณไม่ดี`
- `ไม่ค่อยมีสัญญาณ`
- `สัญญาณแย่มากแถวนี้`
- `ไม่มีคลื่นในตึก`
- `ชั้นใต้ดินไม่มีสัญญาณ`
- `ต่างจังหวัดไม่มีสัญญาณ`
- `ในบ้านสัญญาณไม่เข้า`
- `สัญญาณเข้าไม่ถึง`

### 4.5 `FUP`

| Where | Citation |
|---|---|
| Schema enum | `main.py:989` |
| Definition | `prompt.txt:128` |
| Thai examples | `prompt.txt:129` |

**`prompt.txt:128`**

```text
Definition: Network speed is throttled AFTER the customer exceeds their data package limit (Fair Usage Policy). The customer explicitly mentions running out of data or speed reduction after heavy usage.
```

**`prompt.txt:129`**

```text
Thai examples: ความเร็วเน็ตลดลงหลังจากใช้ข้อมูลครบตามแพ็กเกจ, เน็ตหมดแล้วช้ามาก, ใช้เน็ตครบโควต้าแล้วช้า, เน็ตหมดเร็ว, ความเร็วลดหลังใช้ครบ, เน็ตถูกลดสปีด
```

**Evidence spans** (`prompt.txt:129`):

- `ความเร็วเน็ตลดลงหลังจากใช้ข้อมูลครบตามแพ็กเกจ`
- `เน็ตหมดแล้วช้ามาก`
- `ใช้เน็ตครบโควต้าแล้วช้า`
- `เน็ตหมดเร็ว`
- `ความเร็วลดหลังใช้ครบ`
- `เน็ตถูกลดสปีด`

### 4.6 `Installation`

| Where | Citation |
|---|---|
| Schema enum | `main.py:989` |
| Definition | `prompt.txt:132` |
| Thai examples | `prompt.txt:133` |

**`prompt.txt:132`**

```text
Definition: Problems with setting up or activating network services — technician appointments, hardware installation failures, or initial configuration issues that prevent service from starting.
```

**`prompt.txt:133`**

```text
Thai examples: การติดตั้งล้มเหลว, อุปกรณ์ไม่ทำงาน, ช่างไม่มา, ติดตั้งไม่เสร็จ, รอช่างนาน, เราเตอร์ใช้ไม่ได้, ต่อไฟเบอร์ไม่ได้, ติดตั้งแล้วใช้ไม่ได้
```

**Evidence spans** (`prompt.txt:133`):

- `การติดตั้งล้มเหลว`
- `อุปกรณ์ไม่ทำงาน`
- `ช่างไม่มา`
- `ติดตั้งไม่เสร็จ`
- `รอช่างนาน`
- `เราเตอร์ใช้ไม่ได้`
- `ต่อไฟเบอร์ไม่ได้`
- `ติดตั้งแล้วใช้ไม่ได้`

### 4.7 `Support`

| Where | Citation |
|---|---|
| Schema enum | `main.py:989` |
| Definition | `prompt.txt:136` |
| Thai examples | `prompt.txt:137` |

**`prompt.txt:136`**

```text
Definition: Customer needs help with a network-related problem but has not yet identified a specific performance issue (not clearly slow, drop, or outage). They need diagnosis, troubleshooting guidance, or general network assistance.
```

**`prompt.txt:137`**

```text
Thai examples: แก้ไม่ได้, ไม่มีใครช่วย, แจ้งปัญหาแล้วไม่แก้ให้, ช่วยเช็คสัญญาณให้หน่อย, ไม่รู้ปัญหาอะไร, ตั้งค่าไม่ถูก, ปัญหาเน็ตแต่ไม่รู้สาเหตุ
```

**Evidence spans** (`prompt.txt:137`):

- `แก้ไม่ได้`
- `ไม่มีใครช่วย`
- `แจ้งปัญหาแล้วไม่แก้ให้`
- `ช่วยเช็คสัญญาณให้หน่อย`
- `ไม่รู้ปัญหาอะไร`
- `ตั้งค่าไม่ถูก`
- `ปัญหาเน็ตแต่ไม่รู้สาเหตุ`

### 4.8 `Voice Quality`

| Where | Citation |
|---|---|
| Schema enum | `main.py:989` |
| Definition | `prompt.txt:140` |
| Thai examples | `prompt.txt:141` |

**`prompt.txt:140`**

```text
Definition: Audio quality during calls is poor — unclear, static, echoing, or cutting in and out — caused by the network's performance in transmitting voice signals.
```

**`prompt.txt:141`**

```text
Thai examples: เสียงไม่ชัด, มีเสียงรบกวน, สัญญาณขาดหาย, เสียงแตก, คุยไม่รู้เรื่อง, เสียงหาย, เสียงกระตุก, ได้ยินไม่ชัด, เสียงเบามาก, พูดแล้วอีกฝั่งไม่ได้ยิน
```

**Evidence spans** (`prompt.txt:141`):

- `เสียงไม่ชัด`
- `มีเสียงรบกวน`
- `สัญญาณขาดหาย`
- `เสียงแตก`
- `คุยไม่รู้เรื่อง`
- `เสียงหาย`
- `เสียงกระตุก`
- `ได้ยินไม่ชัด`
- `เสียงเบามาก`
- `พูดแล้วอีกฝั่งไม่ได้ยิน`

## Disambiguation rules — `prompt.txt:143-169`

These lines make an `issue_type` label *decisive* when a span could match two categories.
**Cite one of them whenever the transcript carries spans from more than one category.**

```text
  143          <disambiguation_rules>
  144              Use these rules when the customer's complaint could match multiple categories:
  145  
  146              Speed vs FUP:
  147                  - If customer mentions running out of data, exceeding quota, or speed drop after heavy usage → FUP
  148                  - If customer reports slow speed without any mention of data limits or quota → Speed
  149                  - Key signal: "เน็ตหมด" or "ใช้ครบแล้ว" → FUP. Just "เน็ตช้า" without data context → Speed
  150  
  151              Drop vs Outage:
  152                  - If service works intermittently (connects then disconnects repeatedly) → Drop
  153                  - If service is completely non-functional for an extended period with zero connectivity → Outage
  154                  - Key signal: "หลุดบ่อย" or "ขาดๆหายๆ" → Drop. "ใช้ไม่ได้เลย" or "ไม่มีสัญญาณเลย" → Outage
  155  
  156              Coverage vs Outage:
  157                  - If the issue is tied to a specific location (works elsewhere, fails here) → Coverage
  158                  - If service is down everywhere regardless of location → Outage
  159                  - Key signal: mentions a place name or "ตรงนี้/แถวนี้/ที่บ้าน" → Coverage
  160  
  161              Speed vs Drop:
  162                  - If the connection stays active but is slow → Speed
  163                  - If the connection completely disconnects mid-use → Drop
  164                  - Key signal: "ค้าง/ช้า/แลค" → Speed. "หลุด/ตัด/ขาด" → Drop
  165  
  166              Coverage vs Drop:
  167                  - If signal is consistently weak in a specific area → Coverage
  168                  - If signal fluctuates between working and not working → Drop
  169          </disambiguation_rules>
```

Line by line, for citation:

| Pair | Deciding line | The signal |
|---|---|---|
| Speed vs FUP | `prompt.txt:147` | quota / data exhausted mentioned → `FUP` |
| Speed vs FUP | `prompt.txt:148` | slow with **no** data-limit mention → `Speed` |
| Speed vs FUP | `prompt.txt:149` | `เน็ตหมด` or `ใช้ครบแล้ว` → `FUP`; bare `เน็ตช้า` → `Speed` |
| Drop vs Outage | `prompt.txt:152` | intermittent, reconnects → `Drop` |
| Drop vs Outage | `prompt.txt:153` | zero connectivity, extended → `Outage` |
| Drop vs Outage | `prompt.txt:154` | `หลุดบ่อย` / `ขาดๆหายๆ` → `Drop`; `ใช้ไม่ได้เลย` / `ไม่มีสัญญาณเลย` → `Outage` |
| Coverage vs Outage | `prompt.txt:157` | works elsewhere, fails here → `Coverage` |
| Coverage vs Outage | `prompt.txt:158` | down everywhere → `Outage` |
| Coverage vs Outage | `prompt.txt:159` | a place name, or `ตรงนี้/แถวนี้/ที่บ้าน` → `Coverage` |
| Speed vs Drop | `prompt.txt:162` | connection stays up but slow → `Speed` |
| Speed vs Drop | `prompt.txt:163` | disconnects mid-use → `Drop` |
| Speed vs Drop | `prompt.txt:164` | `ค้าง/ช้า/แลค` → `Speed`; `หลุด/ตัด/ขาด` → `Drop` |
| Coverage vs Drop | `prompt.txt:167` | consistently weak in one area → `Coverage` |
| Coverage vs Drop | `prompt.txt:168` | fluctuates working / not working → `Drop` |

**The rules cover 5 of the 28 possible category pairs.** `Installation`, `Support`, `Voice Quality` are never disambiguated
against anything. An item whose span could plausibly be `Support` or `Speed` has **no
production line that settles it** — that item is under-specified by construction. Rewrite it so
exactly one category's example list matches.

## Ambiguous spans, computed

Spans appearing **verbatim in two different `issue_type` lists**:

**None.** No span is listed verbatim under two `issue_type` categories, so any single
span is decisive for exactly one category — *provided* the transcript contains only one.

**Near-collisions**, computed: spans where one is a **substring of another** across two
categories. These are the ones that bite, because a transcript containing the longer span also
contains the shorter one, and a naive `in` check matches both.

**None.**

Spans appearing in **both the `network` reason list (`prompt.txt:4`) and an `issue_type`
list** — harmless (different fields) but useful, because one span can carry `ev_reason` and
`ev_issue_type` at once:

| Span | `issue_type` | Lines |
|---|---|---|
| `ดูวิดีโอแล้วกระตุก` | `Speed` | `prompt.txt:4` + `prompt.txt:113` |
| `สัญญาณขาดๆหายๆ` | `Drop` | `prompt.txt:4` + `prompt.txt:121` |
| `สัญญาณไม่เสถียร` | `Drop` | `prompt.txt:4` + `prompt.txt:121` |
| `หมุนโหลด` | `Speed` | `prompt.txt:4` + `prompt.txt:113` |
| `หลุดบ่อย` | `Drop` | `prompt.txt:4` + `prompt.txt:121` |
| `เน็ตกระตุก` | `Speed` | `prompt.txt:4` + `prompt.txt:113` |
| `เน็ตช้า` | `Speed` | `prompt.txt:4` + `prompt.txt:113` |
| `เน็ตล่ม` | `Outage` | `prompt.txt:4` + `prompt.txt:117` |
| `เล่นเกมแล้วหลุด` | `Drop` | `prompt.txt:4` + `prompt.txt:121` |
| `โทรไม่ได้เลย` | `Outage` | `prompt.txt:4` + `prompt.txt:117` |
| `ไปเที่ยวไม่มีสัญญาณ` | `Coverage` | `prompt.txt:4` + `prompt.txt:125` |
| `ไม่ค่อยมีสัญญาณ` | `Coverage` | `prompt.txt:4` + `prompt.txt:125` |

**These are the highest-value spans for a network item**: one verbatim span justifies
`reason = network` (`prompt.txt:4`) and the `issue_type` simultaneously, with two independent
production citations.

---

# 5. `call_event_detection` — 6 values, typos preserved

**Not scored**, but required by the schema (`main.py:1054`), so it must be present and legal.

The enum, byte-exact. **The typos are part of the string.** A value that "fixes" them is not
in the enum:

**`main.py:1046`**

```python
"enum": ["Market-Driven Events (เหตุการณ์ทางการตลาด)", "Crisis & Emergency Events (เหตุการณ์วิกฤตหรือภัยพิบัติ)", "Campaign-Drvien Events (เหตุการณ์ด้านเคมเปญต่างๆของบริษัท)", "Technology & Service Events (เหตุการณ์ด้านเทคโนโลยี/บริการ)", "True-DTAC Merger(การรวมกิจการของ True และ ดีแทค)", "Emerging or Undefined Events (เหตุผลที่ยังไม่สามารถจัดกลุ่มได้)"],
```

Broken out, one per row:

| # | Enum value, exactly | Typo carried |
|---|---|---|
| 1 | `Market-Driven Events (เหตุการณ์ทางการตลาด)` | — |
| 2 | `Crisis & Emergency Events (เหตุการณ์วิกฤตหรือภัยพิบัติ)` | — |
| 3 | `Campaign-Drvien Events (เหตุการณ์ด้านเคมเปญต่างๆของบริษัท)` | **`Drvien`** for `Driven`; **`เคมเปญ`** for `แคมเปญ` |
| 4 | `Technology & Service Events (เหตุการณ์ด้านเทคโนโลยี/บริการ)` | — |
| 5 | `True-DTAC Merger(การรวมกิจการของ True และ ดีแทค)` | **no space** before `(` |
| 6 | `Emerging or Undefined Events (เหตุผลที่ยังไม่สามารถจัดกลุ่มได้)` | **`เหตุผล`** (*reason*) where every prompt says **`เหตุการณ์`** (*event*) |

`prompt.py:4402-4407` reproduces the enum **byte-for-byte including `Drvien`**, so the
retention prompt agrees with the schema:

```text
- `Market-Driven Events (เหตุการณ์ทางการตลาด)`
- `Crisis & Emergency Events (เหตุการณ์วิกฤตหรือภัยพิบัติ)`
- `Campaign-Drvien Events (เหตุการณ์ด้านเคมเปญต่างๆของบริษัท)`
- `Technology & Service Events (เหตุการณ์ด้านเทคโนโลยี/บริการ)`
- `True-DTAC Merger(การรวมกิจการของ True และ ดีแทค)`
- `Emerging or Undefined Events (เหตุผลที่ยังไม่สามารถจัดกลุ่มได้)`
```

`prompt.txt:71-88` does **not** — three of its six differ. See **D6**:

```text
   71          - `Market-Driven Events (เหตุการณ์ทางการตลาด)`
   72              - Definition (Eng): Events caused by market competition or changes from other service providers.
   73              - Example event (Thai): การเปิดตัวแพ็กเกจราคาถูกจากคู่แข่ง, การเปลี่ยนแปลงพฤติกรรมผู้บริโภค, การปรับลดราคาสมาร์ทโฟนหรืออุปกรณ์จากคู่แข่งที่มาพร้อมแพ็กเกจรายเดือนราคาพิเศษ, การเปิดตัวสินค้าใหม่
   74          - `Crisis & Emergency Events (เหตุการณ์วิกฤตหรือภัยพิบัติ)`
   75              - Definition (Eng): Events that impact the economy or daily life of customers.
   76              - Example event (Thai): การระบาดของโรค, ภัยธรรมชาติ, เหตุการณ์ทางการเมือง, ภาวะเศรษฐกิจถดถอย
   77          - `Campaign-Driven Events (เหตุการณ์ด้านเคมเปญต่างๆของบริษัท)`
   78              - Definition (Eng): Events caused by the launch or end of campaigns by True.
   79              - Example event (Thai): การสิ้นสุดโปรโมชั่นพิเศษ, การเปลี่ยนแปลงเงื่อนไขของแคมเปญ, การเปิดตัวแคมเปญใหม่ที่ลูกค้าไม่เข้าใจหรือรู้สึกว่าไม่คุ้มค่า
   80          - `Technology & Service Events (เหตุการณ์ด้านเทคโนโลยี/บริการ)`
   81              - Definition (Eng): Events related to changes in technology or services provided by True that affect customer experience.
   82              - Example event (Thai): การปรับปรุงเครือข่าย, ปัญหาด้านช่องทางบริการลูกค้า, ปัญหาด้านช่องทางบริการลูกค้า
   83          - `True-DTAC Merger (เหตุการณ์การรวมกิจการของ True และ Dtac)`
   84              - Definition (Eng): Events related to the merger of True and Dtac.
   85              - Example event (Thai): ความกังวลของลูกค้าเกี่ยวกับคุณภาพสัญญาณหลังการควบรวม, ความไม่แน่นอนเกี่ยวกับสิทธิประโยชน์เดิม, การเปลี่ยนแปลงระบบบริการหรือช่องทางติดต่อที่ทำให้ลูกค้ารู้สึกไม่สะดวก
   86          - `Emerging or Undefined Events (เหตุการณ์ที่ยังไม่สามารถจัดกลุ่มได้)`
   87              - Definition (Eng): Events that do not cover the above categories or are newly emerging trends affecting customer behavior.
   88              - Definition (Thai): เหตุการณ์ที่ไม่ครอบคลุมหมวดหมู่ข้างต้นหรือเป็นแนวโน้มใหม่ที่ส่งผลต่อพฤติกรรมของลูกค้า
```

**Rule: emit the `main.py:1046` spelling.** Cite `main.py:1046` for the string and
`prompt.txt:<definition line>` for the reasoning.

| Value (use the `main.py:1046` spelling) | Definition | Thai examples |
|---|---|---|
| `Market-Driven Events (...)` | `prompt.txt:72` | `prompt.txt:73` |
| `Crisis & Emergency Events (...)` | `prompt.txt:75` | `prompt.txt:76` |
| `Campaign-Drvien Events (...)` | `prompt.txt:78` | `prompt.txt:79` |
| `Technology & Service Events (...)` | `prompt.txt:81` | `prompt.txt:82` |
| `True-DTAC Merger(...)` | `prompt.txt:84` | `prompt.txt:85` |
| `Emerging or Undefined Events (...)` | `prompt.txt:87` | `prompt.txt:88` |

---

# 6. `churn_probability` — integer bands

The schema declares a bare integer with **no bounds**:

**`main.py:996`**

```python
"churn_probability": {"type": "INTEGER"},
```

The 0-100 range and the bands live only in the prompt, `prompt.txt:196-220`:

```text
  196      <churn_probability>
  197          Definition: When issue_type is identified, estimate the probability (0-100) that the customer will churn based on the severity of the network issue, the customer's sentiment, and their statements.
  198          Output: Integer 0-100 (required when issue_type is not null)
  199  
  200          Scoring Guide:
  201              80-100 (Very High Risk):
  202                  - Customer explicitly plans to switch or has already taken action
  203                  - Signal phrases: ขอรหัสย้ายค่าย, ซื้อซิมค่ายอื่นแล้ว, ย้ายแน่นอน, ไม่ไหวแล้ว, ติดต่อค่ายอื่นแล้ว, ไม่ใช้แล้ว
  204  
  205              60-79 (High Risk):
  206                  - Severe ongoing issue, multiple failed resolution attempts, strong frustration
  207                  - Signal phrases: แจ้งหลายรอบแล้วไม่แก้, ทนไม่ไหว, กำลังคิดจะย้าย, เคยแจ้งแล้วก็ไม่ดีขึ้น, เบื่อมาก, ถ้าไม่แก้ก็ย้าย
  208  
  209              40-59 (Moderate Risk):
  210                  - Noticeable issue affecting daily activities, some frustration expressed
  211                  - Signal phrases: ใช้งานลำบาก, ไม่ค่อยดี, รำคาญ, มีปัญหาบ่อย, ไม่เหมือนเดิม, เริ่มไม่ไหว
  212  
  213              20-39 (Low-Medium Risk):
  214                  - Minor inconvenience, first-time reporting, mild concern
  215                  - Signal phrases: อยากแจ้งให้ทราบ, ช่วยเช็คให้หน่อย, เพิ่งเริ่มมีปัญหา, ไม่แน่ใจว่าเป็นอะไร, บางทีช้า
  216  
  217              0-19 (Low Risk):
  218                  - Issue mentioned casually, customer willing to wait, shows patience
  219                  - Signal phrases: ไม่เป็นไร, รอได้, ถ้าแก้ได้ก็ดี, แค่อยากถาม, ไม่ได้เร่งร้อน
  220      </churn_probability>
```

For a testset, take the **midpoint** of the band the transcript justifies and cite that band's
signal-phrase line:

| Band | Label | Suggested value | Definition | Signal phrases |
|---|---|---:|---|---|
| 80-100 | Very High Risk | 90 | `prompt.txt:202` | `prompt.txt:203` |
| 60-79 | High Risk | 70 | `prompt.txt:206` | `prompt.txt:207` |
| 40-59 | Moderate Risk | 50 | `prompt.txt:210` | `prompt.txt:211` |
| 20-39 | Low-Medium Risk | 30 | `prompt.txt:214` | `prompt.txt:215` |
| 0-19 | Low Risk | 10 | `prompt.txt:218` | `prompt.txt:219` |

**Evidence spans, 80-100 Very High Risk** (`prompt.txt:203`):

- `ขอรหัสย้ายค่าย`
- `ซื้อซิมค่ายอื่นแล้ว`
- `ย้ายแน่นอน`
- `ไม่ไหวแล้ว`
- `ติดต่อค่ายอื่นแล้ว`
- `ไม่ใช้แล้ว`

**Evidence spans, 60-79 High Risk** (`prompt.txt:207`):

- `แจ้งหลายรอบแล้วไม่แก้`
- `ทนไม่ไหว`
- `กำลังคิดจะย้าย`
- `เคยแจ้งแล้วก็ไม่ดีขึ้น`
- `เบื่อมาก`
- `ถ้าไม่แก้ก็ย้าย`

**Evidence spans, 40-59 Moderate Risk** (`prompt.txt:211`):

- `ใช้งานลำบาก`
- `ไม่ค่อยดี`
- `รำคาญ`
- `มีปัญหาบ่อย`
- `ไม่เหมือนเดิม`
- `เริ่มไม่ไหว`

**Evidence spans, 20-39 Low-Medium Risk** (`prompt.txt:215`):

- `อยากแจ้งให้ทราบ`
- `ช่วยเช็คให้หน่อย`
- `เพิ่งเริ่มมีปัญหา`
- `ไม่แน่ใจว่าเป็นอะไร`
- `บางทีช้า`

**Evidence spans, 0-19 Low Risk** (`prompt.txt:219`):

- `ไม่เป็นไร`
- `รอได้`
- `ถ้าแก้ได้ก็ดี`
- `แค่อยากถาม`
- `ไม่ได้เร่งร้อน`

## `churn_probability` and `retention_outcome` are independent

Computed: spans that appear in both a churn-probability band and an outcome keyword list.

**No exact-string overlap** — though the concepts overlap heavily. `ขอรหัสย้ายค่าย` is an
80-100 signal (`prompt.txt:203`) and `ขอรหัสย้ายค้าย` (*sic*, with the typo) is a `churn`
keyword (`prompt.txt:59`).

**Do not derive one field from the other.** A call can end `save` after a 90 — the agent won
them back. `churn_probability` measures the severity of the *network issue*
(`prompt.txt:197`); `retention_outcome` measures how the call *ended*
(`prompt.py:4389`). Give each its own span.

---

# 7. The rest of the `network_issue` block

Schema, `main.py:983-1007`. `sub_reason`, `problem_statement_list` and the four `area_*`
fields are unscored and effectively unconstrained:

```python
  983      NETWORK_ISSUE_SCHEMA = {
  984          "type": "OBJECT",
  985          "nullable": True, # Fixes the NULL error in Vertex AI
  986          "properties": {
  987              "issue_type": {
  988                  "type": "STRING",
  989                  "enum": ["Speed", "Outage", "Drop", "Coverage", "FUP", "Installation", "Support", "Voice Quality"]
  990              },
  991              "sub_reason": {"type": "STRING"},
  992              "problem_statement_list": {
  993                  "type": "ARRAY",
  994                  "items": {"type": "STRING"}
  995              },
  996              "churn_probability": {"type": "INTEGER"},
  997              "area": {
  998                  "type": "OBJECT",
  999                  "properties": {
 1000                      "area_tag_province": {"type": "STRING", "nullable": True},
 1001                      "area_tag_district": {"type": "STRING", "nullable": True},
 1002                      "area_tag_sub_district": {"type": "STRING", "nullable": True},
 1003                      "area_tag_landmark": {"type": "STRING", "nullable": True}
 1004                  }
 1005              }
 1006          }
 1007      }
```

| Field | Schema | Prompt rule | Constraint the schema does **not** enforce |
|---|---|---|---|
| `sub_reason` | `main.py:991` | `prompt.txt:172-183` | English, max 800 chars (`prompt.txt:181`) |
| `problem_statement_list` | `main.py:992-995` | `prompt.txt:185-194` | **customer speech only**, verbatim Thai, min 1 (`prompt.txt:188-190`, `:193`) |
| `churn_probability` | `main.py:996` | `prompt.txt:196-220` | integer 0-100 (`prompt.txt:198`) |
| `area_tag_province` | `main.py:1000` | `prompt.txt:233` | English transliteration (`prompt.txt:228`) |
| `area_tag_district` | `main.py:1001` | `prompt.txt:234` | English transliteration |
| `area_tag_sub_district` | `main.py:1002` | `prompt.txt:235` | English transliteration |
| `area_tag_landmark` | `main.py:1003` | `prompt.txt:236` | English transliteration |

`problem_statement_list` is the one field a testset can violate by accident:

```text
- Extract ONLY from the customer's speech, NOT the agent's
- Include multiple statements if the customer describes the issue in different ways or at different points in the conversation
- Preserve the original Thai phrasing exactly as spoken
```

Spans must come **only from the customer**, never the agent — the same constraint as
`ev_<label>`, so reuse the same spans.

**Location levels are independent** (`prompt.txt:225`): never infer province from district. If
the transcript says only "แถวลาดพร้าว", fill `area_tag_district` and leave province null.

Note also that `NETWORK_ISSUE_SCHEMA` (`main.py:983-1007`) declares **no `required` list**, so
`issue_type` may legally be absent from a `network_issue` object that is itself present — a
state `prompt.txt:99-104` forbids but the schema permits.

---

# 8. Divergence register

Every place the schema, the scorer and the prompts disagree. **Each is a trap for a
ground-truth label**, so each carries a stated rule.

## D1 — A 12th reason exists in the prompt and is structurally unscorable

| Source | Says |
|---|---|
| `prompt.txt:38-40` | reason #10 is `true point, dtac reward` |
| `main.py:972` | enum has **11** values; not among them |
| `fact_checker.py:857-869` | `target_classes` has **11** values; not among them |
| `prompt.py:23-24` … `prompt.py:1789` | historical retention prompts **did** list it (v1 through v8_5) |
| `prompt.py:4380` | current retention prompt folds it into `other` |

A model prompted from `prompt.txt` can emit a reason outside the enum. Worse, the value
**contains a comma**, so `get_reasons_set` (`fact_checker.py:877`) splits it into `true point`
and `dtac reward` — two tokens matching no scored class. It registers as neither a TP nor a
recognisable FP; it silently becomes a miss against whatever the true class was.

**Rule: never label `true point, dtac reward`. Use `other`, cite `prompt.py:4380`.**

## D2 — The retention prompt shipped without `undefined` for its first four versions, but the scorer has always scored it

| Source | Classes |
|---|---|
| `main.py:1018` (schema enum) | `churn`, `save`, `unknown`, **`undefined`** |
| `fact_checker.py:791` (scored) | `save`, `churn`, `unknown`, **`undefined`** |
| `fact_checker.py:769` (report order) | `save`, `churn`, `unknown`, **`undefined`**, `total` |
| `prompt.py:34-37` (`prompt_v1`) | `churn`, `save`, `unknown` — **three only** |
| `prompt.py:123-126` (`prompt_v2`) | `churn`, `save`, `unknown` — **three only** |
| `prompt.py:494` (`prompt_v5`) | first version to define `undefined` |
| `prompt.py:4399` (`prompt_v9_16`) | defines `undefined` |
| `system_prompt/retention.yml` | never enumerates outcomes at all |

**This is the divergence the brief predicted, and it is worse than "the prompt is missing a
class."** Under `prompt_v1` through `prompt_v4`, `undefined` could appear in ground truth but
never in a prediction. Its recall was therefore structurally 0 and its precision structurally
0 — and it still carried `weight = TP + FN` (`fact_checker.py:808`) into the weighted average
at `fact_checker.py:830-845`, dragging the headline number down for a reason no amount of
model improvement could fix.

The live system prompt shipped in the repo (`system_prompt/retention.yml`) still enumerates
nothing; the class list reaches the model only through the SharePoint user prompt
(`fact_checker/retention.yml:35-39`), which is **not under version control**.

**Rule: the testset must include `undefined` items.** That is the only way the harness can
show whether a model has been told the class exists. Cite `main.py:1018` + `prompt.py:4399`,
never `prompt.txt:63`.

## D3 — `undefine` vs `undefined`

`prompt.txt:63` spells the fourth class **`undefine`**, with no trailing `d`:

**`prompt.txt:63`**

```text
- `undefine`
```

`main.py:1018` and `fact_checker.py:791` both spell it `undefined`. A model copying the prompt
emits `undefine`. `pre_process` lowercases and strips (`fact_checker.py:733-739`) but does not
correct spelling, so `undefine` never equals `undefined`. Every such row becomes a **false
negative on `undefined` plus a phantom column in the confusion matrix** — the crosstab at
`:783-789` builds columns from observed values and `:793-794` reindexes to their union, so an
`undefine` column appears with a zero diagonal.

**Rule: ground truth is always `undefined`. Never `undefine`.**

## D4 — Indecision is `save` under retention and `unknown` under MNP, and `prompt.txt` contradicts itself

| Source | Says |
|---|---|
| `prompt.txt:55` | `save` covers a customer who "initially requested to think about it but did not explicitly cancel" |
| `prompt.txt:61` | `unknown` covers a customer who "is undecided about whether to continue or cancel" |
| `prompt.py:4397` | indecision "is counted as a 'save' because the final decision to churn has not been executed or confirmed" |
| `prompt.py:4398` | `unknown` is narrowed to a call "technically interrupted or crashing (e.g. dropped call)" |

`prompt.txt:55` and `prompt.txt:61` **contradict each other on the same fact pattern**: a
customer who says "ขอคิดดูก่อน" and hangs up. Line 55 makes it `save`; line 61 makes it
`unknown`. The MNP prompt cannot settle its own case.

**Rule: indecision → `save`, cite `prompt.py:4397`. Reserve `unknown` for dropped or
interrupted calls, cite `prompt.py:4398`. Never cite `prompt.txt:61` for an indecision item.**

## D5 — `Prepaid` is a product in `prompt.txt` and not in the schema, and `product_type` does not exist

| Source | Product values |
|---|---|
| `main.py:1037-1040` | `Postpaid`, `TOL`, `TVS`, `unknown` — `additionalProperties: False` at `:1042` |
| `fact_checker.py:971-973` | `postpaid`, `tol`, `tvs`, `unknown` |
| `prompt.py:4321-4324` | `Postpaid`, `TOL`, `TVS`, `unknown` — matches |
| `prompt.txt:241-249` | `Postpaid`, **`Prepaid`**, `TVS`, `TOL` — **has `Prepaid`, lacks `unknown`** |
| `prompt.txt:258` | "product_type must be one of: Postpaid, Prepaid, TVS, TOL, or null" |

Two separate problems.

First, `prompt.txt` offers a `Prepaid` value the retention schema cannot represent —
`additionalProperties: False` (`main.py:1042`) rejects the key outright.

Second, `prompt.txt`'s `product_type` is **a field that does not exist in the retention
schema at all**. `NETWORK_ISSUE_SCHEMA` (`main.py:983-1007`) contains `issue_type`,
`sub_reason`, `problem_statement_list`, `churn_probability` and `area` — no `product_type`.
Yet `prompt.txt:254` and `:258` make it mandatory whenever `issue_type` is set. A retention
model following `prompt.txt`'s validation checklist would fail a check on a field it cannot
emit.

**Rule: four product keys only. A prepaid migration is `postpaid` + `reason = post to pre` +
`retention_outcome = churn` (`prompt.py:4392`). Never emit `product_type`.**

## D6 — Three `call_event_detection` values are spelled differently in `prompt.txt`

| # | `main.py:1046` (authoritative) | `prompt.txt` | Match? |
|---|---|---|---|
| 1 | `Market-Driven Events (เหตุการณ์ทางการตลาด)` | `:71`, same | yes |
| 2 | `Crisis & Emergency Events (เหตุการณ์วิกฤตหรือภัยพิบัติ)` | `:74`, same | yes |
| 3 | `Campaign-Drvien Events (…)` | `:77` spells it `Campaign-**Driven**` | **no** |
| 4 | `Technology & Service Events (…)` | `:80`, same | yes |
| 5 | `True-DTAC Merger(การรวมกิจการของ True และ ดีแทค)` | `:83` adds a **space** before `(`, and reads `(เหตุการณ์การรวมกิจการของ True และ **Dtac**)` | **no** |
| 6 | `Emerging or Undefined Events (**เหตุผล**ที่ยังไม่สามารถจัดกลุ่มได้)` | `:86` reads `(**เหตุการณ์**ที่ยังไม่สามารถจัดกลุ่มได้)` | **no** |

The direction is the interesting part: the **schema carries the typo** (`Drvien`) and
`prompt.txt` carries the *correct* spelling (`Driven`). A model following `prompt.txt` produces
a string that is **not in the enum**. Under constrained decoding the value is coerced to
something arbitrary; without it the value passes through and every downstream `==` fails.

`prompt.py:4404` reproduces `Drvien` faithfully, so the retention prompt is consistent with the
schema and `prompt.txt` is the outlier.

**Rule: emit the `main.py:1046` strings, typos and all.**

## D7 — The reason evidence field is `keyword` in the schema and `Phrase` in the live system prompt

| Source | Key |
|---|---|
| `main.py:975-978` | **`keyword`**, marked `required` at `:980` |
| `system_prompt/retention.yml:23`, `:27`, `:31` | **`Phrase`** (capital P) |
| `prompt.py:4383`, `:4385`, `:4387` | **`Phrase`** |
| `prompt.txt` | n/a — MNP uses a flat `keyword` string |

**`main.py:977`**

```python
"description": "List keywords or short phrases directly from the audio that explicitly indicate or support the reason. Use comma separation. Use empty string if not applicable."
```

**`system_prompt/retention.yml:23`**

```yaml
"Phrase": "คำพูดของลูกค้าที่สื่อถึงเหตุผล"
```

The **live system prompt shipped in the retention repo** instructs the model to emit `Phrase`
while the function-calling schema declares `keyword` and marks it required (`main.py:980`).

This does not break scoring — `prepare_predictions` reads only `.get("reason")`
(`fact_checker.py:609`, `:613`, `:617`) and never touches the evidence field. That is precisely
the problem: **the production pipeline has never captured the evidence span in a validated
field**, and nothing downstream would notice if it were empty.

**Rule for this testset: our `ev_<label>` spans are the field production lost.** Store them
under `keyword` if a JSON fixture needs a schema-valid name; cite `main.py:975-978`.

## D8 — The shipped retention system-prompt example is malformed JSON

`system_prompt/retention.yml:34-45` shows the model an example with **no commas between the
`network_issue` keys**:

```yaml
   34                  "network_issue": {
   35                      "issue_type": "Speed"
   36                      "sub_reason": "Complete signal loss in residential area since last week"
   37                      "problem_statement_list": ["เน็ตช้ามากจนดูวิดีโอไม่ได้เลย", "สัญญาณไม่เสถียรทำให้โทรออกบ่อยๆไม่ได้"]
   38                      "churn_probability": 75
   39                      "area": {
   40                          "area_tag_province": "province"
   41                          "area_tag_district": "district"
   42                          "area_tag_sub_district": "sub_district"
   43                          "area_tag_landmark": "landmark"
   44                      }
   45                  }
```

`"issue_type": "Speed"` is followed directly by `"sub_reason"` with no separator, and the same
holds for every subsequent key and for all four keys inside `area`. **The example the model is
shown cannot be parsed as JSON.**

Function calling makes this survivable — the schema, not the example, drives output structure —
but a model asked to imitate the example free-form will reproduce the error.

**Relevance to the testset:** if the candidate model is evaluated **without** forced tool
calling, this is a likely source of parse failures, and a parse failure is exactly the shape of
the degenerate arm already covered by `retention_arm_empty.csv`. Keep at least one item that
exercises it.

## D9 — Reason rank is discarded, so `main` / `secondary` / `third` are not separable

`fact_checker.py:871-879` unions the three ranks into one set; `:882-883` applies it to both
frames. **No metric distinguishes a main reason from a third reason.**

**Rule: never build an item whose correctness depends on rank.** State ranks for realism, but
`rule_reason` must justify **membership**, never position.

## D10 — Reason cells are comma-split, so a comma in a pasted keyword list manufactures labels

`fact_checker.py:877` splits every cell on `,`. The keyword lists in `prompt.txt` are
themselves comma-delimited, so a fixture author who pastes a keyword list into a reason cell
manufactures a dozen nonsense labels — each of which becomes an FP.

**Rule: a reason cell holds label strings only, never evidence.** Evidence lives in
`ev_<label>`.

## D11 — `call_result` drops null ground truth but not null predictions

**`fact_checker.py:756`**

```python
call_result_df = merged_df.dropna(subset=['call_result_gt']).copy()
```

Only the ground-truth side is dropped. An item with no ground-truth outcome vanishes from the
`call_result` dimension while still being scored on `reason` and `product`, so **the three
dimensions have three different denominators**. An item with no *predicted* outcome stays in
and counts against the model.

**Rule: every testset item carries a non-null `retention_outcome`.**

## D12 — Four sources order the reasons four different ways

| Source | Position of `other` |
|---|---|
| `main.py:964` (schema prose) | **8th** |
| `main.py:972` (schema enum) | **11th** |
| `fact_checker.py:865` (scorer) | **8th** |
| `prompt.txt:46` (MNP) | **12th** |
| `prompt.py:4377` (retention) | **11th** |

The *set* is identical in all of them; only the order moves. Harmless to scoring — every
consumer is a set or a dict lookup — but it defeats any citation identifying a reason by
position.

**Rule: cite reasons by string, never by index.**

## D13 — `prompt.txt` names the outcome field `call_result`; the retention schema names it `retention_outcome`

| Source | Field name |
|---|---|
| `prompt.txt:52` | `call_result` |
| `main.py:1016` | `retention_outcome` |
| `prompt.py:4389` | `retention_outcome` |
| `fact_checker.py:622` | reads `retention_outcome`, writes a column named `call_result` |
| MNP's own `main.py:732` | reads `ai_result.get("call_result", "")` |

The two apps genuinely use different JSON keys for the same concept, and the retention scorer
renames one to the other on `fact_checker.py:622`. This is not a bug — it is a naming seam —
but it means **a `rule_retention_outcome` citation into `prompt.txt` is citing a different
field name than the one the retention model emits.** Always pair a `prompt.txt` outcome
citation with `main.py:1016` or `prompt.py:4389`.

## D14 — `churn_probability` has a documented range the schema does not enforce

`main.py:996` declares `{"type": "INTEGER"}` with no `minimum` or `maximum`. `prompt.txt:198`
says "Integer 0-100". A model emitting `250` is schema-valid.

**Rule: ground-truth values are band midpoints in 0-100, cited to `prompt.txt:196-220`.** If
the harness records out-of-range predictions, that is a finding about the model, not a fixture
error.

---

# 9. Quick reference

Everything a label needs, on one screen.

**`reason`** — 11, `main.py:972` / `fact_checker.py:857-869`, **scored**

`network` · `promotion related` · `device promotion related` · `save cost` · `contract end` ·
`sale upsell problem` · `dissatisfied service` · `post to pre` · `customer reason` ·
`down sell not success` · `other`

**`retention_outcome`** (scored as `call_result`) — 4, `main.py:1018` / `fact_checker.py:791`,
**scored**

`save` · `churn` · `unknown` · `undefined`

**`product`** — 4, `main.py:1037-1040` / `fact_checker.py:971-973`, **scored**

schema `Postpaid` · `TOL` · `TVS` · `unknown` → scored `postpaid` · `tol` · `tvs` · `unknown`

**`issue_type`** — 8, `main.py:989`, not scored

`Speed` · `Outage` · `Drop` · `Coverage` · `FUP` · `Installation` · `Support` · `Voice Quality`

**`call_event_detection`** — 6, `main.py:1046`, not scored, **typos mandatory**

`Market-Driven Events (เหตุการณ์ทางการตลาด)` ·
`Crisis & Emergency Events (เหตุการณ์วิกฤตหรือภัยพิบัติ)` ·
`Campaign-Drvien Events (เหตุการณ์ด้านเคมเปญต่างๆของบริษัท)` ·
`Technology & Service Events (เหตุการณ์ด้านเทคโนโลยี/บริการ)` ·
`True-DTAC Merger(การรวมกิจการของ True และ ดีแทค)` ·
`Emerging or Undefined Events (เหตุผลที่ยังไม่สามารถจัดกลุ่มได้)`

**`churn_probability`** — integer, `main.py:996`, not scored. Band midpoints: 90 / 70 / 50 /
30 / 10.

## Acceptable `rule_*` citation forms

| Field | Preferred citation |
|---|---|
| `rule_reason` | `prompt.txt:<keyword line>` — the span is in that list |
| `rule_reason` (retention-specific wording) | `prompt.py:4327-4381` — the current definitions |
| `rule_retention_outcome` | `prompt.py:4389-4399` first; `prompt.txt:55` / `:58` / `:61` second, paired with `main.py:1016` |
| `rule_product` | `prompt.py:4321-4324` |
| `rule_issue_type` | `prompt.txt:<definition line>`, **plus** `prompt.txt:<disambiguation line>` whenever two categories could match |
| `rule_churn_probability` | `prompt.txt:<band signal line>` |
| `rule_call_event_detection` | `main.py:1046` for the string, `prompt.txt:<definition line>` for the reasoning |

## Citation forms that are NOT acceptable

- A citation to a line that merely **lists** the value (`main.py:972` alone). The enum proves
  the value is legal, not that it is correct for this transcript. Pair it with a definition or
  keyword line.
- `prompt.txt:63` for `undefined` — wrong spelling (**D3**).
- `prompt.txt:38-40` for anything — prompt-only reason (**D1**).
- `prompt.txt:61` for an indecision item — contradicted by `prompt.py:4397` (**D4**).
- Anything under `prompt.txt:239-250` (`product_type`) — the field does not exist (**D5**).
- A `prompt.txt` `call_event_detection` string as the emitted value — three of six are not in
  the enum (**D6**).
- Anything identifying a reason by ordinal position (**D12**).
- A citation that justifies rank rather than membership (**D9**).
