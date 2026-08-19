# Development Log

## 🔴 Active Task

**Latest (2026-08-20, overnight): Typhoon fixes the transcription stage and does NOT change
the business outcome — and yesterday's headline needs two corrections.**
Full write-up: `docs/overnight-2026-08-20.md`. Report:
https://claude.ai/code/artifact/18def122-f7a7-436a-9d46-531b9056f3eb
Run `out/runs/20260820-e23-with-typhoon`, five arms, 2,046 records. Spend $3.74.

**1. `typhoon-whisper-large-v3` is a large, clean win on transcription.** Same 138-call
corpus, same gateway:

| | Qwen3-ASR 1.7B | Typhoon large-v3 |
|---|---:|---:|
| CER (normalised) | 0.1120 | **0.0438** |
| WER (normalised) | 0.1691 | **0.0774** |
| calls transcribed | 136/138 | **138/138** |
| catastrophic runaways | **16 (11.8%)** | **0** |

Qwen's 0.1120 already EXCLUDES its 16 runaways, so the true gap is wider than 2.6x. Qwen never
produced ASR-082 or ASR-089 after 8 attempts each; Typhoon transcribed both.

**2. And it buys nothing end-to-end.** `typhoon-pipeline` vs `qwen-pipeline` is
INDISTINGUISHABLE on all three dimensions — net +1 on `call_result` over 13 discordant calls,
+5 reason, +1 product. Halving CER and deleting an 11.8% failure rate moved the business answer
on ONE call in 138. The reason: `ceiling` (perfect transcript) scores 0.645 and typhoon scores
0.597, so the pipeline is already within 0.05 of its own ceiling. **The binding constraint is
the labelling step, not the audio.** Adopt Typhoon for reliability, not to win the migration
argument, and say so when proposing it.

| arm | call_result F1 | accuracy | reason | product |
|---|---:|---:|---:|---:|
| format-control | 0.663 | 85/136 | 0.239 | 0.804 |
| ceiling | 0.645 | 84/136 | 0.228 | 0.808 |
| **typhoon-pipeline** | **0.597** | 76/138 | 0.230 | **0.791** |
| qwen-pipeline | 0.588 | 74/136 | 0.207 | 0.762 |
| gemini-audio | 0.495 | 60/136 | 0.267 | 0.727 |

**3. TWO CORRECTIONS to the 2026-08-19 entry below.** Neither reverses it; both change how much
weight it carries.

- *It was scored the wrong way.* The preregistration says every headline figure is computed on
  **replicate 1 alone** — "choosing the replicate after seeing three of them is choosing the
  answer" — and `experiment23_score.py` was collapsing all three by modal vote.
  `--replicate-policy` now defaults to `first` and is stamped into the output JSON. Both
  policies agree, so the conclusion survives; it is now verified rather than assumed.
- *"AHEAD" is much weaker than it reads.* Both AHEAD verdicts clear their band by EXACTLY ZERO.
  20,000-resample cluster bootstrap over calls: **qwen vs gemini 52.6%**, typhoon vs gemini
  61.0%, and **25 single calls could each flip the qwen result alone**. The INDISTINGUISHABLE
  findings are the robust ones (94–98%). **Do not present this as "the internal pipeline
  wins."** Production's audio arm is clearly the weakest on `call_result`; the margin is too
  thin to bank.

**4. Tar's token issue is solved, and it is one lever.** Retention control, already on disk:
245 median completion tokens, 0 reasoning. sentiment_qa measured on production's real prompt
and the real 94k-char `user_config.xlsx`, 24 calls/arm: baseline 11,961 output of which
**8,774 (73%) is thinking**; `reasoning` off gives **3,440 and still returns all 118 keys** —
a 71% cut. `reasoning: low` does nothing, retention's decoding does nothing, and **deleting
the prompt's "think 1…5" block does nothing**. The budget is the lever, at three call sites
(highest-volume is the daily batch `qa_pipeline_tasks.yml:76`, not fact-check).
NOT claimed: that capping thinking preserves accuracy — untestable here, no labelled
sentiment_qa batch exists. `docs/sentiment-qa-token-ask.md` (EN+TH) asks production for the one
Vertex field that settles the budget question.

**Environment note for the next session:** the ASR tooling needs `.venv-asr`, not `.venv`. The
root pins numpy 2.3.4; `requirements-asr.txt` pins 2.5.2 and says the two "must never merge".
It now exists and the full asr-eval suite runs — 152 passed, 2 xfailed, including `test_dsp.py`
and `test_tooling.py`, which had never been collectable here.

**NEXT ACTION, in order:**

1. **Fix the ceiling, not the microphone.** A perfect transcript scores 0.645 because no
   labeller ever emits `unknown` or `undefined` — 21 of 136 calls are unwinnable for every arm,
   production included. That is where the next experiment belongs, and no upstream work can
   move the headline until it is done.
2. **Send `docs/sentiment-qa-token-ask.md`** — one field converts a directional finding into a
   settled one.
3. **Send `docs/ask1-email-draft.md`** — still the only thing that retires `RECONCILED: NO`.
4. Adopt Typhoon for the ASR stage on reliability grounds.

Open PR: #35.

---


**Latest (2026-08-19, E23): the internal pipeline WINS the business decision, by one call.**
Four arms, 136 scored calls x 3 replicates = 1,632 label calls, run
`out/runs/20260819-125109Z-e21`. On the primary dimension `call_result`, `qwen-pipeline` is
**AHEAD** of `gemini-audio`: d=37 discordant, net **+15**, band **±15** at alpha 1/64. That is
the narrowest possible pass — one call the other way and it does not clear.

| arm | call_result F1 | accuracy | product F1 | parse failures |
|---|---:|---:|---:|---:|
| `format-control` (ASR-shaped text, zero mishearing) | 0.663 | 85/136 | 0.804 | 0 |
| `ceiling` (perfect transcript) | 0.638 | 83/136 | 0.808 | 0 |
| **`qwen-pipeline`** (Qwen3-ASR -> Qwen3.8-27B) | **0.582** | 74/136 | **0.759** | 0 |
| `gemini-audio` (production today) | 0.490 | 59/136 | 0.721 | **20/408** |

**The result answers the question that was actually asked.** Qwen3-ASR transcribes worse —
2.6x the CER, ~1 call in 8 lost to a runaway — and the pipeline built on it still makes better
business decisions. The stronger reasoner absorbs the transcription damage. CER was
deliberately not re-tested; it cannot carry this decision.

**Why it is believable rather than merely reported.** `format-control` scored *above*
`ceiling`, so formatting costs nothing here and Qwen's shortfall against ceiling is genuine
mishearing — the control that caught two false headlines before. Gemini's 20 parse failures
did NOT decide it: no item lost all three replicates, so every call stayed scoreable. Its
deficit is labelling. Concretely it books **19 of 52 churning customers as saved** (63% churn
recall) against Qwen's 78% — for a Retention pipeline, the expensive direction.

**What is NOT claimed.** `reason` (net -3) and `product` (+5) are both INDISTINGUISHABLE, not
wins. Every score is an **upper bound**: this corpus states its labels in ~100% of calls, and
`leak_probe.py` reports lift 1.00 on the outcome channel rather than hiding it. No arm exceeds
~0.66 because neither labeller ever emits `unknown` or `undefined`, leaving 21 of 136 calls
unwinnable for everyone. Synthetic TTS audio, two voices, generator-authored labels.
**`RECONCILED: NO` stands** — `docs/ask1-email-draft.md` is still the only thing that retires it.

**This reverses the earlier split recommendation.** "Keep ASR external, move labelling
internal" came from stage-by-stage measurement. End-to-end it does not hold.

**NEXT ACTION — two open threads, in order:**

1. **Tar's sentiment_qa token cost** (30–40k output tokens per call). Cause identified, not yet
   fixed: `thinkingBudget: -1` (unlimited) and `temperature: 1`, where retention uses `0` and
   `0.0`; the prompt also instructs "When evaluating, think: 1…5". 118 output keys is ~4–7k
   tokens, so ~80% is thinking, not fields. Three config changes proposed, none tested.
2. **`typhoon-whisper-large-v3`** appeared on the gateway on 2026-08-19 — Thai-specialised ASR,
   directly aimed at the one stage E23 shows to be weakest. Untested.

Deferred with the user's agreement: the **ASR-repair arm** (approved, no repair prompt exists
in `prompts.py` yet) and the **git-lfs decision** for the 3.34 GB pack under `asr-eval-v2/`,
which stays gitignored meanwhile.

---


**Latest (2026-08-16, tooling): Harbor was evaluated and DECLINED for now, with the trigger
list written down so it is a decision and not a recurring debate.**
[`harbor-framework/harbor`](https://github.com/harbor-framework/harbor) — Apache 2.0, Python,
v0.16.1, 4.3k stars, 216 open issues. Built by the Laude Institute (the Terminal-Bench team),
and the official harness for Terminal-Bench 2.0. Raised because LangChain's Harrison Chase is
promoting it: *"a great framework for running evals for long running, stateful agents … its
becoming industry standard."* LangChain integrated it across LangSmith, Sandboxes and Deep
Agents, and LangSmith is one of its execution backends.

**How it works**, recorded here so nobody re-researches it: a task is a directory —
`instruction.md`, `task.toml` (timeouts, CPU/memory/GPU, network policy), `environment/`
(Dockerfile or compose), `tests/test.sh` (the verifier), optional `solution/`. Per trial Harbor
builds the container, installs an agent inside it, hands it the instruction, lets it run
autonomously to `[agent].timeout_sec`, then runs the verifier — in the same container by
default, or a separate grading container that receives only declared artifacts. The verifier
writes `reward.txt` (one number) or `reward.json` (several floats) to `/logs/verifier/`. Agents
ship built in (Terminus-2, Claude Code, Codex CLI, OpenHands, Gemini CLI, …) or you implement
`BaseAgent` — `name()`, `version()`, `setup()`, `run()` — and pass
`--agent path.to.agent:SomeAgent`. Backends: local Docker, Daytona, Modal, LangSmith, Blaxel,
Novita, Kubernetes. `harbor datasets list` carries TB2.0, SWE-Bench, Aider Polyglot.

**Why not now.** Harbor is a *runner*; it would replace `runner.py` and leave `compare.py`
untouched, and `compare.py` is the asset. Its `reward.json` is **per trial**, while every
verdict here is **cross-trial** — paired across 138 items x 3 replicates, exact bands,
alpha 1/64, `N_flip`. It has no notion of a paired test, UNDERPOWERED, coverage refusal, or a
differential against production's real scorer; it does not need one, because a container either
ends in the right state or it does not. It also addresses none of the four things actually
blocking this repository: gateway-bound throughput, no production-shaped labelled batch,
statistical power, and the incumbent's determinism. Adopting it for the retention track is
precisely what the 2026-08-08 entry below warns against — *more infrastructure built against
the same synthetic comparison, which has now been decided twice.*

**One first impression was wrong and is corrected here.** The integration was assumed to be
awkward. It is not: a `BaseAgent` subclass that POSTs to Token Factory and populates
`AgentContext` is about thirty lines. The case against adopting it is *not* effort — it is that
the effort buys no measurement.

**Adopt Harbor when any ONE of these becomes true:**

1. **RL or SFT on the internal GPU is wanted.** Rollout interfaces are its reason for existing
   and there is no comparable alternative. This is the strongest trigger.
2. **An eval target becomes agentic or multi-step** — e.g. if ASR -> text -> QA becomes a
   tool-using pipeline rather than two independent calls.
3. **Soak or stress needs more concurrency than one client can drive.** This is the one place
   Harbor would *delete* code rather than add a dependency: `scripts/soak_test.py`, the
   shakedown and the GPU probes are all bespoke versions of its core competence. Note it does
   NOT fix the real limit found on 2026-08-15 — 4 of 13 required metrics, GPU utilisation among
   them, are unobtainable from any client and need host access.
4. **Harbor reaches 1.0.** At v0.16.1 with 216 open issues it does not belong in the path of
   paid runs in a repository whose `requirements.txt` header exists because a minor version
   bump silently changed what the scorer computed.

If a first trial is wanted before any trigger fires, the honest target is the **ASR track** —
new, nothing depends on it yet, and containerised execution would eliminate the Windows
file-lock class recorded under Known Bugs outright. Not the retention track.

**Latest (2026-08-16, ASR track): the audio eval set shipped, and the first arm has now been
scored on it.** `asr-eval/` (PR #28, merged) — 20 synthetic Thai call-centre recordings, 123.6 min,
3.6-9.5 min each, 8 kHz mono PCM16, with exact ground truth, entity annotations, per-turn
timelines, and validation / plotting / scoring tooling. It measures the component
`.env.example:27-29` names as the whole migration problem: production sends audio, the
candidate cannot receive it, so a separate ASR step and a transcript artifact have to exist
that do not exist today.

Every constant in `asr-eval/scripts/asr_common.py` cites the production file and line that
fixes it. The load-bearing ones: `.wav` is the only extension past the upload filter
(`upload_voice_task.py:352`); metadata is parsed positionally out of the filename in three
places that must agree (`get_batch_result_task.py:302-319`); a `call_direction` that is not
exactly `IN`/`OUT` **raises** rather than degrading (`prep_payload_task.py:335`); and the agent
name occupies **two** positional fields, so an underscore in a name shifts every later index
silently.

**Ground truth is authored, then synthesised** — the reverse of the usual route. The text is
written first and the audio generated from it, so the reference is exact by construction and
reference error is removed as a term in every WER. The cost is stated in the README in these
words: the audio is synthetic, so **a WER measured here is not a production WER estimate**,
only a controlled arm-against-arm comparison. Ten mechanism families x 2 calls, seven of which
are the acoustic *cause* of an artifact class `ASR-EXPECTATION.md` already argued as text.
Phones reserve `0810000301`-`0810000320`, clear of all three spent sub-ranges.

**An adversarial verification pass found five real defects, and one lesson about running it.**
The critical one: the entity scorer compared a value as a *substring* of every digit in the
transcript concatenated together, so `45` + `99` spliced to match amount `599` — a junk
transcript mentioning none of the call scored **11.4% entity accuracy**, now **0.0%**. Also:
`date` could never value-match at all; a production citation pointed at the wrong lines;
`validation.json` embedded an absolute home path 40 times, the same leak class as 2026-08-09;
and two DSP constants named effects the chain did not deliver — hum synthesised at 50/150 Hz
was **entirely removed** by the 300-3400 Hz band-limit, and an L1-normalised reverb IR left
`far_field` dry. **The fix for the reverb then overshot in the opposite direction**, burying
the direct path and collapsing the syllable envelope from 5.18 Hz to 1.07 Hz; only the
speech-rate check caught it. The process lesson: the verification ran while the files were
being edited, so three verifiers reported "not reproducible" on defects already patched and
three noted they had reproduced theirs "before it was patched mid-session." **An adversarial
pass needs a frozen tree, or its verdicts are unusable.**

Suite on this workstation: **861 passed / 38 skipped** standalone, **872 / 27** with
`TRUE_SOURCE_ROOT` set — measured in a venv built to `requirements.txt` exactly, because the
system interpreter carries pandas 3.0.0, which that file's header warns collapses
`call_result` accuracy from 0.75 to 0.25. Every skip is environmental and self-describing
(production source not at the default path; gitignored `out/` run directories). `asr-eval`:
112 tests, 608 validation checks, 0 failures. Still `RECONCILED: NO`.

**First arm scored (PR #29): Gemini 2.5 Flash, transcription-only, via OpenRouter.**
Deliberately *not* production's one-call audio->JSON shape — Gemini is asked to do nothing but
transcribe, so its output lands in the same form `transcribe.py` produces and both can go
through the identical scorer. Mixing in the QA task would make a CER difference impossible to
attribute to transcription versus labelling. This is also the first time anything in this
repository has sent audio anywhere; `src/evalgen/prompts.py` sends Thai *text*.

Pooled over the 20 calls: **CER 0.0445**, **WER 0.1176** (newmm tokens), ~~**entity 320/465 =
68.8%**~~.

> **Corrected 2026-08-18.** The entity figure here is wrong and was never updated. It is a
> pre-fix number from an even earlier scorer than the one commit `d568505` corrected, which
> recorded 314/465. **The current figure for this arm is 450/465 = 96.8%.** The scorer's
> surface match was an exact substring test that included spaces, so an arm writing the
> identical words with different phrase spacing scored the entity *lost*; Gemini, which spells
> numbers as Thai words, was penalised hardest. See `docs/eval-round-report.md`.
>
> **The paragraph below is therefore also wrong** and is kept only so the correction has
> something to point at. Entities are not "31% gone" for this arm — they are 3.2% gone. The
> reasoning it illustrates (that a low CER can hide field-level loss) remains sound; this arm
> is simply not an example of it.

**The headline is the gap between those numbers, and it is exactly what the entity metric was
built to expose.** A 4.45% CER reads as an excellent transcript. Underneath it, **31% of the
entities the QA pipeline would write into a field are gone**:

| type | recovered | | type | recovered |
|---|---:|---|---|---:|
| `id` | 64/64 — 100% | | `date` | 22/88 — **25.0%** |
| `phone` | 87/88 — 98.9% | | `package` | 6/15 — **40.0%** |
| `amount` | 101/118 — 85.6% | | `months` | 36/83 — **43.4%** |
| | | | `speed` | 4/9 — **44.4%** |

Digit strings read aloud survive almost perfectly; dates, durations, speeds and product names
do not. A WER-only report would have called this arm a success.

The families discriminate on CER as designed — `disfluency` 0.1211 and `far_field_low_gain`
0.1133 are the hardest, `clean_baseline` 0.0063 the easiest, a 19x spread.

**Two honest caveats on this run, both about the instrument rather than the model:**

- **The normalisation was completely inert**: raw and normalised CER agree to five decimal
  places (+0.00000). None of the three lossless classes ever fired, because neither the
  reference nor Gemini's output contains Thai numerals, doubled SARA E or zero-width
  characters. The raw/norm split cost nothing here and also proved nothing; it stays because
  a different arm may well need it.
- **Entity accuracy does not track acoustic difficulty, and the set cannot currently separate
  the two.** `telephony_noise` has the *best* entity recovery in the set (97.2%) while
  `clean_baseline` has 60.6%. That is not a finding about noise — it is that entity types are
  not balanced across families, so a family's entity score mostly reflects which entity types
  its calls happen to contain. **Do not read the per-family entity column as a difficulty
  ranking.** Balancing entity types across families is the fix, and it is a change to
  `compose_dialogues.py`, not to the scorer.

**Next action: run the internal ASR arm** — `transcribe.py` against `qwen3-asr-1.7b`, then
`score_asr.py`. One arm is a baseline, not a comparison, and this repository decides nothing
off a single arm.

**It is blocked on a DIFFERENT backend from the Gemma arm, which is easy to miss.** The probe
in `docs/token-factory-outage-2026-08-16.txt` (2026-08-16 11:35Z) separates them:

| model | status | backend |
|---|---|---|
| `qwen3.8-27b-fp8` | **HTTP 200**, 0.28 s — control, gateway is healthy | — |
| `gemma-4-12b-it` | HTTP 500 | `10.94.154.104:`**`8000`** unreachable |
| `qwen3-asr-1.7b` | HTTP 500 | `10.94.154.104:`**`8002`** unreachable |

Two separate vLLM processes are down on the same host, on different ports. **Restoring :8000
unblocks the Gemma rerun and does nothing for ASR**; the entry below asking only for :8000 is
therefore incomplete as an ask. The gateway itself is fine — it is reached at
`10.94.154.102` and the control model answers in under a third of a second.

One thing the probe settles for free: `POST /v1/audio/transcriptions` **exists** on the
gateway and fails with a connection error to the backend rather than a 404, so the endpoint
shape `asr-eval/scripts/transcribe.py` was written against is the right one. What to ask the
platform team for is drafted in `docs/token-factory-asr-request-draft.md`.
**Bookkeeping:** Experiment 18 has no section in `EXPERIMENTS.md`; it lives only inside the
generated comparison report, and every other experiment from 1-17 and 20 has one.

**Latest (2026-08-16, Experiment 20): the challenge pack ran for the first time, and it cannot
separate the models.** `retention_challenge_v1` (50 items, never previously evaluated) against
Gemini 2.5 Flash and Qwen3.8 27B. Raw F1 reads Gemini 0.951 / 0.831 / 0.976 and Qwen3.8
0.930 / 0.833 / 0.970 — but the two models **disagreed on exactly one call out of 50** on call
outcome and on **none at all** on product, so both come back UNDERPOWERED. Only `reason` is
judgeable (12 discordant, net +2, band ±10) and it is INDISTINGUISHABLE. **Reading the raw F1
as a ranking is the error the paired test exists to prevent.** Both models fail 19 of 50 on
reason. Nothing here contradicts the v3 result; a second independent pack simply agrees, less
sharply. At 50 items it probes difficulty and cannot discriminate — 200+ would be needed.

**Gemini did not vary at all on this run**: `raw-unstable 0/50`, `N_flip 0`, under the pin that
gave 111/138 on 08-14. Evidence the collapse was an episode, but *not* a clean replication —
different pack, shorter transcripts. **Experiment 19 is still the instrument.**

**Gemma 4 12B never ran**: 150/150 HTTP 500, gateway could not reach its vLLM backend at
`10.94.154.104:8000`. Three defects fixed as a result — the smoke had already failed and the
runner gated on the process exit code (`stability` exits 0 when every call fails); `/v1/models`
listed the model throughout the outage; and a dead arm scores **1.000 on product** because
`to_rows` emits a ground-truth skeleton, so `read_run` now refuses any non-`ok` row.

**A verification workflow caught a fabrication I had shipped.** The report's worked-example
callout was frozen v3 literals — on the 50-item page it read "both models got 128 right and
both got 7 wrong" (128+7+3 = 138) and credited a result to Gemma. A second pass found the
footer citing the wrong JSON and a self-contradiction about prose churn. Every figure in the
F1, token, latency and verdict tables was independently re-derived and agreed; **every defect
was in ungated prose sitting beside correct tables.** The lesson is narrow and reusable: the
generated tables carried assertions, the prose beside them did not.

Reporting is now config-driven — `configs/comparison/<pack>.json`, one file read by both the
report and the case explorer. `docs/experiment20-results.md`,
`docs/reports/model-comparison-challenge.html`, `out/case-explorer-challenge.html`.
Suite: 887 passed / 12 skipped. Still `RECONCILED: NO`.

**Next action: rerun the Gemma arm** once `10.94.154.104:8000` is back (~8 min, no cost, add
the arm to the config and regenerate), then **Experiment 19** for the determinism question.

**Latest (2026-08-14, Experiment 17): the internal GPUs ran E7's pack, and the finding is
about the incumbent.** Gemini 2.5 Flash **stopped being deterministic between 2026-08-10 and
2026-08-14** under a byte-identical workload — same model id, same `Google` pin, same 1,237,746
prompt tokens with the per-item spread identical entry for entry, same decoding, same retries.
Raw-unstable went **0/138 → 111/138**, scored-unstable **0 → 29**, `N_flip` **0 → 34**. The
scorer was ruled out first: re-scoring the *2026-08-10 outputs* with *today's* code returns
0.955 / 0.823 / 0.960 and `N_flip = 0` exactly, so the `cefd4ae9…→9b4afc95…` digest move is
numerically inert here. On Token Factory (LiteLLM/vLLM, fp8), **`gemma-4-12b-it` is BEHIND on
all three dimensions** and **`qwen3.6-27b-fp8` is INDISTINGUISHABLE on both powered
dimensions** — ahead on `call_result` F1 (0.962 vs 0.955), the *most* stable arm by scored
instability (8 against Gemini's 29), at or above Gemini on 8 of 9 mechanisms, and the only arm
in the table with a non-FAIL mechanism row (`long_context` FLAKY 11/12). Against it: `product`
is UNDERPOWERED (4 discordant, threshold 6) and therefore **not measured**, and throughput is
**~11× worse** — both GPU arms pin to 0.32–0.34 calls/s regardless of model, so the gateway is
the bottleneck. Zero retries on both GPU arms (414 attempts / 414 calls) against Gemini's 416.

**Next action: re-run the Gemini arm on consecutive days** (~2 min, $0.56 each) to establish
whether 08-14's instability is an episode or permanent. Until that is known, "Qwen is
indistinguishable from Gemini" describes Gemini *as it behaved on 2026-08-14*, not a stable
incumbent — and E15/E16, which both reuse E10's Gemini arm and quote `raw-unstable 0/138` as
the baseline, now rest on a premise with a date on it.

**A lesson worth carrying:** the plan's invariance trip-wire was "stop if Gemini does not
reproduce 0.955 / 0.823 / 0.960." Two of the three reproduced *exactly* while the model's
determinism collapsed, so **it would not have fired**. Only the stability columns caught it. An
invariance control stated in aggregate F1 alone is too coarse.

`docs/experiment17-plan.md`, `docs/experiment17-results.md`,
`experiments/evidence/retention-e17/summary.json`, `scripts/experiment17.py`. Suite: 887
passed / 12 skipped standalone, 898 / 1 differential. Still `RECONCILED: NO`.

**Latest (2026-08-11, Experiments 15-16): two frontier open models now match or beat Gemini
on quality, and neither is deployable on this evidence.** **GLM 5.2 is AHEAD on `reason`**
(+14 of 32 against a +/-14 band, exactly at the boundary) -- the first open model to clear
that band under a matched zero-reasoning regime, though NOT the first ever: Experiment 5A's
two Qwen AHEADs were bought with 2.4-2.6M reasoning tokens. **Kimi K3 is indistinguishable**
on `reason`, the one dimension with power, with a perfect 1.000 call_result precision. The
blockers are operational: GLM completed 408/414 and retried 33 calls on HTTP 429 (below the
99% rule) with the worst latency measured at every percentile, not just the tail (p50 25.8s,
p95 108.9s, max 186s); Kimi costs **22.7x** Gemini, driven partly by a tokenizer needing
**2.59x** the input tokens for the same Thai text. Gemini remains untouched on operations:
zero variance, zero retries, fastest everywhere. **An eight-agent adversarial pass flagged
four claims in the first write-up, three of which were genuinely wrong** -- a false "first
AHEAD" superlative, an overstatement of Kimi's parity, and an understated retry count --
all corrected in place in `EXPERIMENTS.md`, along with a further fix to ratios that had
divided 408-call totals by 414-call ones. The fourth flag, on latency, was itself mistaken
and is withdrawn (see below). It also caught that E15 and E16 ran concurrently through
one account, so the 429 burst and the p95 are contaminated by self-imposed load; a re-run
at concurrency 1 is the discriminating test. Full table:
`docs/frontier-open-model-comparison.md`. Still `RECONCILED: NO`.

**A second review pass (2026-08-11) found that the corrections commit had itself introduced
errors, now repaired.** The big one: correction 3 retracted "GLM has the worst latency" by
citing Qwen p50s from the **reasoning-on** Experiment 5A arms -- the exact confound
correction 1 had just disclosed. Under the matched zero-reasoning regime those arms post
6.95 s and 2.78 s, so **GLM is in fact the slowest at p50, p95 and max**, and the retraction
is withdrawn. The same regime mix put an impossible `p95 9.00 s` under `p50 28.75 s` in the
comparison table, whose Qwen columns were Experiment 7 in every row except latency. Also
fixed: Gemma's AHEAD is the **third**, not the second or fourth; Kimi's `call_result` was
one pair from INDISTINGUISHABLE, not UNDERPOWERED; and the "clustered on four items" scope
covers the 6 terminal failures, while the 33 retried calls span 22 items. Everything was
recomputed from `retention-e7/summary.json` and the raw logs through the harness's own
`_percentile` and `exact_band`.

**Latest (2026-08-11, Experiments 10-14): Gemma 4 12B is indistinguishable from Gemini on
held-out items, and is the best open model this project has tested.** On the 89 locked
holdout items: `reason` INDISTINGUISHABLE (+0 of 24 discordant), `call_result` and
`product` UNDERPOWERED. Against the open field it posts the best `reason` F1 (0.815 vs
0.774 and 0.701) and is far steadier -- 79 of 138 calls vary against 129 and 130, with
only 8 touching a scored label against 31 and 44 -- at a fifth to a third the parameter
count. It is **5-10x slower** (p50 9.62s vs 1.99s) on one small shared box, and its
endpoint reports no cost, so there is no cost case yet. Prompt tuning moved the target
dimension and **generalised** (`reason` AHEAD, +14 of 18 against a +/-10 band, the third
AHEAD recorded here, after Experiment 5A's two Qwen arms) **and cost the other two** (both
BEHIND). The portable finding, now seen twice: **a prompt edit works by changing what the
model is SHOWN; telling it what to do moves nothing.** Full assessment:
`docs/gemma-4-12b-assessment.md`; runs in
`EXPERIMENTS.md` Experiments 10-14. Still `RECONCILED: NO`, still synthetic.

**Latest (2026-08-09, Experiment 9): prompt tuning for Qwen, aimed at the gate it actually
fails.** Qwen3.6 27B is not behind Gemini on any quality dimension (UNDERPOWERED,
INDISTINGUISHABLE, UNDERPOWERED); it fails on **stability alone**, -129/129. A new
zero-call diagnostic (`src/evalgen/stability.py`, report section 4b) measured that
**77.5% of its instability never touches a label the scorer reads** -- it is churn in
`recommendation`, `keyword` and `call_event_detection`, free text the schema requires and
no metric consumes. Phase-two tuning was authorised for the first time and pre-registered
before any prompt was written, with a committed 49/89 tune/holdout split. **Iteration 1
(`v9_16_q1`) obeyed its constraint and moved raw instability by zero** (217 -> 61 chars
mean, 44/49 unstable both) -- a free-text field cannot be made deterministic by
instruction. **Iteration 2 (`v9_16_q2`) derived the field from one already chosen and got
25/49**, but scored instability rose 7 -> 13. **The holdout has NOT been spent**, because
that trade is not yet understood and the holdout is a one-shot resource. The real question
is now a schema-and-gate one for the app owners, not a prompting one. Full record:
`EXPERIMENTS.md` Experiment 9.

**Latest (2026-08-09, Experiment 8): error severity now measured, and the LLM-judged half
of it failed its own evidence bar.** `src/evalgen/severity.py` attaches an error category
to every unit the scorer counts as wrong, so a report can say *how* an arm failed and not
only how often. The deterministic half needs no model, is byte-identical across three
independent runs, and produced four decision-relevant findings — the largest being that
**over-labelling is 69.2% of the incumbent's `reason` errors** (45 of 65), independently
reproducing Experiment 3's hand count of 39 of 57 on a different pack via a different code
path; and that **a quarter of what the scorer calls "wrong" is a product-row alignment
failure, not a labelling error**, at near-identical rates on all three arms. The judged
near/cross layer did **not** produce a measurement: across 8 runs, **178 of 276 responses
that arrived (64.5%) failed a byte-exact evidence gate**, and the CoreWeave endpoint then
degraded (181 transport errors of 457 calls). Goal-contract criterion 5 is recorded as NOT
met. An 11-of-17 "flip rate" from the first attempt is **withdrawn**: an adversarial review
found gate-rejected responses were being counted as votes. Two review passes (14 agents)
found 20 defects in this work, two of them critical data-safety or post-spend-abort bugs;
all 20 are fixed with regression tests, and one hand-computed expectation was corrected in
writing. Full record: `EXPERIMENTS.md` Experiment 8, `docs/severity-plan-2026-08-09.md`.

**Current focus (2026-08-08): the recommendation is written down, and a second,
independently executed experiment reached the same answer.** `docs/migration-decision-
2026-08-07.md` synthesizes Experiments 1-6: every apparent Qwen advantage this project
ever measured was bought with a reasoning budget production does not grant it
(Experiment 4's confound); with that budget removed (Experiment 5B, the
production-shaped regime), both `qwen/qwen3.6-27b` and `qwen/qwen3.6-35b-a3b` **FAIL** a
pre-registered decision rule. Experiment 7 then repeated the comparison on a different
provider pin (Qwen 27B via Chutes, not Morph or CoreWeave) and reached the same
decision by a different route: 414/414 parse-valid on all three arms, but Qwen 27B
failed on stability alone (129/138 calls changed their exact answer across replicates)
and Qwen 35B-A3B failed all three paired quality gates plus stability (130/138
unstable). Two independently run experiments, two different endpoints for the 27B arm,
the same conclusion. See `docs/experiment7-results.md` and
`experiments/evidence/retention-e7/summary.json` for Experiment 7's own evidence.

**This is still a recommendation, not `RECONCILED: YES`** -- no code path in this
repository prints that, and neither document claims it. The active blocker is unchanged:
this repository still lacks an approved production-shaped labelled batch, the two-row
workbook header contract, and an execution on the company GPU. Both experiments are
mock-data screening evidence. The recommended next spend of effort is that
production-shaped work, or the internal-GPU rerun Experiment 7's own handoff calls for
-- not more infrastructure built against the same synthetic comparison, which has now
been decided twice.

- [ ] **Fix the judge prompt's ground-truth asymmetry before any more flag review.**
      `_rule_entries_for` reads the item's own ground-truth-authored `rules` dict, so
      the rule for a *competing* label is never quoted -- the judge sees why the
      reference label might be right and nothing about why the alternative is wrong.
      Traced as the cause of RET-98 (0->3) and RET-129 (2->3) false flags. Quote the
      whole dimension's class list, and match `#2` second-citation keys (97 across the
      packs) while there. `EXPERIMENTS.md` E6 addendum, defect 4.
- [ ] **Add replicates per judgment unit.** A placebo arm inside the 2026-08-09 A/B
      (8 rows, byte-identical requests in both modes) flipped verdict 4 times out of 8
      at temperature 0. Until judge instability is measured per unit, no judge delta is
      separable from resampling noise.
- [ ] ~~Retention domain owners review the 38 Experiment 7 possible-ground-truth-error
      flags in the restricted judge bundle~~ **Blocked on the two items above, and on
      access:** those flags came from pointer-only prompts, and the raw Experiment 7
      run directories are not on this workstation (gitignored `out/`, executed
      elsewhere), so re-derivation has to happen where that data lives. Current
      contested queue if reviewed anyway: RET-100 (possibly a real GT error, not just
      arguable), RET-98 and RET-129 (both independently re-derived as GT *correct*,
      i.e. probably false flags). Commit decisions, never cited transcript text.
- [ ] Run the committed Experiment 7 application contract on the internal GPU runtime,
      preserving testset, prompt, schema, repeats and decision policy.
- [ ] Reconcile one approved real labelled batch against the application's existing
      Gemini fact-check report and record the discrepancy analysis.
- [ ] Implement `load_workbook()` only after the first two real workbook header rows arrive.

**Historical context retained below.** Experiment 5's Morph endpoint qualified at small
scale but returned 54 HTTP 429 failures and one empty response at full scale. Do not
rewrite the prompt, weaken the schema, or post-hoc retry those recorded rows; a new
retry/backoff or capacity regime must be preregistered as a new experiment.

**~~Blocking the Qwen candidate arm (2026-08-05).~~ RESOLVED the same day, before
Experiment 1 closed.** The arm was never blocked. One endpoint was broken, and the entry
below read that as a property of the model and of production's decoding regime. It is
kept in place and corrected, because the wrong inference is the useful part:

- **Stands.** `out/runs/20260804-224050Z-candidate` is **INVALID and must not be
  scored**: it was served by two backends under one model id. Backends are now pinnable
  (`--provider`) and a split is now visible (`prompt_token_spread`). The run stays on
  disk and stays unscored. It is also **unattributable**: its regime-A rows scored 21 of
  31 `ok` against 0 of 20 under the pin probe, but it recorded no `provider` field, so
  the difference cannot be assigned to a backend. That gap is exactly what the pin
  closes.
- **Stands.** Production runs `thinkingBudget: 0`
  (`config/model_setting/retention.yml`), a NON-REASONING regime. Pinned to **Alibaba**,
  20 of 20 items returned `schema_violation` (plus 9 of 9 on the 3x3 stability probe).
  Every one is a bare JSON *number literal* -- e.g. `-1.1000000000000001e-05` followed
  by ~500 digits -- where the schema's root type is `object`. `finish_reason: stop`, no
  truncation.
- **Wrong: that this said anything about the candidate.** It is a broken constrained
  decoder on one endpoint. The identical request returns a well-formed object from
  **Morph**, Chutes and CoreWeave. Run 1.4 re-baselined the candidate on Morph at
  **60/60 ok**, and across Experiment 1 schema violations went 10 -> 0.
- **Wrong: "of nine `qwen/qwen3.6-27b` endpoints, exactly one is non-reasoning".** The
  census missed Morph, which this entry never named and which has served the candidate
  arm ever since. Experiment 2's cost table records **no reasoning tokens on either
  arm**, so Morph runs the candidate in production's own `thinkingBudget: 0` regime --
  the regime this entry said was unobtainable.
- **Wrong: the "open decision, for a human".** It offered a choice between measuring the
  candidate in a reasoning regime production does not run and reporting that
  `qwen/qwen3.6-27b` has no usable non-reasoning endpoint for constrained decoding.
  Neither branch was real and nothing was waiting on a human. Experiments 2 and 3 then
  put 200 and 300 candidate calls through Morph and reached a verdict: **no accuracy
  case for migrating, and a reliability case against it**, every margin within 2 items
  (`EXPERIMENTS.md`, Experiment 3).

- [ ] Receive the **first two header rows** of the Retention ground-truth workbook
      (no data rows). This settles the two-row header layout that
      `adapters/retention.py::load_workbook` currently refuses to guess, and contains
      no customer record. Cheapest unblock available. **The email is now written and
      ready to send: `docs/ask1-email-draft.md`, English and Thai.** It was extracted
      from `docs/data-contract.md` and sent on its own precisely because Asks 2-4 need a
      privacy conversation and this one does not -- bundling them is what kept this
      unsent since 2026-08-04. Nothing in this repository can retire `RECONCILED: NO`
      until it is answered.
- [ ] Receive ground-truth **row counts and class distribution** per app. Blocks the
      sample design. **Partly overtaken (2026-08-05)**: a sample was designed and shipped
      without them, on synthetic data -- `retention_v2` is 100 items / 108 scored rows,
      with `retention_v1.*` frozen so Experiments 1-2 stay reproducible. The need did not
      go away. That pack holds v1's family proportions rather than production's
      (`docs/testset-v2-plan.md`), so it still cannot say whether a class that matters in
      production is under-tested here. That pack also consumed the synthetic phone block
      whole: all 100 numbers `08100000xx` could spell. The block was widened to
      `0810000xxx` on 2026-08-06 (`src/evalgen/testsets.py:135`) so the next item has a
      number to take, and no existing number moved. ~~100 in use, 900 free.~~ **As of
      2026-08-17: 224 in use, 776 free** -- `0810000000`-`0810000099` (v1/v2 and the
      `block_*` fixtures), `0810000101`-`0810000138` (v3 phase two),
      `0810000201`-`0810000250` (`retention_challenge_v1`). The widening has been used
      twice since; the "100 in use" figure was left behind by both.
- [ ] Receive the count of rows whose `phone_number` is null, blank or `0`. That
      number is the size of a blind spot in the current product metric.
- [ ] Implement `load_workbook()` once the header layout is known.

## 🟡 Roadmap

1. **Reconciliation run.** Score one real labelled batch and confirm the numbers match
   the app's existing Gemini fact-check report. Until this passes, every report is
   stamped `RECONCILED: NO` and no number is a migration verdict. This is the single
   most important outstanding item.
2. **MNP adapter.** Cheapest second app: same pure metric functions, and the label
   space differs by exactly one reason class, already declared in `labelspaces.py`.
3. ~~**Candidate arm wiring.**~~ **DONE (2026-08-08).** `src/evalgen/` calls every arm
   through one OpenAI-compatible client boundary and lands them in the same normalized
   record. `runtime.py` supports a reviewable internal-GPU manifest without changing
   scoring code, while `test_boundary.py` asserts `evalharness` never imports `evalgen`.
   **Not done:** an actual company-GPU execution. Follow `docs/TEAM_GPU_RUNBOOK.md`.
4. **Sentiment QA and Telesales adapters.** Hardest, and lowest information: their
   scorers hard-set `FN = TN = 0`, so recall is structurally 100% and three of four
   configured thresholds cannot fail.
5. **RTR.** Deferred: its scorer aligns ground truth and predictions **positionally**
   after independent sorts, so a single missing row silently misaligns everything after it.

## 🐛 Known Bugs

**~~Intermittent, Windows-only, three occurrences and never reproduced.~~ DIAGNOSED and
mitigated 2026-08-11.** Three tests in `tests/test_cli.py` had each failed once in a full
local run and then passed every subsequent time, never in isolation and never on CI:

- (unnamed; the summary line was captured but not the test id)
- `test_portable_run_bundles_compare_after_the_original_directories_move`
- `test_private_resume_uses_snapshots_and_ignores_the_unused_default_output`

**The fourth occurrence was caught with a traceback -- the first one ever captured.**
Reproduced on run 14 of a 16-run hunt:

```
candidate.rename(env / "original-candidate-moved")
PermissionError: [WinError 5] Access is denied:
  ...runs/20260811-062144Z-candidate -> .../original-candidate-moved
```

It is the **second of two renames**, on a directory `shutil.copytree` finished reading a
line earlier. Windows fails a directory rename with `ERROR_ACCESS_DENIED` while any file
beneath it is open by any process, and a real-time scanner opens files that were just
read.

**Two earlier explanations were tested and ruled out**, so neither should be re-proposed:

1. *`artifacts._fsync_directory` holding a directory handle* -- it cannot. `os.open` on a
   directory raises `PermissionError` on Windows, so the function returns early and opens
   nothing. Measured.
2. *A generic copytree-then-rename race* -- a bare loop over a run-shaped tree survived
   **600 iterations** with zero failures. The race needs the load of a full suite run.

**An in-process leak is ruled out as far as inspection can**: every `os.open` in
`evalgen.artifacts` closes in a `finally`, `RunJournal` opens and closes per append, and
`runner`'s pool is shut down with `wait=True`. The remaining inference -- an external
holder, almost certainly the real-time scanner -- is strongly supported but was never
caught red-handed; the holder was not identified by name.

**Mitigation:** `tests/test_cli.py::move_run_dir`, used at all three sites. It retries the
rename for up to five seconds and then re-raises. This retries the test's own *setup*, not
an assertion, and **it cannot hide a real leak**: a handle held by this process is never
released while the test runs, so the budget expires and the original error propagates with
its traceback. Both directions were proved on Windows -- a lock released after 400 ms is
absorbed (rename succeeded at 391 ms); a lock never released still raises (at 515 ms
against a 500 ms budget).

Frequency before the fix was roughly **1 in 40 full runs** (one failure across ~40 runs on
2026-08-11). A recurrence *after* this change means the budget expired, which would be new
information: it would point at an in-process holder rather than a scanner.

**Status 2026-08-12: no confirmed recurrence of the RENAME case -- but the family was
bigger than this entry said, and the rest of it was in harness code.**

Twelve consecutive differential runs immediately after the mitigation were green. A later
drift audit observed one failure in eight runs and did not capture the test id, so it was
recorded as unattributable rather than counted as a fifth occurrence.

Hunting that unattributable failure with every failure saved rather than grep-filtered
caught the real one, and it is **not** the tests' own directory manipulation:

```
src/evalgen/artifacts.py:120, in atomic_write_bytes
    os.replace(tmp, target)
PermissionError: [WinError 5] Access is denied:
  ...\.run.state.json.vl3ce_xk.tmp -> ...\run.state.json
```

Once in ten full suite runs, in `atomic_write_bytes` -- **`src/`, not `tests/`**. POSIX
`rename(2)` over an open file always succeeds; Windows refuses while any process holds the
destination, and a real-time scanner opens files it has just seen written, which is
exactly what the preceding `fsync` guarantees it noticed.

**This could lose paid work.** `run.state.json` is the crash-safe-resume record and
`atomic_write_text` writes it after every checkpoint, so an unhandled failure there aborts
a run that has already been paid for -- the precise loss the state file exists to prevent.
The same call writes `run.json` and the journal header. It was invisible for as long as it
was because it is rare, it looks like a test flake when it lands in a test, and nobody had
saved a traceback.

**Fixed**, not merely recorded: `artifacts._replace` retries `os.replace` for five seconds
and then re-raises. Same discipline as `move_run_dir` -- a handle held by *this* process is
never released while the write is blocked, so the budget expires and the original error
propagates. Both directions are proved on Windows in `tests/test_artifacts.py`: a holder
released after 400 ms is absorbed and the write lands; a holder that never releases still
raises inside a shortened budget, and neither path leaves a `.tmp` behind. Moves no
manifest sha -- `artifacts.py` is in neither `generation_contract_sha` nor
`scoring_code_sha`.

**Not fixed, and deliberately so:** `append_jsonl` and `RunJournal.append` open the same
directory with `O_APPEND` and could in principle fail the same way. There is no measurement
of that happening, and a retry added on suspicion would be a guess dressed as a control.
Recorded so the next occurrence is recognised rather than investigated from scratch.

The lesson that generalises: **save every failing run in full.** Both times this family hid
from diagnosis, it was because output was filtered to what someone expected to see.


**Four coverage/configuration gaps, recorded 2026-08-08 -- three closed 2026-08-12, one
open.** These did not invalidate Experiment 7's recorded output; they were the
harness-hardening owed before a production-data run.

- [x] **CLOSED 2026-08-12.** `cmd_qualify` (spends real API calls, decides
      QUALIFIED/INCOMPATIBLE) had zero test coverage anywhere in the suite. Now four tests
      in `tests/test_enterprise_experiments.py`: its three refusals (locked plan, unknown
      arm, unregistered provider), each driven with a client factory that **raises**, so
      the assertion is that it refused *before it could spend*; plus the paid path with
      deterministic completions, asserting six logical calls and a `QUALIFIED` artifact
      whose `qualification_sha` recomputes. All three refusals were confirmed to fail with
      their gate removed.
- [x] **CLOSED 2026-08-12.** `cmd_experiment_run`'s three safety gates -- a
      `--confirm-plan-sha` mismatch, an `UNAVAILABLE` arm, an out-of-list
      `--concurrency-level` -- were each only ever exercised with a value that passes, and
      the string `UNAVAILABLE` appeared nowhere in `tests/`. Each now has a
      failing-value test, all three confirmed to fail with their gate removed.
      *Found while writing them:* flipping `availability` to `UNAVAILABLE` alone is
      rejected by `validate_plan` first (`experiments.py:417-436` requires unavailability
      evidence for every candidate provider and refuses an arm claiming UNAVAILABLE while
      an artifact says QUALIFIED), so the test builds a *legitimately* unavailable arm.
      The plan validator is a stronger gate here than the run gate, which was not obvious.
- [x] **CLOSED 2026-08-12, and the bullet was wrong to pair them.**
      `_refuse_incomparable`'s era-mixing/mismatch checks were already covered when this
      was written (`tests/test_cli.py:1845-1888` covers the `workload_sha` mismatch, the
      `--prompts-may-differ` escape, that escape *not* laundering a second change, the
      legacy path, and a testset-sha divergence). Only `manifest.workload_sha`'s
      forbidden-field guard was genuinely untested -- `workload_sha` was called in exactly
      one place in `src/` and imported by no test. Now five tests in
      `tests/test_manifest.py`, parametrised over all five forbidden fields so removing one
      name fails a test that says which; six of them fail with the guard removed.
- [ ] `reliability_gate`'s 0.99 threshold is a hardcoded Python default, not read from
      the plan's own `quality_gates.minimum_parse_valid_rate` field that `validate_plan`
      computes and displays as authoritative. Currently harmless (both are 0.99).
      (Priority: Low today, real if a future plan sets a different value.)
      **Mitigating fact this bullet did not state:** `validate_plan` at
      `experiments.py:246` asserts the plan's declared rate *equals* 0.99, so a plan
      setting a different value is **rejected**, not silently ignored. The real gap
      underneath is broader and belongs with the multi-application work:
      `validate_plan` hardcodes Experiment 5/7's exact numbers throughout
      (`:236-258` -- item id lists, 410/414, alpha 1/64, concurrency levels), so it is a
      single-experiment validator wearing a generic name.

**A fifth gap, of the same class, was not on that list and is now closed.** `cmd_severity`
(~250 lines plus its own argparse block) was never invoked through `main()` by any test --
`tests/test_severity.py` is 1,442 lines and imports `evalgen.severity` but never
`evalgen.cli`. With `cmd_qualify` closed, those two were the only subcommands in that
position. Covered by two tests in `tests/test_cli.py`: the `--deterministic-only` path
end to end with a client factory that raises, asserting the report's own content rather
than an exit code; and that `--dimension product` is refused, since `product` is
deliberately outside the severity taxonomy.

**Two gate-logic gaps found by the same audit were fixed the same night** (both were
mechanical, well-scoped, and safe to verify before merging): `decision()` silently
passing a candidate whose stability comparison was UNDERPOWERED, and `validate_plan()`
never checking which arm's `role` was `incumbent`. See CHANGELOG.md, Fixed.

**Resolved earlier, kept for the record:**

- [x] ~~**The mechanism table stops discriminating as the pack grows, and at 100 items it
      carries no information.**~~ **RESOLVED (2026-08-06).** `MechanismRow` gained
      `always_correct` and a derived, non-monotone `rate` alongside the kept
      PASS/FLAKY/FAIL letter (`report.py`). The letter still saturates by construction --
      that is what made it worth keeping the rate beside it, not instead of it -- but the
      rate does not: one more correct item raises it, one more wrong item lowers it, so
      two all-FAIL rows still separate. `retention_v3`'s 9-family table (up from 5) is
      the first pack this was load-bearing for.
      <details><summary>Original entry, kept for the record</summary>

      Its verdict rule was FAIL if *any* item in a group failed on every replicate,
      which is monotone decreasing in group size: adding items can only push a row
      toward FAIL. At 20 items four of five rows read FAIL/FAIL and `multislot` (n=2)
      was the one row still separating the arms; at 100 items `multislot` grew to 10
      items and collapsed, and all five rows read FAIL/FAIL on both arms. Predicted in
      writing before the pack was built (`docs/eval-improvement-plan.md`, finding 1;
      `docs/testset-v2-plan.md`, caveat 1) and Experiment 3 confirmed it.
      </details>

**Production defects found while building, reproduced deliberately** (not bugs here,
but the reason some code looks odd):

- [ ] Calls with a null, blank or zero `phone_number` are dropped from the product
      dimension entirely, while still being scored in the other two. A class that was
      never evaluated reports `weight = 0, accuracy = 1.0000`. (Priority: Med. Ask the
      app team how many rows are affected before deciding whether to raise it.)
- [ ] An all-empty prediction set scores accuracy 0.8246 with recall 0.0000 on the
      fixture, because true negatives dominate. Distribution-dependent and higher on
      larger single-label sets. (Priority: High for interpretation. The harness does
      **not** inherit this: coverage refusal and recall-based gating exist for it.)
- [ ] Sentiment QA and Telesales hard-set `FN = 0, TN = 0`, making recall identically
      100% and precision identically equal to accuracy. (Priority: High. Affects the
      acceptance criteria on the table for the review, whatever this harness does.)

## ✅ History

- **2026-08-09**: The judge's flags were hand-checked, mostly wrong, the cause fixed --
  and then the fix's own success claim was reviewed and largely withdrawn. Three of
  Experiment 6's four cross-validated ground-truth flags were judge errors with one root
  cause: the prompt carried rule *citations* but never rule *text*. `judge.py` now quotes
  the cited `production-reference/` lines verbatim (hand-computed expectation first,
  default ON, `--no-rule-text` for the old behavior). An A/B measured raw flags 32 -> 18;
  a four-reviewer adversarial pass then found that **the 270 rows are 107 distinct units
  replicated 2-3x** (collapsed: 15 -> 7, `p=0.0386`, INDISTINGUISHABLE at this repo's own
  alpha), that **8 rows were an accidental placebo arm** whose requests were byte-identical
  in both modes and **4 of 8 flipped anyway** (first measurement of judge self-inconsistency
  at temperature 0), and that "dropped to zero flags" was false. The fix also *created*
  3/3 flags on RET-98 and RET-129, traced to a structural asymmetry: the prompt quotes the
  rule for the ground-truth label only, never the competing label. A MAJOR data-safety
  finding (absolute path + OS account name in the shareable export) was fixed with a
  regression test. `EXPERIMENTS.md` Experiment 6 addendum carries the full correction.
- **2026-08-08**: Experiment 7 completed on synthetic Retention v3. Provider
  qualification covered 20 advertised endpoints (120 bounded calls); the selected
  Google/Chutes/AkashML arms then completed 1,242/1,242 parse-valid full calls. Gemini
  remains the reference: Qwen3.6 27B failed stability and Qwen3.6 35B-A3B failed quality
  plus stability. The independent Gemma judge completed 360 advisory opinions and
  flagged 38 possible ground-truth errors for human review. Generation, qualification
  and judge spend was an observed lower bound of approximately US$1.215310. Raw/private
  evidence remains ignored; safe aggregate handoff committed. Standalone pinned suite:
  649 passed, 33 skipped; tracked production-reference mode: 660 passed, 22 skipped.
- **2026-08-08**: Provider-neutral runtime manifests, portable self-contained run
  snapshots, crash-resume journals, application contracts, stricter artifact identity,
  call-clustered paired inference, shareable/private judge surfaces, and decision-grade
  completeness checks made the harness ready for an internal-GPU rerun without changing
  the scoring package.
- **2026-08-07**: Migration decision written down.
  `docs/migration-decision-2026-08-07.md` synthesizes Experiments 1-6 into one
  recommendation: **do not migrate** to `qwen/qwen3.6-27b` or `qwen/qwen3.6-35b-a3b`.
  Every apparent Qwen advantage traced back to the reasoning-regime confound Experiment
  4 found; with reasoning off (Experiment 5B, production's actual regime) both
  candidates FAIL a pre-registered rule. Experiment 6's ground-truth review does not
  change this. Explicitly not `RECONCILED: YES` -- still a recommendation, not a
  verdict, pending production-shaped ground truth. Independently corroborated the
  next day by Experiment 7, run on a different provider pin, above.
- **2026-08-07**: Full audit of the merged enterprise framework (~2,500 lines, never
  before code-reviewed line by line) -- 21 agents, 12 candidate findings, all
  independently verified, zero refuted. Two real gate-logic gaps fixed (`decision()`
  silently passing an UNDERPOWERED stability comparison; `role` never validated), four
  test-coverage gaps recorded (see Known Bugs), two stale `EXPERIMENTS.md` line
  citations corrected, one `sys.path` bug in `test_enterprise_experiments.py` found by
  hand and fixed. Every numeric claim checked against committed evidence matched
  exactly -- no arithmetic was wrong anywhere the audit looked.
- **2026-08-07**: Experiment 6. `src/evalgen/judge.py` -- an independent model
  (`google/gemma-4-31b-it`, reasoning off, pinned to CoreWeave) adjudicates every scorer
  disagreement, diagnostic only, isolation from the verdict path enforced by an AST test
  rather than only claimed in a docstring. 262 items across three pairings, zero parse
  failures: 62.6% ground-truth-correct, 30.5% defensible, 6.9% flagged as a possible
  ground-truth error. Four flags cross-validated by all three independent comparisons;
  `RET-85` is the strongest candidate, same shape as the original RET-11 catch. Nothing
  changed on the strength of any flag. Full report:
  `docs/overnight-audit-and-experiment-6-report.md`.
- **2026-08-06**: Experiment 5A (parallel historical reasoning-regime run).
  `retention_v3` (138 items) scored on all three arms
  under the re-derived bands, 1,242 calls, ~$9.49. Both Qwen arms are **AHEAD** of the
  incumbent on `reason` at alpha=1/64 -- the first AHEAD verdict in this project without
  a repeat-pass caveat -- but it is bought entirely inside the reasoning-regime confound
  Experiment 4 found (2.3-2.6M reasoning tokens on both Qwen arms, zero on the
  incumbent), so it reads as "Qwen with reasoning beats Gemini with none," not "Qwen
  labels Thai better." `product` returned zero informative verdicts across all nine
  comparisons scored: every one landed `d < 6`. `long_context`, read on the
  always-correct metric, showed the incumbent failing consistently at 10x (3 `FAIL`
  items, 0 `FLAKY`) while both Qwen arms were only ever `FLAKY` at either dilation --
  correcting a premature mid-run read that length degrades labelling in general; it
  degrades this one model. The `scorer_sha`-invalidates-comparability defect from
  Experiment 4 recurred mid-run-sequence (a docs commit moved HEAD between arm launches)
  and this time cost a real re-run, not just a footnote.
- **2026-08-06**: Prerequisites for `retention_v3` -- verdict bands re-derived from the
  arithmetic alone (the old n=22 bands returned a directional verdict on two identical
  models 57% of the time by 108 rows), `MechanismRow` gained a non-monotone `rate`
  (resolves the Known Bug below), 16 new pack-validation tests plus a CI step closing the
  hole where `retention_v2` had zero automated checks, and the phone block widened
  `^08100000[0-9]{2}$` -> `^0810000[0-9]{3}$` (strict superset, zero fixture numbers
  moved, 100 in use / 900 free). Suite: 483 passed / 11 skipped standalone, 494 / 0
  differential.
- **2026-08-06**: `retention_v3` authored -- 138 items, the 100-item v2 pack
  byte-identical plus 38 new across four families the pack had zero coverage of before:
  `long_context` (dilated Experiment-3 items, 3x and 10x), `asr_noise` (ten artifact
  classes, hand-derived expectation written twice after the first version's own
  verification method turned out to have imported the code it was meant to check
  independently -- recorded as a process failure in `ASR-EXPECTATION.md` itself, not
  quietly redone), `code_switch`, and `regression`. A budget overrun against the
  pre-registered `+8 to 12`, recorded as one rather than argued away.
- **2026-08-05**: Experiment 4. Third arm, `qwen/qwen3.6-35b-a3b`: not viable, loses to
  the 27B on all three dimensions. The real finding is that re-running the 27B after
  Morph started returning HTTP 400 moved `reason` net **-1 -> +24** on an unchanged
  model id, prompt and pack, because the replacement endpoint (CoreWeave) reasons and
  Morph did not -- the pin is a term in the result, not a detail of the method.
- **2026-08-06**: Experiment 5B enterprise Gate 2 completed. Exactly 1,458 approved
  full/load calls produced both candidate `FAIL` decisions. Committed a self-hashed approval and
  execution ledger plus safe per-arm, paired and summary reports; raw response logs
  remain gitignored. Corrected the offline runtime gate so missing metadata on failed
  calls does not silently replace the 99% reliability rule with 100%; no paid call was
  rerun. The same raw logs generated byte-identical JSON/Markdown reports twice.
- **2026-08-06**: Experiment 5B enterprise framework pre-registered, with zero model
  calls. Added v3 dataset and prompt manifests, the machine plan, provider qualification
  taxonomy, explicit reasoning controls, one-attempt reliability, exact paired verdicts,
  workload/scoring/classification hashes, locked approval gates, load levels 1/4/8 and
  quality-first reports. Offline verification in the isolated worktree: 493 passed / 33
  skipped; 22 skips require deliberately unshared gitignored historical `out/`
  directories, and 11 are the documented production-source checks. Pointing
  `TRUE_SOURCE_ROOT` at the tracked production reference made the differential,
  requirement-pin and boundary selection pass 18/18.
- **2026-08-05**: Run index. `scripts/run_index.py` generates `RUNS.md` from `out/runs/`,
  a committed index of every run with the provenance needed to cite one: model, provider,
  prompt sha, decoding, outcomes, pin proof, cost. `out/` is gitignored because run
  artifacts carry model output verbatim, so until now the whole run history was invisible
  to git and every report cited runs no reviewer could see. 19 runs recorded, 20 tests.
  The suite stands at **451 passed / 11 skipped** standalone and **462 passed / 0
  skipped** with `TRUE_SOURCE_ROOT` set.
- **2026-08-05**: Experiment 3. 100 items, 108 scored rows, 600 calls. **The candidate's
  `reason` lead was noise, and it reversed**: net went from +5/+6 at 22 rows to **-1** at
  108, and every margin is now within 2 items. The stated prediction that would have
  distinguished signal from noise came out on the noise side. Verdict: no accuracy case
  for migrating, and a reliability case against it. The 22-row figures from Experiments
  1-2 are superseded and are not to be quoted again.
- **2026-08-05**: `retention_v2` test set: 100 items / 108 rows, with `retention_v1.*`
  frozen so Experiments 1-2 stay reproducible. `export_xlsx.py` gained a **Side by side**
  sheet, added to `EXPECTED_SHEETS` so its presence and position are verified with the
  rest.
- **2026-08-05**: Experiment 2. Five replicates on both arms, 400 calls. **The candidate
  is nondeterministic at temperature 0 and the incumbent is not** -- `N_flip` 8 against
  0 on base, over 200 byte-identical calls per arm. `reason` net on `e1` came out **+6**,
  crossing the pre-registered AHEAD band, and was **not** called AHEAD: the aggregate
  table is scored on replicate 1 alone (`cli.py:25-31`), so `+6` is one draw from the arm
  that flips, and the same measurement has now produced +5, +4 and +6. Both arms were
  raised, not just the candidate, because unequal replicate counts give the two arms
  unequal chances to show instability (`report.py:635-639`, the `_header` warning;
  `EXPERIMENTS.md:245` cites `573-577` for the same warning and that line number is
  wrong -- it lands in `render()`'s docstring about section order, and did so when it
  was written).
- **2026-08-05**: Phase 1 of the post-Experiment-1 eval plan. RET-11's ground truth
  corrected -- it gained `dissatisfied service`, which moved `test_fabrication.py`'s
  hand-checked literals from 42/18 and 30/19 to **39/15 and 29/18**, counts updated with
  the change rather than the reverse. Both open class boundaries arbitrated in
  `tests/fixtures/testsets/VOCABULARIES.md`. `src/evalgen/evidence.py` added as a
  deterministic diagnostic that is **never a scored dimension**, feeds no verdict and
  ranks no arm, with its hand-computed expectation written before the implementation --
  the direct lesson of the retracted `keyword` metric. `report.py` gained section 6
  (cost, tokens, latency), placed after the aggregates rather than before them.
- **2026-08-05**: External benchmark comparison, Qwen3.6 27B against Gemini 2.5 Flash,
  benchmark by benchmark (`docs/model-comparison-qwen-vs-gemini.md`). Reference only, and
  it disagrees with this repository's measurements for a stated reason: the public index
  scores the candidate in its reasoning configuration, which production does not run.
- **2026-08-05**: `source-code-review/` renamed `production-reference/` and now
  **tracked**, reversing the earlier decision to block it, with `.gitignore` hardened
  against production source and stray workbooks in the same pass. `.gitattributes` now
  forces LF, after `core.autocrlf` had silently corrupted a fixture.
- **2026-08-05**: `src/evalgen/` added -- the OpenRouter model-calling pipeline, and the
  point at which this repository started running models rather than only scoring them:
  cli, runner, client, request, outcomes, flatten, prompts, decoding, report, fabrication,
  testsets, config, console. Kept out of `src/evalharness/` so the scoring library still
  imports no networking library, with `test_boundary.py` parsing the AST to assert it.
- **2026-08-04**: Version pin gate fixed to survive extraction. It had located
  production's `requirements.txt` by a hardcoded relative path, so the one gate this
  build made a point of demonstrating silently stopped running once the repo moved.
  Standalone went from 78 passed / 15 skipped with nothing enforcing the pins, to 82
  passed / 11 skipped with the gate among the passing.
- **2026-08-04**: Extracted to its own repository in 14 commits, `.gitignore` first
  and alone, fixtures committed before the metric code they check.
- **2026-08-04**: README and data contract written. The data contract asks for the
  smallest set of data that makes a defensible comparison possible, with a reason
  attached to every item.
- **2026-08-04**: Version pin gate added and demonstrated **failing** under a
  mismatched interpreter before being trusted.
- **2026-08-04**: Runtime data-directory refusals. `EVAL_HARNESS_DATA_DIR` has no
  default and must resolve outside any git worktree.
- **2026-08-04**: Run manifest split into blocking and recorded fields, after an
  earlier draft blocked on decoding-config equality that is unsatisfiable across
  backends and would have been bypassed on every real run.
- **2026-08-04**: Paired comparison, 2x2 disagreement table, coverage refusal, HMAC
  item keys and the PII guard.
- **2026-08-04**: Differential test against True's real production scorer, reaching it
  without cloud credentials via stub environment and `object.__new__`.
- **2026-08-04**: Three scorers with three denominators, plus the adapter that refuses
  to guess the workbook layout.
- **2026-08-04**: Hand-computed fixture pack committed **before** the metric code, so
  the discipline lives in the history rather than only in a README claim.
