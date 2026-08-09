---
type: draft
created: 2026-08-09
status: ready to send -- not sent
tags: [work/true, project/intelligence-layer, evaluation, blocker]
---

# Ask 1 email: the two header rows

**Why this is the highest-value 5 minutes available to this project.** Every report the
harness prints is stamped `RECONCILED: NO`, and no code path in it can print `YES`, because
it has never scored one real labelled batch. `adapters/retention.py::load_workbook()`
refuses to run rather than guess the workbook's layout. Two header rows -- containing **no
customer record at all** -- settle that layout exactly. Nothing else on the roadmap unblocks
as much for as little.

The full reasoning is in `docs/data-contract.md`; this is just Ask 1, extracted so it can be
sent on its own without asking anyone to read a specification first.

**Send it as its own email.** Asks 2-4 in the data contract need a privacy conversation.
This one does not, and bundling it with them is what has kept it unsent since 2026-08-04.

---

## English

**To:** Anan Sanongchitcharorn (Retention app owner) — cc whoever maintains the
`fact_check` ground-truth workbook
**Subject:** 5-minute ask: two header rows from the Retention ground-truth workbook (no data)

> Hi Anan,
>
> I'm building an evaluation harness that scores Retention call-labelling the same way
> production's own fact-checker does, so we can compare models on like-for-like numbers.
> It works today, but every report it produces carries a "not reconciled" stamp, because it
> has never been checked against one real labelled batch.
>
> To read the ground-truth workbook the way `prepare_ground_truth` does, I need one small
> thing:
>
> **The first two rows only** of
> `/Control Management/Call Center/Sentiment Analysis Retention Reason/fact_check/ground_truth/input/Post Evaluate Sentiment Analysis Retention.xlsm`,
> sheet **`Raw Data with User`**.
>
> Header rows only — **zero data rows**. A screenshot, a paste into your reply, or a
> two-row copy are all equally fine.
>
> **Why those two rows specifically.** `prepare_ground_truth` builds its column names by
> merging a two-row header block (`fact_checker.py:517-525`) rather than reading a single
> header row. We are currently guessing how those two rows combine, and a wrong guess means
> the harness joins on the wrong columns and produces numbers that look plausible and are
> not. The two rows settle it exactly.
>
> **There is no customer record in what I'm asking for**, so there's nothing to clear
> before sending it — that is deliberate, and it's why this ask is separate from the
> larger data request.
>
> What it unblocks: the harness can then read one real batch and reconcile its numbers
> against the app's existing Gemini fact-check report. Until that happens, everything it
> reports is a screening result on synthetic data, not a decision.
>
> Happy to walk through the harness or the wider data request whenever it's useful — but
> those two rows are the only thing blocking the next step.
>
> Thanks,
> Totrakool

---

## ภาษาไทย

> เรียนคุณอนันต์
>
> ผมกำลังพัฒนาเครื่องมือประเมินผล (evaluation harness) ที่ให้คะแนนการติดป้ายกำกับสาย
> Retention ด้วยวิธีเดียวกับ fact-checker ที่ใช้อยู่บนโปรดักชัน เพื่อให้เปรียบเทียบโมเดล
> ต่าง ๆ บนตัวเลขชุดเดียวกันได้ ตอนนี้เครื่องมือทำงานได้แล้ว แต่รายงานทุกฉบับที่ออกมายังถูก
> ประทับว่า "ยังไม่ได้กระทบยอด" (not reconciled) เพราะยังไม่เคยตรวจสอบกับชุดข้อมูลจริงที่
> ติดป้ายกำกับไว้แล้วแม้แต่ชุดเดียว
>
> เพื่อให้อ่านไฟล์ ground truth ได้แบบเดียวกับที่ `prepare_ground_truth` อ่าน ผมขอเพียง
> สิ่งเดียวครับ
>
> **เฉพาะสองแถวแรก** ของไฟล์
> `/Control Management/Call Center/Sentiment Analysis Retention Reason/fact_check/ground_truth/input/Post Evaluate Sentiment Analysis Retention.xlsm`
> ชีท **`Raw Data with User`**
>
> ขอเป็นแถวหัวตารางเท่านั้น **ไม่ต้องมีแถวข้อมูลเลย** จะส่งเป็นภาพหน้าจอ วางในอีเมลตอบกลับ
> หรือคัดลอกมาสองแถว แบบไหนก็ได้เหมือนกันครับ
>
> **ทำไมต้องเป็นสองแถวนี้** เพราะ `prepare_ground_truth` สร้างชื่อคอลัมน์จากการรวมหัวตาราง
> สองแถวเข้าด้วยกัน (`fact_checker.py:517-525`) ไม่ใช่อ่านหัวตารางแถวเดียว ตอนนี้เราต้อง
> เดาว่าสองแถวนั้นรวมกันอย่างไร ซึ่งถ้าเดาผิด เครื่องมือจะ join ผิดคอลัมน์และให้ตัวเลขที่ดู
> สมเหตุสมผลแต่ไม่ถูกต้อง สองแถวนี้จะตอบคำถามนั้นได้อย่างแน่ชัด
>
> **สิ่งที่ขอนี้ไม่มีข้อมูลลูกค้าอยู่เลยแม้แต่รายการเดียว** จึงไม่มีประเด็นด้านความเป็นส่วนตัวที่
> ต้องขออนุมัติก่อนส่ง ซึ่งเป็นความตั้งใจ และเป็นเหตุผลที่ผมแยกคำขอนี้ออกมาจากคำขอข้อมูลชุดใหญ่
>
> สิ่งที่จะปลดล็อกได้: เครื่องมือจะสามารถอ่านข้อมูลจริงหนึ่งชุดและกระทบยอดตัวเลขกับรายงาน
> fact-check ของ Gemini ที่แอปใช้อยู่ได้ จนกว่าจะถึงตอนนั้น ทุกอย่างที่รายงานออกมายังเป็น
> เพียงผลคัดกรองบนข้อมูลสังเคราะห์ ไม่ใช่ข้อสรุปสำหรับตัดสินใจ
>
> ยินดีอธิบายรายละเอียดของเครื่องมือหรือคำขอข้อมูลชุดใหญ่เมื่อไรก็ได้ครับ แต่ตอนนี้สองแถว
> นั้นคือสิ่งเดียวที่ติดอยู่
>
> ขอบคุณครับ
> ต่อตระกูล

---

## If they ask "why not just send the whole file?"

Because we would rather not hold it. `docs/data-contract.md` says so explicitly: the `.xlsm`
is not requested, no audio is requested, no transcripts are requested. The harness reads two
extracts, from a directory outside any git worktree, enforced at runtime rather than by
convention. Asking for less is not politeness here; it is the control.

## If they ask "what do you do after this?"

Ask 2 (row counts and class distribution — numbers, not a file), then Ask 3 and Ask 4 (the
extracts, with `phone_number` sent as an HMAC-SHA256 hash whose key stays with their team).
Those need a short privacy conversation. This one does not, which is exactly why it goes
first and alone.
