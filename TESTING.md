# Testing & Verification

## Quick Start

```bash
python -m venv .venv
.venv/Scripts/python -m pip install -r requirements.txt   # Windows
# .venv/bin/python -m pip install -r requirements.txt     # macOS/Linux
.venv/Scripts/python -m pytest tests/ -q
```

Expected: **431 passed, 11 skipped**.

The 11 skips are correct and expected standalone. See "Two modes" below.

## Two modes, two different counts

This repository proves more when True's production source tree is reachable. Report
whichever number you actually ran, and say which mode.

| Mode | Command | Expected |
|---|---|---|
| **Standalone** | `pytest tests/ -q` | **431 passed, 11 skipped** |
| **With production source** | `TRUE_SOURCE_ROOT=<path> pytest tests/ -q` | **442 passed, 0 skipped** |

```bash
# Windows, pointing at the vendored archive in life-os
set TRUE_SOURCE_ROOT=C:\Users\te90056471\my-github\life-os\work-work\projects\2026-07-local-llm-platform\source-code-review\sentiment-batch-retention-main
.venv\Scripts\python -m pytest tests/ -q
```

As of 2026-08-05 a reference copy is also tracked in this repo at `production-reference/`
(see its `.gitignore` entry for why). Pointing `TRUE_SOURCE_ROOT` at
`production-reference/sentiment-batch-retention-main` instead of the life-os path works
identically; `tests/production_ref.py`'s own default still resolves to a directory
*outside* the repo, so standalone `pytest tests/ -q` keeps skipping the differential
tests unless `TRUE_SOURCE_ROOT` is set explicitly -- that default was left alone
deliberately rather than widened to auto-discover the in-repo copy, so the documented
"431 passed, 11 skipped" standalone count stays true for anyone who clones this repo.

The skipped tests are the differential check against production's real scorer, and the
cross-check of our pins against production's `requirements.txt`. **The version pin gate
itself always runs**, in both modes: it depends on nothing outside this repository.

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

git grep -ohE "\b0[689][0-9]{8}\b" | sort -u        # must be 08100000xx only
```

Three independent controls: directory-level ignores, a runtime refusal of any data
directory inside a git worktree, and a writer that raises on customer columns.

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
    --model qwen/qwen3.6-27b --provider Alibaba \
    --items RET-01,RET-11,RET-16 --repeats 3
```

Read the line `prompt_tokens fingerprint  N/N items returned exactly one value`. It
must be `3/3`.

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
| `20260804-222943Z-incumbent` | gemini-2.5-flash | 42 | 18 | 0.429 |
| `20260804-224050Z-candidate` | qwen3.6-27b | 30 | 19 | 0.633 |

`save cost` alone is 13 of Qwen's 30. Those two rows are asserted as literals: if either
moves, the helper changed and the baseline did not.

**`v9_16_e1` depends on `decoding.decoding_schema` deviation 2.** The committed port
`schemas/retention.json` does not allow `""` in a `reason` enum, although every
`reason.description` in it says "Use empty string if not applicable" -- a
self-contradiction that predates this variant. `decoding_schema` adds `""` to the enums
it sends, which is the only reason a model can copy the blanked example at all. Without
that deviation every copying model would land in `schema_violation` and an e1 run would
be measuring the grammar rather than the example. Both facts are pinned:
`test_e1_example_is_legal_under_the_grammar_that_is_actually_sent` and
`test_e1_example_fails_the_committed_port_schema_at_exactly_the_blanked_slots`.

## Continuous Integration

`.github/workflows/ci.yml` runs the standalone suite on push and pull request to
`main`. It cannot run the differential test, because True's production source is not
available to CI, so **CI proves less than a local run with `TRUE_SOURCE_ROOT` set**.
Treat a green CI badge as "the harness is internally consistent", not as "the harness
agrees with production".

## What none of this proves

**No number this harness produces is a migration verdict yet.** Every report prints
`RECONCILED: NO` until a run has been checked against the app's own live Gemini
fact-check report. That needs real labelled data and real model runs, neither of which
this repository has. A fully green suite means the harness computes what it claims to
compute, not that any model is good enough to deploy.
