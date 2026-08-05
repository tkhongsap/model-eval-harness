# Claude Code - model-eval-harness

**Read [AGENTS.md](./AGENTS.md) first.** It holds the durable, tool-agnostic project
context: mission, architecture, key files, conventions, and the open items. This file
holds only what is specific to working here with Claude Code, and does not repeat it.

Cross-project standards live in canon. Link to them, never copy them in.

## Before you change anything

Three files are load-bearing for reasons that are not obvious from reading them:

| File | Why it matters |
|---|---|
| `tests/fixtures/retention_expected.csv` | Hand-computed **before** the code existed. It is the independent check. |
| `src/evalharness/keys.py` + `paths.py` | The two runtime controls keeping customer identifiers out of git. |
| `requirements.txt` | The pins change what the production scorer computes, not just how it installs. |

## The rule that matters most

**When a test fails, ask whether the fixture is wrong before assuming the code is.**

`retention_expected.csv` holds 23 hand-computed integers derived on paper from
production's semantics, in `tests/fixtures/WORKED-COMPUTATION.md`, written before any
metric code. Three independent derivations agree today: the arithmetic, the clean-room
implementation, and True's real production scorer.

Editing an expectation so a test goes green collapses three independent checks into
one, and the one that survives is the code checking itself. If the hand computation
and the code genuinely disagree, one of them has found something: work out which, and
write the answer down in `WORKED-COMPUTATION.md`.

## Things that look like bugs and are not

- **`load_workbook()` raises `NotImplementedError`.** Deliberate. See AGENTS.md.
- **Three dimensions use three different denominators** (10, 11, 9 on the fixture).
  This mirrors production exactly. "Simplifying" it to one produces three wrong numbers.
- **The product dimension silently drops calls with a null phone number.** A reproduced
  production defect, documented in `metrics.py`. Reproduced so numbers stay comparable,
  counted in coverage so the loss is visible.
- **Parse failures are scored rather than dropped.** A deliberate deviation from
  production, one of exactly two, both listed in the goal contract's closed list.
- **The differential test skips without `TRUE_SOURCE_ROOT`.** Correct: it refuses to
  compare against a production whose normalisation has silently stopped firing.

## Two packages, not one

`src/evalharness/` scores. `src/evalgen/` is the generation half that produces what it
scores: the OpenRouter call loop, the single decision point for `parse_ok`, the flatten
that changes the grain to one row per product, the decoding deviations, and the report.
Entry point is `scripts/evalgen.py`; each module is listed with the reason it matters in
[AGENTS.md](./AGENTS.md), "Key Files".

The boundary between the two is what to be careful with. `evalharness` imports no model
client, and `tests/test_boundary.py` fails if that changes, so an `import openai` added
to the scoring path "just for a moment" breaks a build rather than a rule.

## Running things

```bash
.venv/Scripts/python -m pytest tests/ -q          # 451 passed, 11 skipped
```

Full detail, including how to make the differential test actually run, is in
[TESTING.md](./TESTING.md).

## Working preferences

- Plan before executing; discuss approach first.
- Conventional Commits, matching the existing history.
- Commit or push **only when explicitly asked**.
- Keep customer identifiers out of examples, commit messages and test fixtures. The
  synthetic phone range is `08100000xx` and it stays that way.

## Build and Verification Contract

For any substantial change:

1. State the outcome, authoritative source, scope boundary and observable done
   criteria before editing.
2. Run the suite in **both** modes, standalone and with `TRUE_SOURCE_ROOT` set. A
   green standalone run does not prove the differential still agrees.
3. If a deviation from production is added or changed, it goes in the closed list
   with its reason. Nothing gets added to that list by argument mid-change.
4. Do not weaken a gate to make a run pass. The pins, the coverage refusal, the
   manifest block and the `RECONCILED` stamp all exist because something specific
   went wrong without them.
