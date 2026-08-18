# Tightening the harness

**Date:** 2026-08-18 · **Scope:** the eval machinery, not any one experiment

## The thesis

This harness computes carefully and publishes carelessly.

The scoring code is unusually well defended — a package boundary enforced by AST parse, an
exact paired test that refuses to call underpowered results a tie, three independent
derivations of the ground truth, coverage refusals, sha-pinned plans. Very little goes wrong
inside it.

Everything that has actually gone wrong went wrong at the **publish boundary**, in the gap
between a number the code computed and a number a person read:

| retracted claim | what it really was | how long it stood |
|---|---|---|
| entity recovery 67.5% vs 92.9% | a whitespace convention in the scorer | until re-derived by hand |
| Qwen3-ASR CER **0.673** | one decoder failure pooled into a corpus mean | published, quoted, 6x wrong |
| "whitespace inflates CER" | `chars()` already strips whitespace | a claim invented mid-analysis |
| DEVLOG entity 320/465 | a figure from a scorer two revisions old | weeks |
| pack-A F1 printed under a pooled heading | two scopes, one table | until an agent checked |
| business accuracy "identical, to the call" | identical counts, different calls | until an agent checked |

Six wrong published numbers. **Zero of them were bugs in the scorer.** Every one was a
correct number described wrongly, or a stale number nobody re-derived.

Meanwhile: **525 numeric claims live in `docs/*.md` and not one test checks any of them
against the JSON it came from.** That is the hole.

Today's five code bugs — Clopper-Pearson's inverted lower bound, the `math.comb` overflow
past n≈2000, a short-degenerate-output false positive, a periodic test helper, a silent
LF→CRLF rewrite of a committed golden — were all caught by tests within minutes. The
machinery works. It is the reporting layer that has no machinery.

**So the priority is not more metrics. It is closing the gap between what the code knows and
what the reader is told.**

---

## Tier 1 — stop a wrong number reaching a reader

### 1.1 Verify published figures against their source (~1 day)

A test that walks `docs/*.md`, extracts tagged claims, and re-derives each from the JSON it
came from. Claims opt in with a marker so prose stays prose:

```markdown
| call_result | <!--claim:pooled-bands.json:business_accuracy.qwen38.call_result-->93.1%<!--/--> |
```

Catches the DEVLOG-320/465 class outright, and the pack-A-under-a-pooled-heading class if
the marker names the scope. Start with the four decision documents rather than all of
`docs/`; a checker that must be silenced to land a doc gets silenced.

**Why first:** it is the only item that addresses the failure mode that has actually
occurred, six times.

### 1.2 Make provenance stamps unforgeable (~half a day)

Both ASR reports stamp `scoring_code_sha256: bed27990…`, which matches **no committed
version** of `score_asr.py`. They were generated from uncommitted code and the entity fields
patched in afterwards without refreshing the stamp. The numbers happen to reproduce at HEAD —
verified — so this is a broken audit trail rather than a wrong result. But a stamp that
cannot identify the code that produced it is not a control.

- Stamp the git commit alongside the file hash, and stamp `dirty: true` when the tree has
  uncommitted changes to the scoring path.
- A test that refuses any committed report whose `scoring_code_sha256` matches nothing in
  history.

### 1.3 Wire in the strict coverage guard (~1 hour)

`compare.check_exact_coverage` is defined, tested, and documented "New decision paths should
call this helper" — and is called from no production code. Only the loose 2%-tolerance
`check_coverage` is wired in, at `cli.py:3455`. Wire the strict one into the decision path,
keep the loose one for exploratory runs, and make which ran appear in the report.

---

## Tier 2 — make the gates actually fire

Today added a leak probe, a runaway detector, a phone census, ten gate tests. **All of them
only run if a human remembers.** There is no CI.

### 2.1 One verification command (~half a day)

`make verify` / `scripts/verify.py` running, in order and failing loudly:

```
root suite · asr-eval suite · score_asr --self-test · leak_probe
validate_audio · phone census · experiment-check on every plan JSON
```

Then the same as a GitHub Action. The suites take ~2 minutes; there is no reason not to.

### 2.2 A supported golden-regeneration path (~1 hour)

Regenerating the report golden today required a monkeypatching script, and the first attempt
silently rewrote every line ending LF→CRLF — git showed 305 deletions and 354 insertions,
in which the 49 real lines were unreviewable. Add `--regenerate-golden`, force `newline="\n"`,
and print the added/removed counts so the reviewer sees the shape before committing.

---

## Tier 3 — close the metric gaps E23 committed to

### 3.1 Critical-entity F1 (~2 days)

`score_entities` iterates reference entities only. There is no false-positive path, so **a
model that invents a phone number pays nothing**, and F1 is not derivable — only a recall-like
hit rate. Build hypothesis-side extraction (`numeric_runs()` and `spoken_digit_runs()` already
enumerate candidates; `date` and `package` need matchers), and define the "critical" tier
explicitly as phone / amount / id.

This also fixes a live misreading: both arms score 450/465, but Gemini matches 416 by surface
form and 189 by value while Qwen matches 337 and 404. The identical total hides the largest
behavioural difference between them.

### 3.2 Failure taxonomy and audio-arm operations (~1 day)

`Outcome` covers empty (2 ways) and invalid (3 ways). **Timeout collapses into
`transport_error`; repetition is not in the enum at all.** Add both. Aggregate the audio arm's
latency and cost — E21 records both per row and never sums them. Keep `cost_usd: None` for the
self-hosted side; rendering "$0.00" beside Gemini's real charge is a false comparison.

### 3.3 Align the reason denominators (~half a day)

The two arms are scored on 157 vs 159 rows on pack A because they emit different numbers of
orphan predictions, so the headline 76.3-vs-79.8 reason-precision comparison is across
slightly different populations. Either align them or print both denominators beside the
figure.

---

## Tier 4 — operational

### 4.1 Parallelise scoring (~half a day, zero risk to the numbers)

Scoring is O(len(ref) × len(hyp)) pure Python: ~10 minutes for 20 items, so **~1.5 hours per
arm at 138**, and with two arms and three replicates that is most of a day per run. That cost
is itself a correctness risk — it is what makes people score a subset and compare it to a full
set.

Fix by running the existing DP across items in a process pool. **The arithmetic is untouched**,
which matters: `requirements.txt` and the DP are load-bearing, and swapping in a faster edit
distance would change what the scorer computes. Same function, more cores.

### 4.2 The two decisions only you can make

- **git-lfs before any audio lands.** 138 calls ≈ 819 MB of incompressible PCM; `.git` is
  already 523 MB (with 362 MB of `tmp_pack_*` garbage that wants a `git gc` regardless).
  Committing it makes every clone a permanent ~1 GB proposition.
- **`docs/ask1-email-draft.md`.** Two header rows, no customer data, no privacy review.
  Unsent since 2026-08-09, and the only thing that can retire `RECONCILED: NO`. Everything
  above makes a screening harness more trustworthy; this is the one item that makes it a
  production gate.

---

## Suggested order

| | item | effort | why here |
|---|---|---|---|
| 1 | 2.1 verification command | 0.5d | makes every existing gate real; unblocks the rest |
| 2 | 1.2 provenance stamps | 0.5d | cheap, and the audit trail is currently broken |
| 3 | 1.1 doc-figure checker | 1d | addresses the failure mode that has actually occurred |
| 4 | 1.3 strict coverage | 1h | a written intention that was never wired up |
| 5 | 4.1 parallel scoring | 0.5d | unblocks running E23 at all |
| 6 | 3.1 entity F1 | 2d | the largest real metric gap |
| 7 | 3.2 failure taxonomy | 1d | requested for the E23 report |
| 8 | 2.2, 3.3 | 1d | tidy-ups with real teeth |

About a week and a half, and the first four days are the ones that change whether a wrong
number can reach a decision.

## What this deliberately does not propose

**More metrics.** The harness already computes more than it reports — per-class precision and
recall existed from the beginning and were discarded at the report boundary until today. The
constraint is not measurement.

**Replacing the paired test with confidence intervals.** They answer different questions and
both belong in the report. The intervals added today are for "how precisely do we know this
arm's accuracy"; `exact_band` remains the instrument for "is the candidate different".

**A faster edit distance.** `rapidfuzz` agrees with the committed DP on distance but splits
S/D/I differently on 96 of 400 random pairs, and that split drives dropped-passage detection.
The contract path keeps the DP that produced every published number.
