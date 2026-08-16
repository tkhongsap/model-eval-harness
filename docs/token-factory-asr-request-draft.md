---
type: draft
created: 2026-08-16
status: ready to send -- not sent
tags: [work/true, project/intelligence-layer, token-factory, asr, blocker]
---

# Token Factory: what to ask the GPU team

**Short answer to "is it resolved / can the document resolve it?" — No, on both counts.**

- The **backend fault is real** and cannot be fixed from our side or from the guide. The
  gateway cannot open a connection to the two backends it routes Gemma and Qwen3-ASR to.
  Only someone inside the datacenter can see *why*.
- The **audio question is a genuine documentation gap**. `openapi.yaml` exposes three paths
  (`/v1/models`, `/v1/responses`, `/v1/chat/completions`) and its chat endpoint states *"Text
  input is supported... Image input is model-dependent"* — **audio is not mentioned anywhere
  in the contract.** So there is no documented way to send audio to an ASR model.

**It is not "the model was never downloaded."** `qwen3-asr-1.7b` is provisioned to our key and
appears in the catalog. And `gemma-4-12b` — one of the two dead models — **serves perfectly on
the other endpoint** (`api.modellismz.app`, 200 in 0.71 s). The weights are fine. The problem
is the path from Token Factory's gateway to the backends it routes those two models to.

## Where the models actually live

All our traffic goes to **one** address — the gateway `10.94.154.102:443`. We never talk to a
model port directly; LiteLLM on the gateway forwards to backend vLLM servers.

| Model | Backend | How we know |
|---|---|---|
| `gemma-4-12b-it` | `10.94.154.104:8000` | the gateway's error names it |
| `qwen3-asr-1.7b` | `10.94.154.104:8002` | the gateway's error names it |
| `qwen3.8-27b-fp8` | **unknown** | it works, so it never errors, so it never reveals its address |

That last row matters: a *working* model tells us nothing about where it is served from. We
only learned `:8000` and `:8002` because those two failed and the failure message leaked the
address. Qwen3.8 could be on the same host on another port, or on an entirely different
machine — we cannot tell, and nothing we can run from outside will tell us.

Separately, `api.modellismz.app` is a **different endpoint entirely**, not part of Token
Factory. It serves only `gemma-4-12b`, and that one works.

---

## Paste this to the GPU team

> **Token Factory: two vLLM backends on `10.94.154.104` are not accepting connections.**
>
> Via `https://token-fac-api.truecorp.co.th/v1` (gateway `10.94.154.102`), on 2026-08-16
> 18:20 +07:
>
> | Model | Result |
> |---|---|
> | `qwen3.8-27b-fp8` | **HTTP 200, 0.17 s** — works, 3/3 |
> | `gemma-4-12b-it` | HTTP 500, 3/3 — gateway cannot reach `10.94.154.104:8000` |
> | `qwen3-asr-1.7b` | HTTP 500, 3/3 — gateway cannot reach `10.94.154.104:8002` |
>
> Gateway's own error text:
> ```
> litellm.InternalServerError: Hosted_vllmException -
> Cannot connect to host 10.94.154.104:8002
> [Connect call failed ('10.94.154.104', 8002)].
> Received Model Group=qwen3-asr-1.7b
> ```
>
> **This is not the gateway, the key, TLS, or our VPN** — `qwen3.8-27b-fp8` answers in 0.17 s
> over the exact same connection and key. It is consistent, not intermittent (3/3 on each
> model, reproduced repeatedly over ~7 hours today).
>
> **The host is alive.** From my workstation on VPN, `10.94.154.104:443` and `:8080`
> **actively refuse** — so the machine is up and routable. Ports `:8000`–`:8003` **time out**
> rather than refusing, which is what a firewall drop looks like, so from outside I cannot
> tell whether vLLM is listening on them. **I am not claiming the processes are down.**
>
> The evidence that matters is the gateway's, not mine: `10.94.154.102` is inside the
> datacenter and it cannot open a connection to `10.94.154.104:8000` or `:8002`.
>
> Could you check, from the gateway side, why those two backends are unreachable? The cause
> could be any of:
> - the vLLM processes are not running;
> - they are running but bound to a different port or interface;
> - a firewall/network rule between `10.94.154.102` and `10.94.154.104` changed;
> - the LiteLLM route config points at a stale address.
>
> We cannot distinguish these from outside — you can.
>
> **Separate question, and the one that actually blocks us — it can be answered while the box
> is still down:**
>
> **How are we supposed to send audio to `qwen3-asr-1.7b`?** The published `openapi.yaml`
> defines only `/v1/models`, `/v1/responses` and `/v1/chat/completions`, and describes chat
> input as text and image only — no audio anywhere. We tried
> `POST /v1/audio/transcriptions` (OpenAI/Whisper multipart) and the gateway *did* route it
> (`Received Model Group=qwen3-asr-1.7b`), so a route exists, but with the backend down we
> can't tell whether the request shape is the supported one.
>
> Specifically:
> - Is `/v1/audio/transcriptions` supported, and will it be added to `openapi.yaml`?
> - Or should we call `/v1/chat/completions` with an `input_audio` content part?
> - Which container / sample rate / max duration? Ours are 8 kHz mono PCM-16 WAV, 3.6–9.5 min.
>
> A one-line answer plus a minimal working example is enough.
>
> Also, minor: our catalog showed `qwen3.6-27b-fp8` on Friday and shows `qwen3.8-27b-fp8`
> today. Was that a deliberate swap? A model ID has to stay fixed for the duration of an
> evaluation run, so we'd like to know when builds change.

---

## ภาษาไทย — สำหรับส่งทีม GPU

> **Token Factory: vLLM สองตัวบน `10.94.154.104` เชื่อมต่อไม่ได้ครับ**
>
> ทดสอบผ่าน `https://token-fac-api.truecorp.co.th/v1` (gateway `10.94.154.102`)
> วันที่ 2026-08-16 เวลา 18:20 น.
>
> | โมเดล | ผลลัพธ์ |
> |---|---|
> | `qwen3.8-27b-fp8` | **HTTP 200, 0.17 วิ** — ใช้งานได้ 3/3 |
> | `gemma-4-12b-it` | HTTP 500, 3/3 — gateway ต่อ `10.94.154.104:8000` ไม่ได้ |
> | `qwen3-asr-1.7b` | HTTP 500, 3/3 — gateway ต่อ `10.94.154.104:8002` ไม่ได้ |
>
> ข้อความ error จาก gateway เอง:
> ```
> Cannot connect to host 10.94.154.104:8002
> [Connect call failed ('10.94.154.104', 8002)].
> Received Model Group=qwen3-asr-1.7b
> ```
>
> **ไม่ใช่ปัญหาที่ gateway, key, TLS หรือ VPN ของเรา** เพราะ `qwen3.8-27b-fp8`
> ตอบกลับใน 0.17 วินาที ผ่าน connection และ key เดียวกัน และเกิดขึ้นสม่ำเสมอ
> (3/3 ทุกโมเดล ทดสอบซ้ำตลอด ~7 ชั่วโมงวันนี้)
>
> **ตัวเครื่องยังทำงานอยู่** จากเครื่องผมที่ต่อ VPN: `10.94.154.104:443` และ `:8080`
> ตอบ refuse แปลว่าเครื่องยังอยู่และ route ถึง ส่วน `:8000`–`:8003` timeout
> ซึ่งเป็นลักษณะของ firewall drop ผมจึงมองไม่เห็นว่า vLLM รันอยู่หรือไม่
> **ผมไม่ได้สรุปว่า process ตายนะครับ**
>
> หลักฐานที่สำคัญคือฝั่ง gateway ไม่ใช่ฝั่งผม: `10.94.154.102` อยู่ใน datacenter
> และเชื่อมต่อไปที่ `10.94.154.104:8000` และ `:8002` ไม่ได้
>
> รบกวนช่วยตรวจสอบจากฝั่ง gateway ว่าทำไมสอง backend นี้ถึงติดต่อไม่ได้ครับ
> สาเหตุเป็นไปได้หลายอย่าง:
> - vLLM process ไม่ได้รันอยู่
> - รันอยู่แต่ bind คนละ port หรือคนละ interface
> - มีการเปลี่ยน firewall/network rule ระหว่าง `10.94.154.102` กับ `10.94.154.104`
> - LiteLLM route config ชี้ไปที่ address เก่า
>
> จากข้างนอกเราแยกไม่ออกครับ แต่ทีมน่าจะตรวจสอบได้
>
> **อีกคำถามหนึ่ง ซึ่งเป็นตัวที่บล็อกงานเราจริง ๆ และตอบได้เลยโดยไม่ต้องรอเครื่องกลับมา:**
>
> **เราต้องส่งไฟล์เสียงไปที่ `qwen3-asr-1.7b` อย่างไรครับ?** ใน `openapi.yaml`
> มีแค่ `/v1/models`, `/v1/responses`, `/v1/chat/completions` และระบุว่า chat
> รับ text กับ image เท่านั้น ไม่มี audio เลย เราลองเรียก
> `POST /v1/audio/transcriptions` แล้ว gateway route ให้จริง
> (`Received Model Group=qwen3-asr-1.7b`) แสดงว่ามี route อยู่
> แต่เมื่อ backend ล่มจึงยืนยันไม่ได้ว่ารูปแบบ request ถูกต้องไหม
>
> - `/v1/audio/transcriptions` รองรับไหม และจะเพิ่มใน `openapi.yaml` ไหมครับ
> - หรือควรเรียก `/v1/chat/completions` โดยส่งเสียงเป็น `input_audio` content part
> - ควรใช้ไฟล์แบบไหน sample rate เท่าไร ความยาวสูงสุดเท่าไร
>   (ของเราเป็น WAV 8 kHz mono PCM-16 ยาว 3.6–9.5 นาที)
>
> ขอคำตอบสั้น ๆ พร้อมตัวอย่างที่ใช้งานได้ก็พอครับ
>
> เรื่องเล็กน้อย: catalog ของเราวันศุกร์เป็น `qwen3.6-27b-fp8` วันนี้เป็น
> `qwen3.8-27b-fp8` เป็นการเปลี่ยนที่ตั้งใจไหมครับ? เนื่องจาก model ID
> ต้องคงที่ตลอดการประเมินหนึ่งรอบ หากมีการเปลี่ยนอยากรบกวนแจ้งด้วยครับ

---

## The evidence, in full

Two files, both safe to forward — the API key appears in neither:

- `docs/token-factory-outage-2026-08-16.txt` — the full error log: catalog, one generation
  call per model with untruncated response bodies, and the audio-path attempt. Regenerate at
  any time with `bash scripts/token_factory_error_log.sh`, which exits non-zero while any
  model is failing, so it can be polled until the backends return.
- `docs/token-factory-diagnostic-2026-08-16.txt` — adds the raw TCP reachability check that
  the error log does not carry.

**What each fact rules out:**

| Observation | Rules out |
|---|---|
| `qwen3.8-27b-fp8` returns 200 in 0.17 s, 3/3 | gateway down, bad key, TLS problem, VPN problem, rate limiting |
| Failure is 3/3 on both models, over ~7 hours | intermittent glitch, transient load |
| Error text names `10.94.154.104:8000` / `:8002` and comes **from the gateway** | our client, our request shape, our network |
| `10.94.154.104:443` and `:8080` **refuse** while `:8000`–`:8003` **time out** | the host being powered off or off-network — it is up and routable. Does **not** establish whether vLLM is listening: the timeouts are consistent with a firewall drop, so those ports are simply invisible from outside |
| `gemma-4-12b` serves fine on `api.modellismz.app` (200, 0.71 s) | missing or corrupt weights; a model-level problem |

**Conclusion:** the gateway cannot open a connection to the two backends it routes Gemma and
Qwen3-ASR to. Everything else in the path is healthy. The *cause* — process down, wrong bind
address, firewall change, or stale route config — cannot be determined from outside the
datacenter, and the message above asks the team rather than asserting one.

## Formal support channel, if the GPU team is not the right owner

`Token_Factory_API_Guide.md` says keys are provisioned by, and support requests go to,
**Thanawat.Kaewboworn@truecorp.co.th**, with a required checklist: BU and use-case name, a
timestamp with timezone, method and path, HTTP status, model identifier, whether the failure
is consistent or intermittent, sanitized error body, client latency, and **key alias only —
never the key value.** Everything on that list is in the message above except **BU/use-case
name and key alias**, which I do not have; fill those in before sending to him.

## What was and was not sent to the endpoint

Per the guide's data policy: everything used to reproduce this was synthetic — a
`"Reply with: READY"` prompt and a 0.5-second silent WAV. **No evaluation audio, no
transcripts, no customer data.** The IP addresses quoted come from the gateway's own error
responses.

## Why the audio question is the one to push

The backend will presumably come back. The invocation-shape question will not resolve itself,
and it is a real gap: a model has been provisioned to our key with no documented way to send
it the one input it exists to consume.

Everything on our side is ready — the audio set, ground truth and scorer are committed, and
the Gemini arm is merged and scored. The internal arm is a ~20-file run that produces the
actual Gemini-vs-our-GPU comparison. The only unknown is the request shape.
