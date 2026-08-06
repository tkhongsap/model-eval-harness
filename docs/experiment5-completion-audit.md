# Experiment 5 completion audit

**Audit date:** 2026-08-06

**Scope:** the active enterprise Retention Phase 1/2 goal

**Overall status:** offline platform complete; paid evidence pending approval

This table is the requirement-by-requirement completion record. `Implemented` means the
current checkout and tests prove the capability exists. `Pending gate` means the code is
ready but the required external evidence cannot exist until the named approval permits
paid model calls. A pending gate is not reported as completion.

| Requirement | Authoritative evidence | Status |
|---|---|---|
| Isolated worktree from Retention v3 commit `a7aff2f` | branch `feat/enterprise-eval-phase1-2`; `git rev-parse HEAD` before implementation | Implemented |
| Team's active checkout remains independent | `git worktree list` shows separate original and enterprise paths/branches | Implemented |
| `evalgen` calls, `evalharness` only scores | AST boundary tests in `tests/test_boundary.py` | Implemented |
| Three dimensions retain three denominators | existing exact metric/differential tests; no denominator code changed | Implemented |
| Zero-usage failure cannot create tokenizer split | `RunResult.prompt_token_spread`; zero-usage regression test | Implemented |
| Repository HEAD no longer identifies scoring | classification, scoring-surface and common-workload content hashes in `manifest.py` | Implemented |
| Explicit reasoning-off request | request/client/runner wiring plus exact request and dry-run tests | Implemented |
| Provider and regime qualification | six-call qualification taxonomy and self-hashed artifact contract | Implemented |
| Morph/Alibaba handled without workload workaround | request/schema incompatibility classifications; no blacklist or one-message branch | Implemented |
| Production-like 27B may be unavailable | locked-plan `UNAVAILABLE` state requires evidence for every eligible provider | Implemented |
| Reasoning-enabled CoreWeave cannot replace explicit-off arm | runtime gate and documented diagnostic-only policy | Implemented |
| Versioned v3 dataset | `retention_v3.manifest.json`, SHA-pinned by the experiment plan | Implemented |
| Versioned prompt/config library | prompt manifest and executable-registry drift test | Implemented |
| Experiment 5 preregistered before calls | draft machine plan and `EXPERIMENTS.md`; no paid Experiment 5 run artifacts | Implemented |
| Full workload: 138 × 3 per arm, one attempt | locked runner derives 414 calls from the validated plan | Implemented; real outputs pending gate 2 |
| Parse-valid gate ≥99% (410/414) | unrounded reliability function and boundary tests | Implemented |
| Exact paired quality verdicts | observed-discordance exact bands at alpha 1/64 per side and calibration tests | Implemented |
| Paired replicate stability | item-level identical-payload flags and paired exact verdict | Implemented |
| Load: fixed 12 items × two at concurrency 1/4/8 | locked load runner, plan validation, report completeness refusal | Implemented; real outputs pending gate 2 |
| Quality-first operations ranking | candidate decision runs reliability/runtime/quality/stability before operations | Implemented |
| Machine and human per-model/pairwise reports | deterministic JSON/Markdown plus XLSX Overview/Quality/Regressions/Load | Implemented |
| Item-level regressions retained | HMAC-keyed rows in pairwise JSON, Markdown summary and XLSX | Implemented |
| Reproducible report generation | end-to-end fake test executes 1,458 full/load calls and compares two generated JSON/Markdown reports byte-for-byte | Implemented |
| Pack validation | `evalgen check` passes v1, v2 and v3 | Implemented |
| Full standalone verification | 493 passed / 33 expected skips in isolated checkout | Implemented |
| Production differential and pin verification | `TRUE_SOURCE_ROOT=production-reference/sentiment-batch-retention-main`; differential/requirements/boundary selection: 18 passed | Implemented |
| Qualification evidence from current providers | approval gate 1: ≤108 calls, current conservative ceiling $3.47 | Pending gate 1 |
| Full/load real model results and recommendation | approval gate 2 after qualification selection/lock | Pending gate 2 |
| Production/audio reconciliation | explicitly excluded from this phase; every report remains `RECONCILED: NO` | Intentionally unavailable |

## Approval boundary

No provider qualification or full/load model call is authorized by the implementation
itself. Gate 1 authorizes only current named six-call provider probes. Gate 2 is separate:
selected providers and qualification artifact hashes must be copied into the plan, its
status changed to `locked`, offline checks rerun, and the new plan SHA explicitly entered
on every paid command.
