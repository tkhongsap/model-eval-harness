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

## Why it settles the question by itself

Tar reported sentiment_qa calls returning 30,000–50,000 output tokens. The diagnosis is that
most of that is *thinking*, not answer — but until 2026-08-20 that was an estimate written
in `DEVLOG.md` and never measured.

We have now measured it **off-platform**, on OpenRouter, using production's own
`system_prompt.txt` and the real `user_config.xlsx` field definitions:

| arm | median completion tokens | of which thinking | keys returned | valid JSON |
|---|---:|---:|---:|---:|
| production's regime (unlimited thinking) | ~13,000 | **~75%** | 118 | yes |
| thinking off | **~3,900** | 0 | 118 | yes |

So roughly **three quarters of the output is reasoning**, and turning it off still returns
the complete 118-key object. That is a ~70% reduction in output tokens with the full schema
intact.

**But OpenRouter is not Vertex.** It has no `thinkingConfig.thinkingBudget`; the nearest
lever is `reasoning.effort`. Our numbers are therefore a faithful test of the *prompt and
schema* and a proxy for the *budget*. `thoughtsTokenCount` from one real batch response is
the direct measurement, and it costs nothing to produce because the batch already emits it.

## What we would do with it

Confirm — or correct — the recommendation below before anyone changes a production config.

## The three changes we would then propose, in order of confidence

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

**ทำไมถึงสำคัญ:** เราวัดนอกแพลตฟอร์ม (OpenRouter) ด้วย system prompt และ user_config จริงของ production
พบว่าประมาณ **75% ของ output tokens เป็นการ "คิด" (thinking)** ไม่ใช่คำตอบ และเมื่อปิด thinking
เหลือ ~3,900 tokens จาก ~13,000 โดยยังได้ครบทั้ง **118 keys** และ JSON ถูกต้อง

แต่ OpenRouter ไม่ใช่ Vertex และไม่มี `thinkingBudget` ตัวเลข `thoughtsTokenCount` จาก batch จริง
จะยืนยันเรื่องนี้ได้โดยตรง และ batch ก็สร้างตัวเลขนี้อยู่แล้ว ไม่มีต้นทุนเพิ่ม

**สิ่งที่เราจะเสนอหลังจากนั้น:** ปรับ `thinkingBudget: -1` เป็นค่าจำกัด (มี 3 จุด โดยจุดที่ปริมาณมากที่สุดคือ
`qa_pipeline_tasks.yml:76` ไม่ใช่ fact_check), แก้ prompt ที่ขัดแย้งกันเอง (`system_prompt.txt:18-24`
สั่งให้คิดเป็น 5 ขั้น แต่บรรทัด `:26` บอกว่าไม่ต้องเขียนแบบ step by step), และตั้ง `maxOutputTokens`
ให้ต่ำกว่า 65535 ซึ่งตอนนี้เป็นค่าสูงสุดจึงไม่ได้กันอะไรเลย

**สิ่งที่เรายังไม่ได้พิสูจน์:** เรายังไม่ได้พิสูจน์ว่าการลด thinking จะไม่กระทบความแม่นยำ เพราะยังไม่มีชุดข้อมูล
sentiment_qa ที่มี ground truth ในระบบ eval — เรื่องนี้ต้องทำแยกต่างหาก
