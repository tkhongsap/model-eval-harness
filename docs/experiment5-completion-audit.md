# Experiment 5 completion audit

**Audit date:** 2026-08-06

**Scope:** the active enterprise Retention Phase 1/2 goal

**Overall status:** complete; both Qwen candidates failed; `RECONCILED: NO`

This table is the requirement-by-requirement completion record. `Implemented` means the
current checkout and tests prove the capability exists. `Complete` means the paid
evidence and deterministic reports now exist. Production/audio reconciliation remains
explicitly outside this phase and is not reported as completion.

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
| Morph/Alibaba handled without workload workaround | exact live probes qualified both with 6/6 object-root, zero-reasoning responses; no blacklist or one-message branch | Implemented |
| Production-like 27B may be unavailable | locked-plan `UNAVAILABLE` state requires evidence for every eligible provider | Implemented |
| Reasoning-enabled CoreWeave cannot replace explicit-off arm | runtime gate and documented diagnostic-only policy | Implemented |
| Versioned v3 dataset | `retention_v3.manifest.json`, SHA-pinned by the experiment plan | Implemented |
| Versioned prompt/config library | prompt manifest and executable-registry drift test | Implemented |
| Experiment 5 preregistered before calls | commit `d96845d`, draft SHA `49ae4874…`, and pre-call dry-run audit precede Gate 1 artifacts | Implemented |
| Full workload: 138 × 3 per arm, one attempt | execution ledger: three 414-call full runs | Complete |
| Parse-valid gate ≥99% (410/414) | unrounded reliability function and boundary tests | Implemented |
| Exact paired quality verdicts | observed-discordance exact bands at alpha 1/64 per side and calibration tests | Implemented |
| Paired replicate stability | item-level identical-payload flags and paired exact verdict | Implemented |
| Load: fixed 12 items × two at concurrency 1/4/8 | execution ledger: nine 24-call load runs | Complete |
| Quality-first operations ranking | candidate decision runs reliability/runtime/quality/stability before operations | Implemented |
| Machine and human per-model/pairwise reports | deterministic JSON/Markdown plus XLSX Overview/Quality/Regressions/Load | Implemented |
| Item-level regressions retained | HMAC-keyed rows in pairwise JSON, Markdown summary and XLSX | Implemented |
| Reproducible report generation | end-to-end fake test executes 1,458 full/load calls and compares two generated JSON/Markdown reports byte-for-byte | Implemented |
| Pack validation | `evalgen check` passes v1, v2 and v3 | Implemented |
| Full standalone verification | 498 passed / 33 expected skips in isolated checkout | Implemented |
| Production differential and pin verification | `TRUE_SOURCE_ROOT=production-reference/sentiment-batch-retention-main`; differential/requirements/boundary selection: 18 passed | Implemented |
| Qualification evidence from current providers | 18 committed self-hashed artifacts; 108 calls; 12 qualified / 6 request incompatible; US$0.109184588 reported cost | Implemented |
| Full/load real model results and recommendation | `docs/experiment5-results.md`; committed report set and execution ledger | Complete: both candidates `FAIL` |
| Production/audio reconciliation | explicitly excluded from this phase; every report remains `RECONCILED: NO` | Intentionally unavailable |

## Approval and evidence boundary

Gate 1 and Gate 2 are complete. Gate 2 approval authorized the immutable locked plan SHA,
exactly 1,458 calls and a US$50.13 ceiling. The separate self-hashed approval preserves
that plan SHA instead of editing the preregistration after approval. Execution used all
1,458 calls, one attempt each, for a US$1.507460937 reported-cost lower bound.

The safe report set and its hashes are committed. Raw model response logs remain
gitignored because they contain model text and provider account metadata; their hashes
are recorded in `experiments/evidence/retention-e5/execution.json`. This is enough to
identify the local raw evidence without publishing it.
