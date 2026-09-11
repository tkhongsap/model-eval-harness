# Intent: Adopt the Python toolchain baseline

- **Status:** Accepted
- **Originator:** engineering playbook adoption trial (new team member, fresh session)
- **Date:** 2026-09-11
- **Risk tier:** R1 first guess — the change touches CI (a control) and applies
  mechanical edits to source, but changes no runtime behaviour and reads no data.
  Refined in the spec.

Playbook adopted: `tkhongsap-ai-engineering-playbook`, branch
`docs/ai-native-sdlc-alignment`, HEAD `28d946c` — **but not that commit.** At the time
of reading, the working tree carried 35 uncommitted paths, and the documents this
change depends on were among the untracked ones: the whole `toolchains/` directory and
`templates/intent.md`, `spec.md`, `plan.md`, `review.md` exist only in the working
tree. `adopt-in-a-project.md` lists "a reviewed playbook version or commit selected"
as a prerequisite; no such commit exists for this material, so the version adopted is
"working tree of `docs/ai-native-sdlc-alignment` as read on 2026-09-11".
Procedure followed: `playbooks/adopt-in-a-project.md` steps 1–7.

## Problem

`AGENTS.md` "Open items" records it plainly: *"No linter, type checker or
`pyproject.toml`. Canon's Python CI template expects ruff, mypy and a `pyproject.toml`
… This repository has none. CI runs the test suite only."* `.github/workflows/ci.yml`
lists the same two gaps as deviations 3 and 4 of its header comment.

What that costs, measured on `main` at `982237f` on 2026-09-11 in a venv built to
`requirements.txt` on Python 3.12.14:

| Check | Observed |
|---|---|
| `ruff check` (E, F, I at line-length 100, `production-reference/` excluded) | 4,454 findings at line-length 88; 108 `E501` + 78 others at 100. Of the others: 35 unsorted imports, 16 unused imports, 12 ambiguous names, 9 redefinitions, 7 placeholder-less f-strings, 7 late imports, 6 unused locals |
| `ruff format --check` | 137 of 226 files would be reformatted |
| `mypy` (lenient shape from `toolchains/python.md`, `src/` on the module path) | 295 errors in 61 files as first measured; 276 in 56 on re-measurement with `--no-incremental` (see review R-101) |
| One of them is already a runtime warning | `src/evalgen/artifacts.py:128: SyntaxWarning: invalid escape sequence '\.'` printed on every test run |

None of these blocks anything today, because nothing runs them. A new `import openai`
in the scoring path is caught by `tests/test_boundary.py`; a new unused import, a
shadowed test function or a wrong type is caught by nobody.

### Inventory (playbook step 1)

- **Languages:** Python 3.12 (stated); no `python3.12` on the default `PATH` of this
  machine (`/usr/bin/python3` is 3.9.6) — one is available through `uv`.
- **Packages:** `src/evalharness/` (scoring, stdlib + pinned pandas), `src/evalgen/`
  (generation, imports `openai` in exactly one file). Imports resolve via
  `PYTHONPATH=src`; there is deliberately no installable package.
- **Other Python:** `scripts/` (48 files, launchers and report generators; `scripts/evalgen.py`
  shares a name with the package), `tests/` (49 files), `asr-eval/` (23 files, **own pin set
  in `requirements-asr.txt` that must never merge with the root pins**, own venv
  `.venv-asr`), `production-reference/` (**453 vendored files of True's production source**,
  tracked on purpose, not ours to lint).
- **Environments:** root venv to `requirements.txt` (pandas 2.3.3, numpy 2.3.4,
  openpyxl 3.1.5, pytest 9.1.1 — the pins are load-bearing); `.venv-asr`; generation-only
  `src/evalgen/requirements.txt`.
- **CI:** one job, `test`: pin check → testset pack checks → run-index check → experiment
  plan check → `pytest tests/ -q -rs` → one asr-eval spec test. Actions pinned by floating
  tag (`checkout@v4`, `setup-python@v5`), no `permissions:` block.
- **Tests:** two modes. Standalone `PYTHONPATH=src pytest tests/ -q -rs` and with
  `TRUE_SOURCE_ROOT` pointing at production source.
- **Hash-pinned assets that hygiene tooling must not rewrite:**
  `src/evalgen/prompts/*.txt` (sha256 in `experiments/*.plan.json` and
  `prompts/manifest.json`), `src/evalgen/schemas/retention.json`,
  `tests/fixtures/testsets/*` (byte-exact, CR-refusing), `experiments/*.plan.json`
  (self-hashed), `asr-eval/` corpus and `manifest.json`, `docs/reports/*.json` (generated,
  read by `scripts/doc_claims.py`), `configs/*.json` (fingerprinted runtime manifests).
- **Prose that cites file:line into source:** 16 citations into `src/evalgen/cli.py`
  (five of them in `ci.yml` comments), 5 into `src/evalgen/experiments.py`, and hundreds
  into `production-reference/`. Nothing checks them mechanically.
- **Existing instructions:** `AGENTS.md`, `CLAUDE.md`, `TESTING.md`, `CONTRIBUTING.md`,
  `.github/PULL_REQUEST_TEMPLATE.md`. Canon paths cited are Linux paths that do not
  exist on this machine.
- **Uncommitted work:** none (`main` clean at `982237f`).

### Gap classification against the toolchain baseline (playbook step 2)

| Control (`toolchains/`) | Status | Evidence |
|---|---|---|
| `pyproject.toml` with `[tool.ruff]` | not met | file absent |
| `ruff check` in CI and pre-commit | not met | `ci.yml` header items 3; no hooks |
| `ruff format --check` in CI and pre-commit | not met | 137 files unformatted |
| `mypy .` exits 0 in CI | not met | 295 errors, no config |
| `pytest` in CI | met | `ci.yml` `test` job |
| Coverage floor | not met | no `pytest-cov`; playbook says set at current number — no number exists |
| `permissions: contents: read` | not met | absent from `ci.yml` |
| Actions pinned by SHA | not met | floating tags |
| Security scan (Trivy) | not met | absent; **out of this slice** |
| Eval assets valid on every PR | met | `evalgen.py check`, `run_index.py --check`, `experiment-check` |
| Commands recorded in `AGENTS.md` "Commands" | partial | commands are in `CLAUDE.md`/`TESTING.md`; no "Commands" section in `AGENTS.md` |
| Project risk tier recorded | unknown | no document states one |

## Proposed outcome

After this change, on every pull request and before every local commit, the same
three tools with the same configuration say whether the Python in this repository is
lint-clean, type-clean against a burn-down list, and still passes the existing suite:

- `ruff check .` exits 0, with every accepted exception written in `pyproject.toml`
  beside its reason;
- `mypy .` exits 0, with every currently-failing module named in `mypy.ini` so new type
  errors are blocked while the list shrinks;
- `PYTHONPATH=src pytest tests/ -q -rs` reports the same pass/skip counts as before in
  both modes;
- `pre-commit run --all-files` exits 0 and does not rewrite any hash-pinned asset;
- `AGENTS.md` and `TESTING.md` say how to run all of it, and what was deliberately
  left out.

## Users and systems affected

- Whoever commits to this repository next: a hook now runs before the commit and CI
  has two more required jobs.
- `src/`, `scripts/`, `tests/`, `asr-eval/` Python files receive mechanical import-order
  and unused-import fixes. `production-reference/` receives nothing.
- CI runtime grows by two parallel jobs.
- No scorer, fixture, prompt, plan, manifest, report or pin changes.

## Constraints

- **No `[project]` or `[build-system]` section.** Imports resolve through
  `PYTHONPATH=src` on purpose (`TESTING.md` §Quick Start, `ci.yml` header item 2);
  `toolchains/python.md` §Migrating step 1 says the same.
- **`requirements.txt` is not touched**, and no tool install may move a pin
  (`AGENTS.md` §Conventions).
- **`asr-eval/` keeps its own pins and venv.** Tool config must not pull its
  dependencies into the root environment.
- **No test is weakened, skipped or deleted** to reach green
  (`standards/software-engineering.md` §Verification; `CLAUDE.md` contract item 4).
- **Mechanical fixes commit separately from configuration**
  (`standards/git-and-review.md` §Traceable changes).
- **Hash-pinned assets and generated reports are byte-stable.** Any hygiene hook that
  could rewrite them must exclude them.
- Branch `chore/adopt-toolchain-baseline` off `main`; Conventional Commits; regular
  merge, never squash (`CONTRIBUTING.md`); draft PR only, no merge.

## Open questions

| Question | Blocks | Owner | Needed by |
|---|---|---|---|
| Line length: canon's 100 or the exemplar's 88? Code is written to ~100 (108 vs 4,362 `E501`). | Spec | author (decided in spec: 100) | Spec |
| Should `ruff format` be applied now (137 files) or deferred? | Spec | author (decided in spec: deferred, stated gap) | Spec |
| Who approves the plan? No second person is present in this trial. | Build | repository owner (@tkhongsap, CODEOWNERS) | Before merge |
| Where does a pip-based repository record dev-tool versions, given no `[dependency-groups]`? | Build | author (decided: `requirements-dev.txt`) | Build |
| Coverage floor: the playbook says "set at the current number" but the repository has never measured one. | Release | owner | Follow-up |

## Decision

**Accepted, 2026-09-11**, by the author of this trial, as the single representative
change through the full lifecycle required by `adopt-in-a-project.md` step 6. The
owner's acceptance is still required at the pull request.
