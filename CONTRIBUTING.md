# Contributing

Read [AGENTS.md](./AGENTS.md) for project context and [TESTING.md](./TESTING.md) for
how to run things. Read [DEVLOG.md](./DEVLOG.md) for the active task before choosing
work. Cross-project standards live at `/home/tkhongsap/my-github/s42/canon` and are
linked, not repeated.

## The one rule that is not negotiable

**Never edit an expectation so a test passes.**

`tests/fixtures/retention_expected.csv` holds 23 integers derived on paper, from
production's semantics, in `tests/fixtures/WORKED-COMPUTATION.md`, and committed
before any metric code existed. Three independent derivations agree today: the hand
arithmetic, the clean-room implementation, and True's real production scorer.

Changing an expectation to match output collapses three checks into one, and the one
left standing is the code checking itself. When a test fails:

1. Re-derive the number by hand from `WORKED-COMPUTATION.md`.
2. If the hand computation was wrong, fix it **and** say what was misunderstood.
3. If the code is wrong, fix the code.
4. If production genuinely disagrees with both, you have found something. Write it
   down in `WORKED-COMPUTATION.md` before changing anything.

## Before you open a PR

```bash
PYTHONPATH=src python -m pytest tests/ -q -rs
PYTHONPATH=src TRUE_SOURCE_ROOT=<path> python -m pytest tests/ -q -rs
PYTHONPATH=src python scripts/evalgen.py experiment-check \
  --plan experiments/retention-e7.plan.json
git diff --check
```

**Both modes.** A green standalone run does not prove the differential still agrees,
because standalone skips it. The 2026-08-12 standalone reference is 840 passed and
12 skipped (851 / 1 with production source); report your observed count and each skip
reason instead of editing a test to match that number. If production source is unavailable, say so explicitly in the PR.

Use `.github/PULL_REQUEST_TEMPLATE.md`. Identify the decision grain, privacy class,
runtime/network behavior, tests actually run and whether `RECONCILED` remains `NO`.
Raw model outputs, transcripts, credentials and private judge records never belong in Git.

## Commits

Conventional Commits, matching the existing history: `feat:`, `fix:`, `docs:`,
`test:`, `chore:`.

Write a body, not just a subject. The history here is meant to explain *why* a
decision was made, because most of the non-obvious code exists to avoid a specific
failure that is not visible from the code alone.

## High-risk changes

Flag these explicitly in the PR description:

| Area | Why |
|---|---|
| `keys.py`, `paths.py`, `.gitignore` | The three controls keeping customer identifiers out of git. |
| Any denominator in `metrics.py` | Three dimensions use three different row sets on purpose. Unifying them produces three wrong numbers. |
| `requirements.txt` | The pins change what production computes, not just how it installs. |
| The `RECONCILED` stamp | It exists so the harness cannot launder its own provenance. |
| The deviation list | Deviations from production are a **closed list**. Nothing joins it mid-change by argument. |

## Adding a new app adapter

The metric, comparison and manifest layers are final. A new app should need:

1. A `LabelSpace` in `labelspaces.py` (data, not a code fork).
2. An adapter in `adapters/`.
3. A fixture pack with **hand-computed** expectations, written before the adapter.
4. A differential test, if that app's scorer is reachable as a pure function.

If a new app needs a change above the adapter layer, the abstraction has leaked and
that is worth discussing before working around it.
