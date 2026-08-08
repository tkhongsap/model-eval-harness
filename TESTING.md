# Testing & Verification

## Quick Start

```powershell
python -m venv .venv
.venv/Scripts/python -m pip install -r requirements.txt
$env:PYTHONPATH = 'src'
.venv/Scripts/python -m pytest tests/ -q
```

On macOS/Linux, use `.venv/bin/python` and set
`export PYTHONPATH="$PWD/src"` before running the suite. This explicit path is required
because the repository intentionally has no `pyproject.toml` or editable install yet.
Commands below assume `PYTHONPATH` remains set in the current shell.

Historical fresh-checkout baseline on 2026-08-07: **524 passed, 33 skipped** (computed:
498 + the 26 self-contained tests then added for `judge.py` and its audit-fix
regressions, not independently re-measured against an actual fresh clone). This predates
the runtime, artifact, application-contract, and decision-grade tests in the current
tree. Run the suite and report the observed current count instead of treating 524 as an
expectation.

At that baseline, 11 skips needed production source/pins and 22 integration checks
needed historical gitignored `out/` run directories that a fresh checkout did not
contain. Read the reasons printed by the current run; a skip count alone is not proof.

The scoring and generation environments are intentionally separate. For the exact
Python 3.12 setup, private data-root contract, zero-call preflight, and OpenRouter or
internal-GPU commands, use [the team GPU runbook](./docs/TEAM_GPU_RUNBOOK.md).

### Runtime and durable-artifact checks (offline)

```bash
.venv/Scripts/python -m pytest \
  tests/test_runtime.py tests/test_artifacts.py tests/test_runner.py tests/test_paths.py -q
```

These tests make no model call. They validate non-secret runtime manifests and
fingerprints, OpenRouter-versus-generic request behavior, private destinations outside
Git, atomic authoritative writes, journal start/result and contract hashes, detection
of unresolved dispatched calls or torn journal records, bounded scheduling, checkpoint
failure handling, run-relative bundle inputs, and exact-contract resume.

Validate the example runtime manifest without installing or importing the OpenAI SDK:

```bash
.venv/Scripts/python -c \
  "import json,sys; from pathlib import Path; sys.path.insert(0,'src'); from evalgen.runtime import RuntimeSpec; p=Path('configs/runtime.local-gpu.example.json'); r=RuntimeSpec.from_manifest(json.loads(p.read_text(encoding='utf-8'))); print(r.runtime_id, r.fingerprint())"
```

The example deliberately contains placeholders. Schema validity does not make those
placeholders acceptable provenance for a decision-grade run.

## Two modes, two different counts

This repository proves more when True's production source tree is reachable. Report
whichever number you actually ran, and say which mode.

| Mode | Command | Evidence to report |
|---|---|---|
| **Fresh standalone checkout** | `PYTHONPATH=src pytest tests/ -q -rs` | Observed pass/skip count and every skip reason. Historical reference only: **524 passed, 33 skipped** on 2026-08-07 before the current additions. |
| **With production source and historical local `out/`** | `PYTHONPATH=src TRUE_SOURCE_ROOT=<path> pytest tests/ -q -rs` | Observed count and resolved source path. Historical reference only: **546 passed, 11 skipped** standalone and **557 passed, 0 skipped** with `TRUE_SOURCE_ROOT`, measured 2026-08-07 before the current additions. |

For the Experiment 5 implementation audit, the focused production differential,
dependency-pin and boundary selection passed **18/18** with `TRUE_SOURCE_ROOT` set to
the tracked `production-reference/sentiment-batch-retention-main` tree.

```bash
# Windows, pointing at the vendored archive in life-os
set TRUE_SOURCE_ROOT=C:\Users\te90056471\my-github\life-os\work-work\projects\2026-07-local-llm-platform\source-code-review\sentiment-batch-retention-main
set PYTHONPATH=src
.venv\Scripts\python -m pytest tests/ -q
```

As of 2026-08-05 a reference copy is also tracked in this repo at `production-reference/`
(see its `.gitignore` entry for why). Pointing `TRUE_SOURCE_ROOT` at
`production-reference/sentiment-batch-retention-main` instead of the life-os path works
identically; `tests/production_ref.py`'s own default still resolves to a directory
*outside* the repo, so standalone `pytest tests/ -q` keeps skipping the differential
tests unless `TRUE_SOURCE_ROOT` is set explicitly -- that default was left alone
deliberately rather than widened to auto-discover the in-repo copy. Historical-run
integration tests also skip in a fresh clone because `out/` is deliberately uncommitted.

The skipped tests are the differential check against production's real scorer, and the
cross-check of our pins against production's `requirements.txt`. **The version pin gate
itself always runs**, in both modes: it depends on nothing outside this repository.

## Enterprise Experiment 5 — offline gate

```bash
.venv/Scripts/python scripts/evalgen.py experiment-check
.venv/Scripts/python scripts/evalgen.py experiment-budget
.venv/Scripts/python -m pytest tests/test_enterprise_experiments.py -q
```

The first command validates the plan, asset hashes, fixed slices, prompt registry, call
budget, reliability threshold and lock requirements. It makes no network call and reads
no API key. The budget command uses the committed provider-price snapshot and worst-case
token caps; it also makes no call. The unit suite pins explicit reasoning-off request shape, exact bands,
provider failure classifications, complete committed qualification evidence, the
410/414 gate, paired item stability, operational accounting, committed Gate 2
approval/execution/report hashes, raw-output exclusion, and refusal to run a full arm
while a plan is draft.

`qualification-report` is the no-network path for deterministically reclassifying a
recorded qualification run after a classifier correction; it reads no key and never
reruns paid rows. `qualify` and `experiment-run` are intentionally absent from offline
verification because they make paid calls. Gate 1 qualification is complete. Full/load
execution is also complete: the separately self-hashed Gate 2 approval preserves the
immutable plan SHA, and the committed execution ledger plus report set are verified
without reading a key or making a network call.

## Verification Scripts

### 1. The pins are correct

```bash
.venv/Scripts/python -m pytest tests/test_requirements.py -q
```

Not cosmetic. Under pandas 3.x the production scorer's `dtype == 'object'` guard is
False, string normalisation never fires, and `call_result` accuracy collapses from
0.75 to 0.25 with every weight zeroed.

**Prove the gate still works** by running it under a mismatched interpreter:

```bash
python -m pytest tests/test_requirements.py -q     # system python, unpinned
```

Expected: **failure**, naming the package, both versions, the interpreter and the
consequence. A gate that has only ever been seen to pass is not yet a gate.

### 2. The scorers match the hand computation

```bash
.venv/Scripts/python -m pytest tests/test_metrics.py -q
```

23 exact integer expectations, hand-derived in `tests/fixtures/WORKED-COMPUTATION.md`
before the code existed.

### 3. The scorers match True's real production code

```bash
TRUE_SOURCE_ROOT=<path> .venv/Scripts/python -m pytest tests/test_differential.py -q
```

Imports the actual `FactCheckerModule` and asserts agreement class by class. Skips
with a stated reason when the source tree is absent, or when pandas is not at
production's pin, rather than comparing against a production whose normalisation has
silently stopped firing.

### 4. Customer data cannot reach the repository

```bash
# git check-ignore -q accepts only ONE path, so loop rather than listing them
for f in data/gt.xlsx data/preds.parquet data/notes.ipynb out/report.html build/x.py; do
  git check-ignore -q "$f" && echo "IGNORED  $f" || echo "LEAK     $f"
done

.venv/Scripts/python -m pytest tests/test_paths.py tests/test_compare.py -q -k "refus or shareable or worktree"

git grep -ohE "\b0[689][0-9]{8}\b" -- . ':!production-reference/' | sort -u   # 0810000xxx only
```

Three independent controls: directory-level ignores, a runtime refusal of any data
directory inside a git worktree, and a writer that raises on customer columns.

The grep is the cheap fourth check, and reading its output needs two facts.
`production-reference/` is excluded because it is True's own committed source: its tests
carry numbers this repository never issued and never drew from its block, so including
them makes the check noisy rather than safer. And the block is `^0810000[0-9]{3}$` —
`0810000000`–`0810000999`, `src/evalgen/testsets.py:135` — widened from
`^08100000[0-9]{2}$` on 2026-08-06, when `retention_v2` used the last of the old 100.

Measured 2026-08-06: 100 numbers are allocated to fixtures, all of them in
`0810000000`–`0810000099`, leaving 900 of the block free. The remaining hits are all test
inputs rather than fixtures, and they split two ways, which matters because a reader
checking this list needs to know which ones are *supposed* to be outside the block:

  * **Inside the block, asserted to be accepted** — the two `PHONE_PATTERN` positive cases
    at `tests/test_testsets.py:412` pinning the first and last of the 900 the widening
    bought. These are in-block by construction; they are not leaks and not exceptions.
  * **Outside the block, asserted to be refused** — the `PHONE_PATTERN` negative cases in
    `tests/test_testsets.py`, and the hallucinated payload phone at
    `tests/test_flatten.py:237` that `to_rows` must discard in favour of the item's own.

Deliberately no literal out-of-block number is quoted in this paragraph: doing so would
put it in the grep's own output and teach the reader to skip a line. Anything outside the
block that is not one of those refusal cases is a leak.

### 5. The generation pipeline runs end to end, with no network

```bash
.venv/Scripts/python -m pytest tests/test_cli.py -q
```

`tests/test_cli.py` drives fake completions through the whole chain -- `runner` ->
`outcomes.classify` -> `flatten.to_rows` -> `records.from_row` -> `metrics.score_*` ->
`report.mechanism_table` -- and asserts the CONTENT of the mechanism table, not that a
table was produced. Every module here passes its own unit tests against a pipeline that
does not fit together; these are the joins:

- a perfect arm (answers built from each item's own ground truth) must PASS all five
  mechanisms;
- an arm broken on RET-10 must FAIL `thai_linguistic` and nothing else;
- an item that flips between replicates must be FLAKY, never PASS;
- an arm whose every call died in transport must still emit 22 scored rows per
  replicate, the same count a perfect arm produces.

The client is faked throughout, and the two commands that must never call a model
(`check`, `--dry-run`) replace the client factory with something that raises.

### 5b. The three counters that survive an arm answering nothing

```bash
.venv/Scripts/python -m pytest tests/test_flatten.py tests/test_cli.py -q
```

`Coverage.parse_failures` is **not** sufficient to keep the product dimension honest,
and this is the proof that says so rather than a docstring claiming it. Two different
arms reach weighted product recall **1.000** without naming a single product, because
`flatten.to_rows` emits a ground-truth skeleton to keep the grain:

| route | `parse_ok` | `parse_failures` | caught by |
| --- | --- | --- | --- |
| transport error, refusal, empty, unparseable | False | 22 | `Coverage.parse_failures` |
| parsed, all required keys, `{"product": {}}` | True | **0** | `flatten.named_no_product` -> `ArmSummary.answered_nothing` |

The second row is the one nothing in `Coverage` can see, so it is measured directly:

- `test_the_empty_product_arm_is_credited_too_and_parse_failures_stays_zero` scores the
  empty-product arm over the whole pack and asserts product recall 1.000 **with**
  `parse_failures == 0`, beside the two counters that do separate it (call_result and
  reason recall, both 0.000);
- `test_named_no_product_cannot_drift_from_the_route_to_rows_took` pins the counter to
  the routing across all six "named nothing" payload shapes;
- `test_an_arm_that_named_no_product_is_counted_where_coverage_cannot_see_it` drives it
  through the real run loop and `arm_summary`, because the counter is worthless if the
  CLI forgets to call it.

Two further guards are asserted rather than trusted, both of which used to be
implemented with no test at all:

- `test_two_product_keys_that_fold_together_are_refused_not_silently_dropped` — a
  payload naming both `Postpaid` and `POSTPAID` emits two rows that collapse to one
  merge key, and `metrics.outer_join` keeps whichever came last with no number moving
  in `Coverage`. `cli.replicate_records` refuses instead.
- `test_the_two_ground_truths_are_reconciled_before_any_call` — `_gt_disagreements`
  runs in the run **preflight**, not only in `check`, which is a command a caller can
  skip.

Each of these five was confirmed to FAIL against a mutant with its guard removed before
being trusted; a test that passes whether or not the code works is not evidence.

### 6. Nothing is spent before the prompt has been read

```bash
.venv/Scripts/python scripts/evalgen.py check
.venv/Scripts/python scripts/evalgen.py baseline --arm incumbent \
    --model google/gemini-2.5-flash --dry-run
```

`check` validates the pack: the prompt assembles from committed assets, every evidence
span still appears verbatim in its own transcript, and the testset's own `gt` agrees
with the ground-truth CSV. No key, no network, no cost.

`--dry-run` writes the exact request bodies to `out/runs/<run>/requests.jsonl` and the
assembled prompt to `prompt.txt`, making zero API calls. The bodies come from
`request.build_request`, which is also what `client.complete` sends -- pinned by
`test_the_dry_run_body_is_the_body_the_client_would_send`, which drives the real client
against a stubbed SDK and compares. Without that, the file a reviewer reads could
describe a request nobody makes.

Exit codes are three and the middle one matters: `0` clean, `1` the harness ran and
found problems (a failing mechanism, an invalid pack), `2` the harness refused to run
or to compare. A caller that cannot tell `1` from `2` will eventually read a refusal as
a clean sheet.

### 7. An arm is ONE backend, and the proof is not the `provider` field

```bash
.venv/Scripts/python scripts/evalgen.py stability --arm pin-proof \
    --model qwen/qwen3.6-27b --provider Morph \
    --items RET-01,RET-11,RET-16 --repeats 3
```

Read the line `prompt_tokens fingerprint  N/N items returned exactly one value`. It
must be `3/3`.

**CORRECTED 2026-08-05: this command used to pin `Alibaba`, and copying it was a trap.**
Alibaba is the endpoint whose constrained decoder is broken (EXPERIMENTS.md run 1.2, and
below), so a reader running the old line got 20 of 20 `schema_violation` and would have
concluded the *model* was broken. `Morph` is what every run since 1.4 has used for
`qwen/qwen3.6-27b`. The Alibaba result is not deleted, because it is what this section
teaches — it is the worked example at the end.

**Why that line and not the provider histogram.** MEASURED 2026-08-04: a 60-call
`qwen/qwen3.6-27b` run was served by two backends under one model id. One answered
with `reasoning_tokens=0`, 2538-2643 prompt tokens, a 5.8s median and 10 of 31 rows in
`schema_violation`; the other with `reasoning_tokens>0`, 3583-3931 prompt tokens, a
71.7s median and 0 of 29. `observed_models()` printed `60 x qwen/qwen3.6-27b` and saw
nothing, because `response.model` is the model id and the model id never changed.

`observed_providers` would have caught that one, and it is recorded now -- but it is
**the router describing its own routing**, so a pinned run whose `provider` echoes the
pin has proved only that the field echoes the pin. `prompt_tokens` cannot be echoed:
every replicate of an item sends a byte-identical request, so the count is a pure
function of the backend's tokenizer. Two values means two builds, whatever `provider`
said. On the two 2026-08-04 runs the check reproduces the defect exactly -- the
incumbent splits 0 of 20 items, the unpinned candidate 14 of 20.

`repeats=1` cannot split, and the CLI says so instead of printing a clean `20/20`
that would read as evidence.

**A pinned run can fail loudly, and that is the feature.** `--provider` sends
`allow_fallbacks: false`, so a busy or ineligible endpoint returns 404/429 rather than
being quietly served by a second build. Measured against `qwen/qwen3.6-27b`: of nine
listed endpoints, four are refused outright by `require_parameters` because they cannot
honour both `structured_outputs` and `seed`.

**The worked example: Alibaba.** Unpinned (EXPERIMENTS.md run 1.1), `qwen/qwen3.6-27b`
returned 50/60 ok with its schema violations spread across two backends, and nothing on
disk could say which backend produced them. Pinned to Alibaba (run 1.2) the same request
returned **20 of 20 `schema_violation`**, every one a bare JSON *number* literal where
the schema root is `object`, `finish_reason: stop`, no truncation. Pinned to Morph
(run 1.4), 60/60 ok — as it also is from Chutes and CoreWeave. The defect did not change
between those runs; the pin made it **attributable**, which is the whole argument for
pinning. An arm that cannot name its backend cannot tell an endpoint defect from a model
defect, and will file the first as the second.

### 8. `v9_16_e1` changes exactly one thing, and the thing it changes is measurable

```bash
.venv/Scripts/python -m pytest tests/test_prompts.py tests/test_fabrication.py -q
```

Two gates, and they fail for different reasons.

**The diff gate** (`test_only_the_example_reasons_differ`). `v9_16_e1` is `v9_16_base`
with the worked example's `secondary.reason` and `third.reason` blanked to `""`. It is
built by applying a documented substitution list to the base text, never by forking
`retention_wrapper.txt`, so the two cannot drift apart by anything except the named
edits. The test asserts the line diff is exactly those two replacements, that every
character outside the single ` ```json ` block is byte-identical, and that the two
parsed examples differ at exactly two JSON paths. An ablation that moved two things
would report one number and could not say which change produced it. **Verified against
a mutant**: adding a third edit outside the example fails the gate.

**The measurement** (`fabrication.fabrication_rate`). Counts the reason labels a run
emitted into `secondary`/`third` slots the ground truth leaves blank, and how many of
those are one of the BASE example's three values -- the base's, not the variant's, since
scoring an e1 run against e1's own blanked example would report 0 by construction.
Reproduced against the two 2026-08-04 runs:

| Run | Model | Invented | Example-derived | Rate |
|---|---|---|---|---|
| `20260804-222943Z-incumbent` | gemini-2.5-flash | 39 | 15 | 0.385 |
| `20260804-224050Z-candidate` | qwen3.6-27b | 29 | 18 | 0.621 |

**CORRECTED 2026-08-05: this table read 42 / 18 / 0.429 and 30 / 19 / 0.633.** Neither
run log moved, and neither can — they are frozen artifacts. What moved is what they are
measured *against*. RET-11's ground truth gained `secondary = dissatisfied service`
(licensed by `prompt.py:4361`, evidence span `ไม่มีใครตามเรื่องเลย`), and a slot counts as
invented into only while the ground truth leaves it **blank** (`fabrication.py:149-151`),
so call 5011's `secondary` stopped being a blank slot. Gemini put `dissatisfied service`
there in all three replicates and so lost 3 from both counters (42-3, 18-3); Qwen has
only one `parse_ok` replicate at 5011 and lost 1 (30-1, 19-1). Both figures were
re-derived from the logs, not back-fitted until the suite went green.

`save cost` alone is 13 of Qwen's 29 — **unmoved** by that correction, which matters more
than the headline: what changed was the denominator, not the finding this variant was
built to test.

Those two rows are asserted as literals in `tests/test_fabrication.py`. If either moves,
**either** the helper changed **or** the ground truth did, and the two are told apart by
which artifact has a diff. The ground-truth exception is spent above; a move with no
ground-truth change is the helper.

**`v9_16_e1` depends on `decoding.decoding_schema` deviation 2.** The committed port
`schemas/retention.json` does not allow `""` in a `reason` enum, although every
`reason.description` in it says "Use empty string if not applicable" -- a
self-contradiction that predates this variant. `decoding_schema` adds `""` to the enums
it sends, which is the only reason a model can copy the blanked example at all. Without
that deviation every copying model would land in `schema_violation` and an e1 run would
be measuring the grammar rather than the example. Both facts are pinned:
`test_e1_example_is_legal_under_the_grammar_that_is_actually_sent` and
`test_e1_example_fails_the_committed_port_schema_at_exactly_the_blanked_slots`.

### 9. The pack is an argument, and there are three of them

```bash
.venv/Scripts/python scripts/evalgen.py check          # retention_v1, the default
.venv/Scripts/python scripts/evalgen.py check \
    --testset tests/fixtures/testsets/retention_v2.jsonl \
    --gt tests/fixtures/testsets/retention_v2.gt.csv
.venv/Scripts/python scripts/evalgen.py check \
    --testset tests/fixtures/testsets/retention_v3.jsonl \
    --gt tests/fixtures/testsets/retention_v3.gt.csv
```

`--testset` / `--gt` default to `retention_v1.*` (`cli.py:131-132`), so a command with
neither flag scores the 20-item pack. The same pair is accepted by `baseline` and
`stability`, and by `compare` to override the paths a run recorded.

| Pack | Items | Scored rows | Status |
|---|---:|---:|---|
| `retention_v1` | 20 | 22 | **Frozen.** Experiments 1 and 2 cite it, so it does not move. |
| `retention_v2` | 100 | 108 | **Frozen.** Experiment 3 and 4 cite it; `RET-01`…`RET-20` are byte-identical to v1. |
| `retention_v3` | 138 | 150 | Experiment 5. v2 prefix plus 38 versioned robustness items. |

Verified above by running `check` against each. The scored row is
`(call_id, phone_number, product)` in every pack, which is why the v1/v2/v3 row counts
do not always equal their item counts.

What v2 buys: the six reason classes sitting at **support 1** in v1 are gone — v2's
minimum reason-class support is **6**, across the same 11 classes, so a single miss no
longer swings a class's recall from 1.00 to 0.00. What it does not buy is independence.
It is not a holdout (same author, same procedure), and its `other` class is 80% flood
scenarios, so that class can be passed by keyword. Both are recorded in
`tests/fixtures/testsets/README.md` and `docs/testset-v2-plan.md`; read them before
quoting a v2 per-class number.

### 10. Every run is indexed, because `out/` is not

```bash
.venv/Scripts/python scripts/run_index.py                   # writes RUNS.md
.venv/Scripts/python scripts/run_index.py --check           # exit 1 if RUNS.md is stale
.venv/Scripts/python -m pytest tests/test_run_index.py -q   # 20 passed
```

`out/` is gitignored deliberately — run artifacts carry model output verbatim
(`cli.py:135-137`) — and the consequence is that every `run_id` quoted in
`EXPERIMENTS.md` points into a directory a reader who clones this repository does not
have. `RUNS.md` is the **only committed record that those runs existed**: one row per
run with arm, model requested, provider, prompt id, outcome counts, pin proof, cost, and
the testset and scorer shas.

It carries provenance and no payload, which is precisely why it can be committed while
`out/` stays ignored: the generator reads `run.json` only and never `run.jsonl`, where
the model's text lives. It also recomputes nothing — every column is copied out of what
a run recorded at the time, because a second computation is a second answer to reconcile.

`--check` regenerates and diffs instead of trusting. A committed index nothing verifies
is a claim about the past that quietly stops being true. Dry-run directories have no
`run.json` and are listed as a footnote rather than dropped, so the index agrees with the
directory listing for a reason the reader can see.

## Continuous Integration

`.github/workflows/ci.yml` runs the standalone suite on push and pull request to
`main`. It cannot run the differential test, because True's production source is not
available to CI, so **CI proves less than a local run with `TRUE_SOURCE_ROOT` set**.
Treat a green CI badge as "the harness is internally consistent", not as "the harness
agrees with production".

## What none of this proves

The implementation can be **READY for team setup** while the migration decision remains
**INCONCLUSIVE**. Every report prints `RECONCILED: NO` until a real company-GPU arm has
run on approved real labelled data and the result has been checked against the app's
live Gemini fact-check report. A fully green suite proves the harness computes and
preserves what it claims; it does not prove that a model is good enough to deploy.
