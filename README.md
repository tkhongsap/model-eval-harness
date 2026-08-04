# eval-harness

Scores model outputs against human labels for the Gemini to self-hosted migration.
**Retention app only** for now.

It does not run models. Anan's team runs both arms; this scores what they produce.

## What it proves today, with zero real data

Correctness is demonstrated three independent ways, and all three had to agree:

1. **Hand-computed expectations.** `tests/fixtures/retention_expected.csv` holds exact
   integer TP/FP/FN/TN for every class in all three dimensions, derived on paper in
   `WORKED-COMPUTATION.md` **before this code existed**.
2. **A clean-room implementation.** `src/evalharness/metrics.py` is pure Python, no
   pandas, written from the production semantics rather than copied from them.
3. **A differential test against True's real production scorer.**
   `tests/test_differential.py` imports the actual `FactCheckerModule` and asserts
   agreement class by class, integer by integer.

Two implementations sharing nothing, plus a third derivation on paper, converging on
the same numbers. Any one of them could be wrong; all three being wrong identically
is not plausible.

```
79 passed          # .venv (pandas 2.3.3, production's pin)
72 passed, 7 skipped   # system pandas 3.x, differential correctly skipped
```

## What it cannot prove

**No number from this harness is a migration verdict yet.** Every report prints
`RECONCILED: NO` until a run has been checked against the app's own live Gemini
fact-check report. That gate (framework section 5.3) needs real data and real model
runs, both out of scope here. The stamp exists so the harness cannot launder its own
provenance under deadline pressure.

The workbook adapter is also unproven: the real ground truth reconstructs column
names from a two-row header block (`fact_checker.py:517-525`) that nobody has seen.
`load_workbook()` therefore **raises rather than guessing** (see `docs/data-contract.md`,
Ask 1: the two header rows settle it, and contain no customer record).

## Run it

```bash
python -m venv .venv
.venv/Scripts/python -m pip install -r requirements.txt
.venv/Scripts/python -m pytest tests/ -q
```

The pins are not cosmetic. Under pandas 3.x a string column's dtype is `str`, so
`pre_process`'s `dtype == 'object'` guard (`:734, :738`) is False, normalisation never
fires, and `call_result` accuracy collapses from 0.75 to 0.25 with all weights zeroed.
The differential test **skips with that reason** rather than comparing against a
broken production.

## Handling data

`EVAL_HARNESS_DATA_DIR` has no default. The harness refuses to read a data directory
that resolves inside a git worktree, and `data/`, `out/` and `build/` are ignored
wholesale (directories, not extensions, because a pandas pipeline emits `.parquet`,
`.jsonl` and `.ipynb` too, and notebook cell outputs embed real rows).

`phone_number` is **hashed, not dropped**: it is part of the merge key (`:1075`) and
the product groupby key (`:1080`), so dropping it would break the join. `EVAL_HARNESS_KEY_HMAC`
holds the key, which never lives in this repository. `assert_shareable()` raises on
any customer identifier reaching a shareable artifact.

## Three dimensions, three denominators

The single most important thing to know before changing anything:

| dimension | frame | rows in the fixture | why |
|---|---|---|---|
| `call_result` | after `dropna(subset=['call_result_gt'])` (`:756`) | 10 | orphan prediction excluded |
| `reason` | no dropna at all (`:849-962`) | 11 | orphan prediction retained |
| `product` | call-grain groupby, NaN phone keys dropped (`:1080`) | 9 | a null phone number drops the call entirely |

A harness that computes one denominator and reuses it produces three wrong numbers.

## Two deliberate deviations from production

Both are in the goal contract's closed list. Neither may be "fixed" into agreement.

**Parse failures are scored, not dropped.** In production a failed item becomes a
phantom row, `dropna` (`:756`) removes it from `call_result`, and `reason_calculation`
credits it true negatives (`:899-902`). Measured consequence: an all-empty prediction
set scores accuracy 0.8246 with recall 0.0000 on this fixture. The absolute figure is
distribution-dependent and rises with more single-label items; the point is that
**accuracy is blind to total failure**, which is why recall and F1 carry the gate and
why `check_coverage()` refuses to compare arms with different coverage.

**String normalisation is unconditional.** Production gates it on a dtype check that
silently stops firing on a pandas upgrade.

## Layout

```
src/evalharness/
  labelspaces.py   class lists, verbatim from production, with line citations
  records.py       the normalized record and every normalisation rule
  metrics.py       the three scorers, three denominators
  compare.py       paired comparison, 2x2 disagreement table, coverage refusal
  keys.py          HMAC item keys and the PII guard
  manifest.py      blocking vs recorded fields, RECONCILED stamp
  adapters/        per-app parsing. THE ONLY THING THAT CHANGES when real data lands.
tests/
  fixtures/        CASES.md, WORKED-COMPUTATION.md, the arms, hand-computed expectations
  production_ref.py  test-only scaffolding to import the real production scorer
docs/data-contract.md   what to ask Anan's team for, and why
```

## Extracting this to its own repository

Nothing here imports from life-os, and no customer data has ever been committed, so
the history is clean by construction. `TRUE_SOURCE_ROOT` points the differential test
at the production tree, so it does not depend on this repository's layout.
