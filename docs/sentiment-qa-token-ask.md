---
type: draft
created: 2026-08-20
status: ready to send -- not sent
tags: [work/true, project/intelligence-layer, sentiment-qa, cost]
---

# Ask: one Vertex response's token breakdown for sentiment_qa

**What is needed:** the `usageMetadata` block from **one** real sentiment_qa batch response.
Not a document, not a dashboard export — one JSON object that already exists in the batch
output, containing three integers:

```json
"usageMetadata": {
  "promptTokenCount":     ...,
  "candidatesTokenCount": ...,
  "thoughtsTokenCount":   ...
}
```

**No customer data is involved.** `usageMetadata` is a sibling of the response content, not
part of it. Copying only that block sends three counts and nothing a customer said.

## First: which number is 30,000-40,000?

**This is the question, and it decides which fix is right.** Tar reported "30,000 to 40,000
tokens". Measured off-platform on 120 calls using production's own `system_prompt.txt` and the
real `user_config.xlsx`:

| what | measured | in Tar's range? |
|---|---:|---|
| **input (prompt) tokens** | **30,985 - 32,825**, median 31,926 | **yes -- dead centre, on every call** |
| output (completion) tokens | 8,564 - 17,054, median 11,961 | **no -- not one call of 120 reached 30,000** |
| total (input + output) | ~35,400 - 44,056 | close |

We could **not** reproduce 30-40k *output* tokens, and we tried: raising the reasoning budget
explicitly changes nothing (identical to the token), output does not scale with call length
(correlation 0.12), and Gemini 2.5 **Pro is lower than Flash**, not higher. Nothing was
truncated -- no call ended with `finish_reason: length`.

The input, by contrast, sits in that range on **every single call** and barely moves, which is
also how a stable "30,000 to 40,000" gets quoted. It is almost entirely one thing:
`user_config.xlsx` is **94,174 characters = ~31,400 tokens** of Thai field definitions, re-sent
in full on every call, with `prompt_tokens_details.cached_tokens: 0` -- nothing is cached.

**So please confirm which counter the 30-40k came from.** The `usageMetadata` block above
answers it directly, because it carries all three.

## Where every token actually goes, measured

The structured output is real and it IS a long extended JSON -- but it is not where the
tokens are. Read against the production code
(`prep_payload_task._get_analysis_schema`, 89 leaf fields / 30 objects / 1 array), and
against a real filled response:

| component | measured size | approx tokens |
|---|---:|---:|
| `user_config.xlsx` field definitions | 94,174 chars | **~31,400** |
| `response_schema` sent in `generationConfig` | 13,457 chars | ~4,500 |
| `system_prompt.txt` | 7,012 chars | ~2,300 |
| the call transcript itself | ~3,100 chars | ~1,000 |
| **INPUT subtotal** | | **~35,000-38,000** |
| | | |
| **the filled 118-key JSON answer** | **8,353 chars** | **~2,800** |
| thinking, when `thinkingBudget: -1` | -- | ~9,800 |
| **OUTPUT subtotal** | | **~12,600** |

**The structured answer is about 2,800 tokens.** Even with unlimited thinking on top, the
whole output lands near 12,600 -- an order of magnitude short of 30-40k. The input is the
side that sits in that range, and `user_config.xlsx` alone is most of it.

Within the answer, `service_quality` is 4,258 of the 8,353 characters -- **51% of the output
is 24 blocks of `{evaluation, reason}` with a mandatory Thai free-text reason each**
(`system_prompt.txt:26`). That is the one part of the output worth questioning on size, and
no thinking budget touches it.

Two further measurements, both from enforcing the real schema rather than plain JSON mode:

  * Enforcing `response_schema` **reduced** output rather than growing it -- 2,862 tokens
    against 3,440 for `{"type": "json_object"}` -- and still returned all 118 keys.
  * `response_schema` **plus** unlimited thinking failed outright (`finish_reason: error`,
    no content). Thinking-off with the same schema succeeded. That matches the reliability
    pattern in the table below.

## What we measured about thinking, which is true either way

| arm | median completion tokens | of which thinking | keys returned | valid JSON |
|---|---:|---:|---:|---:|
| production's regime (unlimited thinking) | 11,961 | **73%** | 118 | 14/24 |
| thinking off | **3,440** | 0 | 118 | **22/24** |

Roughly **three quarters of the output is reasoning**, and turning it off still returns the
complete 118-key object -- a ~71% reduction in output tokens with the full schema intact.

**But OpenRouter is not Vertex.** It has no `thinkingConfig.thinkingBudget`; the nearest
lever is `reasoning.effort`. Our numbers are therefore a faithful test of the *prompt and
schema* and a proxy for the *budget*. `thoughtsTokenCount` from one real batch response is
the direct measurement, and it costs nothing to produce because the batch already emits it.

## What we would do with it

Confirm — or correct — the recommendation below before anyone changes a production config.

## The changes we would propose -- but the order depends on your answer above

**If the 30-40k is INPUT**, the lever is the prompt, not the thinking budget:

0. **Cache or trim the ~31,400-token field-definition prompt.** It is identical on
   every call and is currently re-sent and re-charged each time; our records show
   `cached_tokens: 0`, i.e. nothing is cached at all. Vertex context caching on a
   fixed prefix is the direct fix; trimming `user_config.xlsx` to the fields actually
   scored is the other.

**If the 30-40k is OUTPUT**, or regardless, these still hold:

1. **`thinkingBudget: -1` → a finite value.** It is set at **three** call sites, and the
   highest-volume one is the daily batch, not the fact-check path usually quoted:
   - `config/sentiment_qa/qa_pipeline_tasks.yml:76`  ← daily volume
   - `config/sentiment_qa/qa_pipeline_fact_check.yml:27`
   - `config/sentiment_qa/qa_pipeline_user_playground.yml:27`

2. **The prompt contradicts itself, and fixing it is free.** `system_prompt.txt:18–24`
   instructs *"When evaluating, think: 1…5"* — five numbered reasoning steps. The Thai
   sentence at `:26` then says the `reason` field must **not** be written step by step and
   should be summarised directly. The model is being told both things about the same task.

3. **`maxOutputTokens: 65535` is not a backstop.** It is the maximum the API allows, so
   nothing currently caps a runaway. Retention has a documented Gemini runaway on a *smaller*
   schema — `finish_reason=length`, 23,529 characters looping inside a free-text subtree
   (`src/evalgen/decoding.py:28-33`). sentiment_qa's schema has far more free-text subtrees
   than that one did, and 23 mandatory Thai `reason` fields.

## What we are explicitly **not** claiming

We have **not** shown that capping thinking preserves answer quality. We cannot: there is no
labelled sentiment_qa batch in the evaluation harness — no ground truth, no scorer, no label
space. What we can say is that the capped arm still returned valid JSON with the full 118-key
object on every call we made.

Measuring whether accuracy holds needs a labelled QA batch. If that matters before the config
changes, it is a separate and larger piece of work, and worth scoping deliberately rather than
inferring from token counts.

---

## Thai

**สิ่งที่ขอ:** บล็อก `usageMetadata` จากผลลัพธ์ sentiment_qa แบบ batch จริง **เพียง 1 รายการ** — มีแค่ตัวเลข
3 ตัว: `promptTokenCount`, `candidatesTokenCount`, `thoughtsTokenCount`

**ไม่มีข้อมูลลูกค้าเกี่ยวข้อง** — `usageMetadata` อยู่แยกจากเนื้อหาคำตอบ ส่งเฉพาะบล็อกนี้ได้เลย

**คำถามแรก: ตัวเลข 30,000-40,000 คือ input หรือ output?** เราวัด 120 calls นอกแพลตฟอร์ม (OpenRouter)
ด้วย system prompt และ user_config จริงของ production พบว่า **input = 30,985-32,825 tokens ทุก call**
(อยู่ในช่วง 30-40k พอดี) แต่ **output = 8,564-17,054 tokens ไม่มี call ไหนถึง 30,000 เลย**

ถ้าเป็น input ตัวการคือ `user_config.xlsx` ขนาด 94,174 ตัวอักษร ประมาณ 31,400 tokens ที่ถูกส่งซ้ำทุกครั้ง
และตอนนี้ไม่มีการ cache เลย (`cached_tokens: 0`) วิธีแก้คือ context caching หรือลดขนาด prompt

**ส่วนเรื่อง thinking (จริงทั้งสองกรณี):** ประมาณ **73% ของ output tokens เป็นการ "คิด"** และเมื่อปิด
thinking เหลือ ~3,440 tokens จาก ~11,961 โดยยังได้ครบทั้ง **118 keys** และ JSON ถูกต้อง

แต่ OpenRouter ไม่ใช่ Vertex และไม่มี `thinkingBudget` ตัวเลข `thoughtsTokenCount` จาก batch จริง
จะยืนยันเรื่องนี้ได้โดยตรง และ batch ก็สร้างตัวเลขนี้อยู่แล้ว ไม่มีต้นทุนเพิ่ม

**สิ่งที่เราจะเสนอหลังจากนั้น:** ปรับ `thinkingBudget: -1` เป็นค่าจำกัด (มี 3 จุด โดยจุดที่ปริมาณมากที่สุดคือ
`qa_pipeline_tasks.yml:76` ไม่ใช่ fact_check), แก้ prompt ที่ขัดแย้งกันเอง (`system_prompt.txt:18-24`
สั่งให้คิดเป็น 5 ขั้น แต่บรรทัด `:26` บอกว่าไม่ต้องเขียนแบบ step by step), และตั้ง `maxOutputTokens`
ให้ต่ำกว่า 65535 ซึ่งตอนนี้เป็นค่าสูงสุดจึงไม่ได้กันอะไรเลย

**สิ่งที่เรายังไม่ได้พิสูจน์:** เรายังไม่ได้พิสูจน์ว่าการลด thinking จะไม่กระทบความแม่นยำ เพราะยังไม่มีชุดข้อมูล
sentiment_qa ที่มี ground truth ในระบบ eval — เรื่องนี้ต้องทำแยกต่างหาก
