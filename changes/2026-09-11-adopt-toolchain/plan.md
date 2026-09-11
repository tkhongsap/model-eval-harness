# Plan: Adopt the Python toolchain baseline

- **Status:** Approved (self-approved; see below)
- **Author:** playbook adoption trial, fresh agent session, 2026-09-11
- **Date:** 2026-09-11
- **Spec:** [`spec.md`](spec.md)
- **Approved by:** the author, 2026-09-11, before implementation. No second person
  was available in this trial; `templates/plan.md` requires an approver and gives no
  procedure for a solo session, so this is recorded rather than faked. Owner review
  happens at the pull request.

## Files that change

| Path | Change | Why |
|---|---|---|
| `changes/2026-09-11-adopt-toolchain/intent.md`, `spec.md`, `plan.md` | Add | Artifact chain, committed before code |
| `pyproject.toml` | Add | `[tool.ruff]`, `[tool.ruff.lint]`, `[tool.ruff.lint.per-file-ignores]`, `[tool.pytest.ini_options]` only |
| `mypy.ini` | Add | Lenient config with 61-module burn-down |
| `requirements-dev.txt` | Add | Pinned `ruff`, `mypy`, `pre-commit`; the pip equivalent of the exemplar's `[dependency-groups] dev` |
| `src/evalgen/{judge,severity,stability,testsets}.py`, `src/evalharness/metrics.py` | Modify (mechanical) | `ruff check --fix`: import order, unused imports |
| `scripts/*.py` (16 files), `tests/*.py` (13 files), `asr-eval/**/*.py` (10 files) | Modify (mechanical) | same |
| `.pre-commit-config.yaml` | Add | Hooks mirroring CI, with excludes for pinned/generated paths |
| `.github/workflows/ci.yml` | Modify | Add `permissions:`, `lint` and `typecheck` jobs; correct header items 3–4; keep every existing step |
| `AGENTS.md` | Modify | "Commands" section, burn-down pointer, stated gaps, Open-items correction, Conventions line |
| `TESTING.md` | Modify | Required checks include the new commands |
| `CHANGELOG.md` | Modify | Unreleased entry |
| `changes/2026-09-11-adopt-toolchain/review.md` | Add | Author's self-review |

Not on this list and must stay off it: `requirements.txt`, anything under
`asr-eval/` that is not a `.py`, `production-reference/`, `tests/fixtures/`,
`experiments/`, `src/evalgen/prompts/`, `src/evalgen/schemas/`, `docs/reports/`,
`configs/`, any test's assertions.

## Work order

1. **Commit the chain** (`intent.md`, `spec.md`, `plan.md`). Proof: they exist in
   history before any config commit.
2. **Config only:** `pyproject.toml`, `mypy.ini`, `requirements-dev.txt`. Proof:
   `PYTHONPATH=src pytest tests/ -q -rs` unchanged at 1071/50; `pytest` header shows
   `configfile: pyproject.toml`; `mypy .` exits 0; `ruff check .` reports only the
   auto-fixable findings the next step removes. Commit as `chore(toolchain): …`.
   This commit is intentionally a scaffold: lint is not green until step 3.
3. **Mechanical fixes:** `ruff check --fix .`; inspect the diff for anything that is
   not an import move or a removed unused import; run the suite in both modes. Proof:
   `ruff check .` → `All checks passed!`; both suite counts unchanged; `experiment-check`
   exits 0. Commit alone as `style: apply ruff mechanical fixes`.
4. **pre-commit:** add `.pre-commit-config.yaml`; `pre-commit install`;
   `pre-commit run --all-files`. Proof: every hook passes; `git status` shows no
   modification under the excluded paths. If a hygiene hook changes anything else,
   inspect and commit it separately as `style:`. Commit the config as `chore(toolchain): …`.
5. **CI:** add the jobs. Proof: `python -c "import yaml; yaml.safe_load(open('.github/workflows/ci.yml'))"`
   parses; `git diff` of the `test` job's `steps:` block is empty; new jobs pinned by SHA
   with version comments. Commit as `ci: …`.
6. **Docs:** `AGENTS.md`, `TESTING.md`, `CHANGELOG.md`. Proof: the commands in the docs
   are the commands run in steps 2–4, pasted, not retyped. Commit as `docs: …`.
7. **Review:** fill `review.md`; commit; push; `gh pr create --draft`.

Each step reverts on its own: step 3's commit can be reverted without touching step 2;
step 4 and 5 are independent files.

## Risks

| Risk | Likelihood | If it happens | Mitigation |
|---|---|---|---|
| A hygiene hook rewrites a sha256-pinned prompt or fixture | High without excludes (two prompt files carry trailing whitespace today) | `experiment-check` fails; prompt tests fail; a plan's asset hash no longer matches | Per-hook `exclude` regex; step-4 proof checks `git status` on those paths |
| `ruff --fix` removes an import that is used only for its side effect | Low | A test or script breaks at import time | Full suite both modes after the fix; read the diff for `F401` removals in `__init__.py` or launcher files |
| Adding `pyproject.toml` changes pytest's rootdir or collection | Low (rootdir is already the repo root) | Different node IDs or collection | Compare counts; `scripts/verify.py` reads exit codes not IDs |
| mypy on `.` picks up `production-reference/` or `.venv/` | Medium | Thousands of errors or a crash | Explicit `exclude` regexes in `mypy.ini`; step-2 proof shows the checked-file count (~132) |
| Dev-tool install moves a pinned package | Low | The scorer computes something different | `tests/test_requirements.py` is in the suite; `pip freeze` after install |
| `language: system` mypy hook runs the wrong interpreter | Medium | Hook passes or fails for the wrong reason | Document "activate `.venv` first"; `verbose: true` prints which mypy ran |
| Import re-sort shifts prose line citations | Certain for `cli.py`, `experiments.py` | 21 citations go stale | Per-file `I001` exemption with reason; follow-up recorded |

## Proof

```bash
# step 2 and 3
.venv/bin/ruff check .
.venv/bin/python -m mypy .
PYTHONPATH=src .venv/bin/python -m pytest tests/ -q -rs
PYTHONPATH=src TRUE_SOURCE_ROOT=production-reference/sentiment-batch-retention-main .venv/bin/python -m pytest tests/ -q -rs
PYTHONPATH=src .venv/bin/python scripts/evalgen.py experiment-check --plan experiments/retention-e7.plan.json
# step 4
.venv/bin/pre-commit run --all-files
git status --short -- src/evalgen/prompts src/evalgen/schemas tests/fixtures experiments asr-eval docs/reports configs production-reference
# step 5
.venv/bin/python -c "import yaml,sys; yaml.safe_load(open('.github/workflows/ci.yml')); print('yaml ok')"
git diff main -- .github/workflows/ci.yml
git diff --check
```

Passing output: `All checks passed!`; `Success: no issues found in <N> source files`;
`1071 passed, 50 skipped` and `1082 passed, 39 skipped`; `experiment-check` exit 0;
every pre-commit hook `Passed`; the `git status` line empty; `yaml ok`; `git diff --check`
silent.

## Out of scope

`ruff format`; fixing any burn-down item; coverage; Trivy; SHA-pinning the existing
`test` job; `uv`; type-checking `asr-eval/`; any change to test expectations; the
DEVLOG (an experiment log, not a tooling log).

## Deviations

Recorded as they happened. Plan adherence: the file list held except for one file
(item 5); the work order held; two proofs needed hand intervention (items 3, 4).

1. **The "playbook version" is not a commit.** Step 1 assumed `28d946c` was the version
   adopted. `git status` in the playbook showed `toolchains/` and the four chain
   templates as untracked files. The intent was corrected before the chain commit; the
   version adopted is a working tree, and no commit can be cited for it.
2. **`python3 -m venv .venv` as instructed used Python 3.9.6.** The project states 3.12
   and its pins (numpy 2.3.4) do not install on 3.9. Used `uv`'s 3.12.14
   (`~/.local/bin/python3.12 -m venv .venv`). Neither the playbook nor `TESTING.md`
   says how to obtain the interpreter; `AGENTS.md` "Commands" now does.
3. **`ruff check --fix` output was not commit-ready.** It split one import line in
   `scripts/provider_probe.py` and dropped the `# noqa: E402` from the new half, so the
   fixed tree had one *new* finding; and in `tests/test_outcomes.py` it merged a
   deliberately separate private import into the block, relocating its explanatory
   comment. Both hand-adjusted in the mechanical commit and stated in its message.
   The plan treated the tool's output as mechanical; it was mechanical plus two edits.
4. **One violation class the spec did not list: `E402` (7) in
   `tests/test_enterprise_experiments.py`**, the `sys.path.insert` before imports
   pattern, which the file already marks with a `# noqa: E402` on its first import.
   Added a per-file ignore with reason before the config commit.
5. **`pre-commit run --all-files` changed a file not on the plan's list:**
   `Token_Factory_API_Guide.html` lacked a final newline. Not pinned, not generated;
   committed alone as `style:` (`f407bf9`) so the hook-config commit carries no content.
   Every excluded path was verified untouched.
6. **Hook id `ruff` is a legacy alias in ruff-pre-commit v0.16.7** (the run printed
   "ruff (legacy alias)"). Used `ruff-check`. The playbook block is verbatim from an
   older rev and does not say so.
7. **The `lint` CI job installs `requirements-dev.txt` only**, not the production pins
   as the plan's "same install" implied. ruff reads no project import. `typecheck`
   installs both and re-proves the pins.
8. **The mypy hook is skipped on commits that touch no `.py` file** (`types: [python]`
   in the exemplar block), observed on the hook-config, CI and docs commits: "mypy
   ... (no files to check) Skipped". A `mypy.ini`-only edit is therefore not
   type-checked locally; CI still runs it. Kept the exemplar shape; noting it.
9. **The second test mode was available.** The constraints anticipated
   `TRUE_SOURCE_ROOT` being unavailable; the tracked copy at
   `production-reference/sentiment-batch-retention-main` works, as `TESTING.md` says.
   Both modes were run before and after: 1071/50 and 1082/39, unchanged.
10. **Approval.** No approver existed; the plan is self-approved and says so. The
    playbook's template has no procedure for a single-person or agent-only session.
