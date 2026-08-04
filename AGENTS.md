# Project Context for AI Agents

## Mission

> Score model outputs against human labels so True can decide, on evidence, whether a
> self-hosted model is good enough to replace Gemini in four production call-centre
> applications. It does not run models: the app team runs both arms, this scores what
> they produce.

## Architecture

- **Language**: Python 3.12, standard library only for the scoring path
- **Backend**: none. This is a library plus a test suite, not a service. No HTTP, no
  database, no deployment target.
- **AI/ML**: **none, deliberately.** See "Service layers do not apply" below.
- **Auth**: none. The one secret is `EVAL_HARNESS_KEY_HMAC`, which salts item keys and
  is held by True's team, never by this repository.
- **External integrations**: none at runtime. The test suite optionally reaches True's
  production source tree via `TRUE_SOURCE_ROOT` to run a differential check.
- **Infrastructure**: a venv pinned to production's own dependency versions.

## Key Files

- `src/evalharness/metrics.py`: the three scorers. **Three dimensions, three
  denominators** is the most important fact in the repository.
- `src/evalharness/records.py`: the normalized record and every normalisation rule,
  each citing the production line it mirrors.
- `src/evalharness/labelspaces.py`: class lists transcribed verbatim from production.
- `src/evalharness/compare.py`: paired comparison, 2x2 disagreement table, coverage refusal.
- `src/evalharness/keys.py`: HMAC item keys and the guard that refuses customer columns.
- `src/evalharness/manifest.py`: blocking versus recorded fields, and the `RECONCILED` stamp.
- `src/evalharness/paths.py`: runtime refusal of any data directory inside a git worktree.
- `src/evalharness/adapters/retention.py`: **the only file that changes when real data arrives.**
- `tests/fixtures/WORKED-COMPUTATION.md`: the hand arithmetic every expectation comes from.
- `tests/production_ref.py`: test-only scaffolding to import True's real scorer.

## Conventions

- **Code style**: PEP 8. No formatter or linter is configured yet (see Open items).
- **Testing**: `pytest`. See [TESTING.md](./TESTING.md).
- **Commits**: Conventional Commits (`feat:`, `fix:`, `docs:`, `test:`, `chore:`).
- **Dependency pins are load-bearing, not cosmetic.** Under pandas 3.x the production
  scorer's `dtype == 'object'` guard is False, string normalisation never fires, and
  `call_result` accuracy collapses from 0.75 to 0.25. Never relax the pins to make an
  install succeed.
- **Never edit an expectation to make a test pass.** `tests/fixtures/retention_expected.csv`
  was hand-computed before the code existed. If a test fails, the first question is
  whether the fixture is wrong, not the code. Changing an expectation to match output
  destroys the only thing that makes the numbers credible.

## Service layers do not apply, and here is why

Canon mandates OpenRouter for AI routing, WorkOS for identity, and Composio for tool
execution. **None applies here, and their absence is not an oversight.**

| Layer | Status | Reason |
|---|---|---|
| OpenRouter | not used | This package makes **no model calls at all**. It scores outputs produced elsewhere. `google.genai` appears only as a stub in test scaffolding, never as a dependency. |
| WorkOS | not used | No users, no sessions, no HTTP surface. |
| Composio | not used | No external API execution. |

If a future version runs candidate models directly, OpenRouter becomes mandatory at
that point. Today it would be a dependency with nothing to route.

## Cross-Project References

> These live in canon and apply to all projects. Linked, never restated here.

| Resource | Location | Use When |
|----------|----------|----------|
| **Project structure and repo policy** | `canon/guides/project-structure.md` | Repo location, visibility, layout |
| **Git workflow** | `canon/guides/git-workflow.md` | Branching, commits, PRs |
| **Production patterns** | `canon/guides/production-patterns.md` | If this ever grows a service |
| **Release versioning** | `canon/guides/release-versioning.md` | Tagging a version |
| **Development process** | `canon/development/development-process.md` | Session workflow |
| **Infrastructure service layers** | `canon/guides/infrastructure-service-layers.md` | Before adding any AI, auth or integration |

## Review Guidelines

- Automated PR review uses CodeRabbit plus Codex where enabled; humans remain merge authority.
- Treat as high-risk in this repository specifically: anything touching
  `keys.py`, `paths.py`, or `.gitignore`, because those three are what keep customer
  identifiers out of git; and any change to `metrics.py` that alters a denominator.
- Use `canon/operations/ai-pr-review-loop.md` for agent-facing policy.

## Open items

**Repository location: a deliberate, owner-approved departure from canon.**

`canon/guides/project-structure.md` states repos MUST be under the `ObjectiveFunction`
org, MUST be private, and must never sit on a personal account. This repository is
**private** (satisfied) but lives at `tkhongsap/model-eval-harness` (a departure).

**Decision, 2026-08-04, by the owner: stay on the private personal account.** The
alternative that canon prescribes, `ObjectiveFunction`, is the studio org, so moving
there would relocate True Corp work product into a studio namespace: a different
governance question rather than a fix. Moving to True's own GitHub Enterprise is
plausibly the right long-term home and remains blocked on the licence and org
questions tracked separately.

Recorded rather than left implicit so a later reader can tell this was weighed and
settled, not overlooked. Two conditions that make it defensible, and that should be
re-checked if either changes:

- The repository **must stay private**. It cites internal SharePoint site names,
  production file paths and line numbers.
- It contains **no customer data**, and the controls in `keys.py`, `paths.py` and
  `.gitignore` exist to keep it that way. Fixture phone numbers are the synthetic
  `08100000xx` range.

Revisit if the True GitHub Enterprise org becomes available, or if anyone outside
True needs access.

**No linter, type checker or `pyproject.toml`.** Canon's Python CI template expects
ruff, mypy and a `pyproject.toml` under `backend/`. This repository has none. CI runs
the test suite only; see the comment block in `.github/workflows/ci.yml`.

## Project-Specific Notes

**No number this harness produces is a migration verdict yet.** Every report prints
`RECONCILED: NO` until a run has been checked against the app's own live Gemini
fact-check report, which needs real data this repository does not have. The stamp
exists so the harness cannot launder its own provenance under schedule pressure. Do
not remove it to make a report look finished.

**The workbook adapter is a known unknown.** `load_workbook()` raises
`NotImplementedError` on purpose: production reconstructs column names from a two-row
header block nobody here has seen, and a loader written against a guess would appear
to work while silently mis-mapping columns. Fix it when the header rows arrive, not before.
