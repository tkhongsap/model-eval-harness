# Testing & Verification

## Quick Start

```bash
python -m venv .venv
.venv/Scripts/python -m pip install -r requirements.txt   # Windows
# .venv/bin/python -m pip install -r requirements.txt     # macOS/Linux
.venv/Scripts/python -m pytest tests/ -q
```

Expected: **82 passed, 11 skipped**.

The 11 skips are correct and expected standalone. See "Two modes" below.

## Two modes, two different counts

This repository proves more when True's production source tree is reachable. Report
whichever number you actually ran, and say which mode.

| Mode | Command | Expected |
|---|---|---|
| **Standalone** | `pytest tests/ -q` | **82 passed, 11 skipped** |
| **With production source** | `TRUE_SOURCE_ROOT=<path> pytest tests/ -q` | **93 passed, 0 skipped** |

```bash
# Windows, pointing at the vendored archive in life-os
set TRUE_SOURCE_ROOT=C:\Users\te90056471\my-github\life-os\work-work\projects\2026-07-local-llm-platform\source-code-review\sentiment-batch-retention-main
.venv\Scripts\python -m pytest tests/ -q
```

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
