# Spec: Adopt the Python toolchain baseline

- **Status:** Accepted
- **Owner:** author of this change (trial); merge authority remains @tkhongsap
- **Date:** 2026-09-11
- **Risk tier:** R1. The change alters a control surface (CI, pre-commit) and applies
  automated edits to source files that feed a decision document, so it is above R0;
  it reads no customer data, calls no model, and every step is reversible by deleting
  the added files, so it is not R2.
- **Intent:** [`intent.md`](intent.md)

## What it must do

1. `ruff check .` from the repository root exits 0 on the branch tip. Rules `E`, `F`,
   `I` at line-length 100. Every remaining violation class is listed in
   `[tool.ruff.lint] ignore` or `[tool.ruff.lint.per-file-ignores]` with a written reason.
2. `mypy .` from the repository root exits 0 on the branch tip, in the lenient shape
   of `toolchains/python.md` (`check_untyped_defs`, `ignore_missing_imports`,
   `explicit_package_bases`), with `src/` on the module path so the two packages are
   checked as real modules, and every module that fails today listed under
   `ignore_errors = True` as the burn-down.
3. `PYTHONPATH=src pytest tests/ -q -rs` passes with the same pass and skip counts as
   the baseline (1071 passed, 50 skipped standalone; 1082 passed, 39 skipped with
   `TRUE_SOURCE_ROOT=production-reference/sentiment-batch-retention-main`).
4. `pre-commit run --all-files` exits 0 and `git status` afterwards shows no change to
   any file under the hash-pinned or generated paths listed in the intent.
5. Every step in `.github/workflows/ci.yml`'s existing `test` job is preserved
   verbatim. Two jobs are added, `lint` and `typecheck`, that run exactly the commands in
   items 1–2 from the same `pip`-built environment.
6. `AGENTS.md` gains a "Commands" section and its "Open items" entry about the missing
   linter is corrected; `TESTING.md`'s required checks include the new commands. Both
   record what is deliberately not gated.

**Non-goals** (a reader could reasonably assume these are in scope):

- Applying `ruff format`. 137 files would change; the repository's prose cites
  `file:line` into source in at least 21 places for `cli.py` and `experiments.py`
  alone, and reformatting invalidates them. Deferred to its own change, recorded as a
  stated gap per `toolchains/ci.md` line 8 ("a missing job is a stated gap, not a
  silent one").
- Fixing type errors or the non-mechanical lint findings. This change installs the
  ratchet; it does not turn it.
- A coverage floor. `toolchains/python.md` says to set it at the current number; there
  is no current number, and `pytest-cov` adds a dependency to the pinned environment.
  Recorded as a follow-up.
- Trivy security scan, action-SHA pinning of the existing `test` job, `uv`. `python.md`
  says `uv` "is the choice for new repositories, not a migration order".
- Type-checking `asr-eval/` from the root environment. Its dependencies live in
  `.venv-asr`; checking it from the root venv would either fail or silently type
  everything as `Any`.

## Contracts

| Contract | This change |
|---|---|
| Capability | Three deterministic checks run locally and in CI with one shared configuration. Limits: `ruff format` is not enforced; mypy is lenient with 61 exempt modules; `asr-eval/` is linted but not type-checked. |
| Data | No data read. Tools read source text only. Hash-pinned assets are excluded from every hook that writes. |
| Tool | `ruff`, `mypy`, `pre-commit` and its two upstream hook repositories (`pre-commit/pre-commit-hooks`, `astral-sh/ruff-pre-commit`), pinned. Side-effect class: writes to working-tree source files under `--fix` (reviewed as a diff), nothing else. |
| Evaluation | The existing suite in both modes is the gate; the three new commands must exit 0; the pin gate `tests/test_requirements.py` proves the dev-tool install moved no pin. |
| Operations | Rollback: delete `pyproject.toml`, `mypy.ini`, `requirements-dev.txt`, `.pre-commit-config.yaml`, remove the two CI jobs, `pre-commit uninstall`. The mechanical-fix commit can be reverted independently. |

## Design

```
developer commit ──► pre-commit ──► hygiene hooks (excluded: pinned/generated paths)
                                ──► ruff --fix (E,F,I @100, pyproject.toml)
                                ──► mypy .  (language: system, venv python, mypy.ini)
pull request     ──► CI test job (unchanged)
                 ──► CI lint job:      pip install -r requirements.txt -r requirements-dev.txt; ruff check .
                 ──► CI typecheck job: same install; python -m mypy .
```

Configuration lives in exactly two files so local and CI cannot drift:
`pyproject.toml` (`[tool.ruff]`, `[tool.ruff.lint]`, `[tool.ruff.lint.per-file-ignores]`,
`[tool.pytest.ini_options]` — nothing else) and `mypy.ini`. Tool versions live in
`requirements-dev.txt` and are mirrored by the `rev:` lines in `.pre-commit-config.yaml`.

**Scope boundaries:**

- `production-reference/` is excluded from ruff and mypy: it is True's code, kept
  verbatim so citations resolve; linting it would either change it or drown the signal
  (453 files).
- `asr-eval/` is linted by ruff (static, needs no dependencies) and excluded from mypy
  (needs `.venv-asr`).
- `scripts/openrouter-smoketest/` is excluded from mypy: the directory name is not a
  valid module path and the script is documented as exploratory.
- `.venv/`, `.venv-asr/` excluded from mypy explicitly.

**Line length 100, not the exemplar's 88.** `toolchains/python.md` states canon's
baseline is 100 and that new repositories use it; the exemplar's 88 is its own history.
This codebase measures 108 lines over 100 versus 4,362 over 88, so it was written to 100.

**Import-sort exemptions for two files.** `ruff check --fix` re-sorts imports in
`src/evalgen/cli.py` (+4 net lines) and `src/evalgen/experiments.py` (+2). Sixteen and
five `file:line` citations in `ci.yml`, `DEVLOG.md`, `EXPERIMENTS.md`, `RUNS.md`,
`TESTING.md` and `docs/` point past those import blocks. Those two files get
`per-file-ignores = ["I001"]` with the reason, and the citations stay true. Fixing the
imports and the citations together is a follow-up.

**mypy burn-down is per-module, not per-directory.** Listing 61 modules is long but it
is the shape the playbook prescribes and the only shape that blocks a new error in a
currently-clean module.

**pre-commit's mypy hook uses `language: system`** with `python -m mypy .`, as the
playbook says, which means hooks must be run from the activated `.venv`. Recorded in
`AGENTS.md` "Commands".

## Flagged concerns

| Concern | Raised by | Severity | Resolution | Accepted by |
|---|---|---|---|---|
| The exemplar's `trailing-whitespace` and `end-of-file-fixer` hooks, copied verbatim, would rewrite `src/evalgen/prompts/retention_wrapper.txt` and `retention_v9_16_body.txt`, whose sha256 is pinned in every experiment plan, breaking `experiment-check` and the prompt tests. | author, from a dry survey of trailing whitespace | Blocking if unhandled | Resolved: per-hook `exclude` regex covering every hash-pinned and generated path | author |
| `F811` in `tests/test_compare.py` (9 findings) is pytest fixtures imported from a helper module and re-bound as parameter names — a false positive, not a shadowed test. | author | Minor | Per-file ignore with reason; not a code change | author |
| `ruff check --fix` changes 47 files; a bad fix would be invisible in a diff that size. | author | Material | Resolved: run the full suite in both modes on the fixed tree before committing; review the diff for anything other than import moves and removed unused imports | author |
| Installing `mypy`/`pre-commit` into the pinned venv could move a pin. | author | Material | Resolved: `tests/test_requirements.py` re-run after install; `pip freeze` shows pandas 2.3.3 / numpy 2.3.4 / openpyxl 3.1.5 unchanged | author |
| The `ruff format` gate required by `ci.md` and `pre-commit.md` is not added. | author | Material | Deferred with a filed follow-up in `AGENTS.md`; the playbook's migration path never mentions formatting | author |
| `pre-commit`'s `check-added-large-files` (500 KB) will block any future audio pack commit under `asr-eval/`. | author | Note | Accepted: audio packs are gitignored by policy (`asr-eval-v2/`, `-v3/`); the existing 20 committed wavs are not "added" so they pass | author |
| No second reviewer exists in this trial. | author | Material | Deferred to the PR; `review.md` records the author's self-review only | pending owner |

## Alternatives considered

- **Global `ignore` for `F811`, `F841`, `E741`** (the exemplar's shape). Rejected for the
  two `F` rules: a global ignore stops catching the real-bug form of each in new code.
  Per-file ignores keep the rule live everywhere else. `E741` and `E501` are style and
  are ignored globally with a burn-down note.
- **`[tool.mypy]` in `pyproject.toml`** instead of `mypy.ini`. Both are `[tool.*]`-only
  compatible; the playbook prescribes `mypy.ini` and it keeps the burn-down list out of
  the file that pytest reads. Followed the playbook.
- **`pythonpath = ["src"]` in `[tool.pytest.ini_options]`** so `PYTHONPATH=src` becomes
  unnecessary. Rejected: it changes how imports resolve, which `python.md` step 1 calls
  a separate decision, and `TESTING.md`/`ci.yml` document the explicit variable.
- **Exclude `asr-eval/` from ruff too.** Rejected: ruff needs no dependencies, the
  fixes there are the same import-order class, and `ci.yml` already runs one of its
  tests from the root job.
- **Apply `ruff format .` now.** Rejected for this slice; see non-goals.

## How it will be verified

Deterministic, all offline, all in the pinned venv:

```bash
.venv/bin/ruff check .                      # expect: All checks passed!
.venv/bin/python -m mypy .                  # expect: Success: no issues found in N source files
PYTHONPATH=src .venv/bin/python -m pytest tests/ -q -rs
                                            # expect: 1071 passed, 50 skipped
PYTHONPATH=src TRUE_SOURCE_ROOT=production-reference/sentiment-batch-retention-main \
  .venv/bin/python -m pytest tests/ -q -rs  # expect: 1082 passed, 39 skipped
PYTHONPATH=src .venv/bin/python scripts/evalgen.py experiment-check --plan experiments/retention-e7.plan.json
                                            # expect: exit 0 (prompt hashes intact)
.venv/bin/pre-commit run --all-files        # expect: every hook Passed; git status clean of pinned paths
git diff --check                            # expect: nothing
```

Thresholds are the baseline counts; any change in pass/skip count is a failure of
this change, not a fact to update.

## Open questions carried forward

- Plan approval by a second person: blocks merge, not build.
- Coverage floor: blocks nothing now; follow-up owner is the repository owner.
- `ruff format` adoption and citation-safe import sorting of `cli.py`/`experiments.py`:
  follow-up.
