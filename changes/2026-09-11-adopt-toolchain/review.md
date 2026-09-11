# Review: chore/adopt-toolchain-baseline (author's self-review)

- **Reviewer:** the author (same agent session that wrote the plan and the code). This
  is a self-review, recorded because `standards/git-and-review.md` says an unrecorded
  pass is indistinguishable from a skip. It is not the owner review the standard
  requires for a control change; that happens on the pull request.
- **Date:** 2026-09-11
- **Risk tier:** R1 (spec)
- **Spec / plan:** [`spec.md`](spec.md), [`plan.md`](plan.md)
- **Commits reviewed:** `fb5defa` (chain), `089393d` (config), `bee680b` (mechanical
  fixes), `f407bf9` (one hygiene fix), `efa5a3a` (pre-commit), `c6638bf` (CI),
  `c7c7b8f` (docs); the closing commit adds this file and the plan's Deviations.

AI review is an input, not merge approval or risk acceptance. Rules applied:
the playbook's `standards/git-and-review.md` (not vendored into this repository).

## Passes

| Pass | Looking for | Skipped? |
|---|---|---|
| Bugs | Logic errors, broken edge cases, regressions, contract violations | No. Read the full `bee680b` diff line by line; ran the suite in both modes on the tip. |
| Security | Injection, authorization gaps, secret and PII exposure, unsafe side effects | No. The change adds CI jobs, a workflow token scope, and hooks that download and run third-party code; that is a security surface. |
| Compliance | Does it match the spec, the plan, and the standards it claims to follow | No. Checked every "must do" in the spec and every row of the plan's file list against the diff. |

## Findings

| ID | Pass | Severity | File:line | Finding | Resolution |
|---|---|---|---|---|---|
| R-001 | Bugs | Minor | `src/evalgen/artifacts.py:128` | `SyntaxWarning: invalid escape sequence '\.'` prints on every test run. ruff's `W605` would catch it, but the playbook baseline selects `E, F, I` without `W` (canon's baseline has `W`). Not fixed here: a string-literal edit in `artifacts.py` is out of a config-only change. | Deferred. Follow-up: add `W` to `select` and fix the one finding. |
| R-002 | Bugs | Material | `.pre-commit-config.yaml:82` (`types: [python]`) | The mypy hook, copied from the exemplar, only runs when the commit touches a `.py` file. A `mypy.ini`-only edit (say, deleting a burn-down entry without fixing the module) is not type-checked before commit. Observed on three commits in this branch: "mypy … (no files to check) Skipped". CI still runs it. | Accepted with follow-up: add `mypy.ini` to the hook's `files`/`types_or` so config edits trigger it. Owner: repository owner. |
| R-003 | Bugs | Note | `scripts/provider_probe.py:31`, `tests/test_outcomes.py:29-33` | `ruff --fix` produced one new `E402` and relocated an explanatory comment. Both hand-corrected in `bee680b`; the corrections are stated in its message. | Fixed. |
| R-004 | Bugs | Note | `mypy.ini` | `warn_unused_configs = True` would warn if any of the 61 burn-down sections matched no module. The tip run printed none, so every section names a module mypy actually checked. | No action. |
| R-005 | Security | Minor | `.github/workflows/ci.yml:42-47` | The existing `test` job still uses floating `actions/checkout@v4` and `setup-python@v5`, which `toolchains/ci.md` says "can be moved under you". Left untouched on purpose (constraint: preserve every existing step); the two new jobs are SHA-pinned. Inconsistent within one file. | Accepted with follow-up recorded in `AGENTS.md` "Commands". |
| R-006 | Security | Note | `.pre-commit-config.yaml:42,57` | Hook repositories are pinned by tag (`v6.0.0`, `v0.16.7`), as the playbook block does, not by SHA as the same playbook demands for Actions. Tags are mutable in principle. pre-commit caches the resolved commit locally, so a moved tag affects fresh clones only. | Accepted; the playbook's own two documents disagree with each other here (see report). |
| R-007 | Security | Note | `requirements-dev.txt` | Installing `pre-commit` pulls `virtualenv`, `PyYAML`, `identify`, `nodeenv`, `cfgv` into the pinned venv. None touches pandas/numpy/openpyxl; `tests/test_requirements.py` passed after install and on the tip (`4 passed, 3 skipped`; the skips are the production cross-check, which needs `TRUE_SOURCE_ROOT`). | No action. |
| R-008 | Security | Note | `.github/workflows/ci.yml:35-39` | `permissions: contents: read` now applies to all three jobs. The `test` job never wrote anything, so this narrows without breaking. | No action. |
| R-009 | Compliance | Material | `.github/workflows/ci.yml`, `.pre-commit-config.yaml` | `toolchains/ci.md` and `pre-commit.md` both require `ruff format --check`. It is not added: 137 files would change and 21+ prose citations would go stale. `ci.md` line 8 allows a stated gap; it is stated in `ci.yml` header item 3, `AGENTS.md` "Commands" and `TESTING.md` "Required checks". | Accepted as a stated gap; follow-up with owner. |
| R-010 | Compliance | Material | `pyproject.toml` (absent `--cov`) | No coverage floor, which `python.md` lists as part of the baseline ("set it at the current number"). No current number exists. | Deferred; owner decision, recorded in `AGENTS.md`. |
| R-011 | Compliance | Minor | `pyproject.toml:52-55` | `[tool.pytest.ini_options]` is present because `python.md` step 1 names it, but it holds only `testpaths`. It changes nothing observable (rootdir was already the repository root; counts identical). Kept so the section exists for the next person; arguably an unnecessary control for this repository. | Accepted; reported to the playbook. |
| R-012 | Compliance | Minor | `pyproject.toml:24-31` | `E501` and `E741` are ignored globally rather than fixed. `python.md` permits this for a migrated repository "with a reason each, and removes them over time". Reasons are beside each entry; no removal date. | Accepted; burn-down has no owner or date, which the playbook does not require but should. |
| R-013 | Compliance | Note | `.github/PULL_REQUEST_TEMPLATE.md` "Canon exceptions" | The project's PR template asks for canon paths under `/home/tkhongsap/my-github/s42/canon`, which does not exist on this machine. The playbook says canon prescribes the baseline but does not vendor it. The PR states what could not be checked. | No action possible here. |
| R-014 | Compliance | Note | `changes/2026-09-11-adopt-toolchain/plan.md` "Approved by" | Self-approved. The standard requires human review from an accountable owner for a control change; this branch is a draft PR until that happens. | Deferred to the PR. |

## Outcome

**Author's verdict: ready for owner review as a draft; not approved for merge.** The
human who makes the call is @tkhongsap (CODEOWNERS for `/.github/`, `/requirements.txt`
and the root). Material findings R-002, R-009 and R-010 each have a stated follow-up;
none is hidden.

Checks that ran, on the tip, in the pinned venv (Python 3.12.14):

- `ruff check .` — `All checks passed!`
- `python -m mypy .` — `Success: no issues found in 132 source files`
- `PYTHONPATH=src pytest tests/ -q -rs` — `1071 passed, 50 skipped`
- `… TRUE_SOURCE_ROOT=production-reference/sentiment-batch-retention-main …` — `1082 passed, 39 skipped`
- `scripts/evalgen.py experiment-check --plan experiments/retention-e7.plan.json` — `OK`
- `pre-commit run --all-files` — every hook `Passed`, exit 0
- `git diff --check main...HEAD` — clean

Checks that could not run, and why:

- **CI itself.** The `lint` and `typecheck` jobs have not executed on GitHub until the
  draft PR opens them; what is recorded here is the local equivalent with the same
  commands and config.
- **`scripts/verify.py` full gate.** Needs `.venv-asr`, absent in this checkout;
  `tests/test_verify.py:388` skipped for that reason in both modes. Not part of this
  change's surface, but it is the repository's full gate and it did not run.
- **Canon cross-references.** `/home/tkhongsap/my-github/s42/canon` is not on this
  machine; every "canon prescribes" claim in the playbook was taken on trust.
- **An independent reviewer.** None was available; this file is the author's passes.
